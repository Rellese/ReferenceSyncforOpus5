from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


PROJECT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT / "config" / "local.json"
REPORTS_PATH = PROJECT / "reports"

MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}
INSTAGRAM_URL_RE = re.compile(
    r"instagram\.com/(?:p|reel|tv)/([^/?#]+)/?",
    re.IGNORECASE,
)


def load_config() -> dict[str, Any]:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    for key, value in list(payload.items()):
        if isinstance(value, str):
            payload[key] = value.replace("$PROJECT", str(PROJECT))

    return payload


def latest_count_issues_report() -> Path:
    candidates = sorted(
        REPORTS_PATH.glob("eagle_instagram_count_issues_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not candidates:
        raise FileNotFoundError(
            "No eagle_instagram_count_issues_*.json report found"
        )

    return candidates[0]


def extract_shortcode(url: Any) -> str | None:
    if not isinstance(url, str):
        return None

    match = INSTAGRAM_URL_RE.search(url)
    return match.group(1) if match else None


def normalize_ext(value: Any) -> str:
    if not isinstance(value, str):
        return ""

    return value.lower().strip().lstrip(".")


def normalize_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""

    return value.strip().lower()


def fetch_instagram_items(
    api_url: str,
    tag: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    limit = 1000
    offset = 0
    items: list[dict[str, Any]] = []
    requests_made = 0
    api_total = None

    with httpx.Client(timeout=60.0) as client:
        while True:
            response = client.post(
                f"{api_url.rstrip('/')}/api/v2/item/get",
                json={
                    "tags": [tag],
                    "fields": [
                        "id",
                        "name",
                        "ext",
                        "size",
                        "tags",
                        "folders",
                        "isDeleted",
                        "url",
                        "annotation",
                        "modificationTime",
                    ],
                    "offset": offset,
                    "limit": limit,
                },
            )
            response.raise_for_status()

            payload = response.json()
            requests_made += 1

            if payload.get("status") != "success":
                raise RuntimeError(f"Eagle API error: {payload}")

            envelope = payload.get("data", {})

            if isinstance(envelope, dict):
                page = envelope.get("data", [])
                api_total = envelope.get("total", api_total)
            elif isinstance(envelope, list):
                page = envelope
            else:
                raise RuntimeError("Unexpected Eagle item/get response")

            if not isinstance(page, list):
                raise RuntimeError("Unexpected Eagle page data")

            items.extend(page)
            offset += len(page)

            if not page:
                break

            if api_total is not None and offset >= int(api_total):
                break

            if len(page) < limit:
                break

    return items, {
        "endpoint": "/api/v2/item/get",
        "requests_made": requests_made,
        "page_size": limit,
        "api_total": api_total,
        "items_received": len(items),
    }


def filename_media_id(media_path: Path) -> str | None:
    name = media_path.name

    match = re.match(
        r"^\d+_(\d+)\.(?:jpg|jpeg|png|webp|mp4|mov)$",
        name,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)

    match = re.match(
        r"^(\d+)\.(?:jpg|jpeg|png|webp|mp4|mov)$",
        name,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)

    return None


def read_local_components(
    source_path: Path,
    relevant_post_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for media_path in sorted(source_path.rglob("*")):
        if not media_path.is_file():
            continue

        if media_path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue

        if ".fdash-" in media_path.name.lower():
            continue

        sidecar_path = Path(f"{media_path}.json")
        if not sidecar_path.is_file():
            continue

        try:
            metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        post_id = str(metadata.get("post_id") or "").strip()
        if post_id not in relevant_post_ids:
            continue

        media_id = str(
            metadata.get("media_id")
            or filename_media_id(media_path)
            or ""
        ).strip()

        raw_num = metadata.get("num")
        try:
            component_index = int(raw_num)
        except (TypeError, ValueError):
            component_index = None

        result[post_id].append(
            {
                "post_id": post_id,
                "media_id": media_id or None,
                "component_index": component_index,
                "filename": media_path.name,
                "stem": media_path.stem,
                "extension": normalize_ext(media_path.suffix),
                "size": media_path.stat().st_size,
                "local_path": str(media_path),
                "sidecar_path": str(sidecar_path),
                "exists": True,
            }
        )

    for components in result.values():
        components.sort(
            key=lambda item: (
                item["component_index"] is None,
                item["component_index"] or 999999,
                item["filename"],
            )
        )

    return result


def exact_identity_match(
    local_item: dict[str, Any],
    eagle_item: dict[str, Any],
) -> bool:
    if local_item["extension"] != normalize_ext(eagle_item.get("ext")):
        return False

    eagle_name = normalize_name(eagle_item.get("name"))
    local_filename = normalize_name(local_item.get("filename"))
    local_stem = normalize_name(local_item.get("stem"))
    media_id = str(local_item.get("media_id") or "").lower()

    eagle_stem = Path(eagle_name).stem if eagle_name else ""

    if eagle_name == local_filename:
        return True

    if eagle_name == local_stem:
        return True

    if eagle_stem == local_stem:
        return True

    if media_id and media_id in eagle_name:
        return True

    return False


def match_post(
    local_items: list[dict[str, Any]],
    eagle_items: list[dict[str, Any]],
) -> dict[str, Any]:
    unmatched_local = set(range(len(local_items)))
    unmatched_eagle = set(range(len(eagle_items)))
    matches: list[dict[str, Any]] = []

    # Pass 1: unique filename/media_id matches.
    changed = True
    while changed:
        changed = False

        for local_index in list(unmatched_local):
            candidates = [
                eagle_index
                for eagle_index in unmatched_eagle
                if exact_identity_match(
                    local_items[local_index],
                    eagle_items[eagle_index],
                )
            ]

            if len(candidates) == 1:
                eagle_index = candidates[0]

                matches.append(
                    {
                        "local_index": local_index,
                        "eagle_index": eagle_index,
                        "method": "NAME_OR_MEDIA_ID",
                    }
                )

                unmatched_local.remove(local_index)
                unmatched_eagle.remove(eagle_index)
                changed = True

    # Pass 2: unique exact size + extension matches.
    changed = True
    while changed:
        changed = False

        for local_index in list(unmatched_local):
            local_item = local_items[local_index]

            candidates = [
                eagle_index
                for eagle_index in unmatched_eagle
                if (
                    local_item["extension"]
                    == normalize_ext(eagle_items[eagle_index].get("ext"))
                    and local_item["size"]
                    == eagle_items[eagle_index].get("size")
                )
            ]

            if len(candidates) != 1:
                continue

            eagle_index = candidates[0]

            reverse_candidates = [
                other_local_index
                for other_local_index in unmatched_local
                if (
                    local_items[other_local_index]["extension"]
                    == normalize_ext(eagle_items[eagle_index].get("ext"))
                    and local_items[other_local_index]["size"]
                    == eagle_items[eagle_index].get("size")
                )
            ]

            if len(reverse_candidates) != 1:
                continue

            matches.append(
                {
                    "local_index": local_index,
                    "eagle_index": eagle_index,
                    "method": "EXACT_SIZE_AND_EXTENSION",
                }
            )

            unmatched_local.remove(local_index)
            unmatched_eagle.remove(eagle_index)
            changed = True

    matched_local_by_index = {
        match["local_index"]: match for match in matches
    }

    local_output = []

    for local_index, local_item in enumerate(local_items):
        row = dict(local_item)
        match = matched_local_by_index.get(local_index)

        if match is None:
            row["match_status"] = "NOT_FOUND_IN_EAGLE"
            row["matched_eagle_id"] = None
            row["match_method"] = None
        else:
            eagle_item = eagle_items[match["eagle_index"]]
            row["match_status"] = "MATCHED"
            row["matched_eagle_id"] = eagle_item.get("id")
            row["match_method"] = match["method"]

        local_output.append(row)

    eagle_output = []

    for eagle_index, eagle_item in enumerate(eagle_items):
        eagle_output.append(
            {
                "id": eagle_item.get("id"),
                "name": eagle_item.get("name"),
                "extension": normalize_ext(eagle_item.get("ext")),
                "size": eagle_item.get("size"),
                "url": eagle_item.get("url"),
                "matched": eagle_index not in unmatched_eagle,
            }
        )

    expected_missing_count = len(local_items) - len(eagle_items)
    detected_missing_count = len(unmatched_local)

    exact_result = (
        len(unmatched_eagle) == 0
        and detected_missing_count == expected_missing_count
    )

    return {
        "expected_local_count": len(local_items),
        "eagle_count": len(eagle_items),
        "expected_missing_count": expected_missing_count,
        "matched_count": len(matches),
        "detected_missing_count": detected_missing_count,
        "unmatched_eagle_count": len(unmatched_eagle),
        "exact_missing_files_resolved": exact_result,
        "local_components": local_output,
        "eagle_items": eagle_output,
    }


def main() -> None:
    config = load_config()

    source_path = Path(config["existing_instagram_source"]).expanduser()
    api_url = str(config.get("eagle_api_url", "http://localhost:41595"))
    tag = str(config.get("instagram_tag", "Instagram"))

    issues_report = latest_count_issues_report()
    issues = json.loads(issues_report.read_text(encoding="utf-8"))

    relevant_post_ids = {
        str(item["post_id"])
        for item in issues
    }

    local_by_post = read_local_components(
        source_path,
        relevant_post_ids,
    )

    eagle_items, api_info = fetch_instagram_items(api_url, tag)

    eagle_by_shortcode: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for item in eagle_items:
        if item.get("isDeleted"):
            continue

        shortcode = extract_shortcode(item.get("url"))
        if shortcode:
            eagle_by_shortcode[shortcode].append(item)

    posts_output = []
    confirmed_missing_files = []
    unresolved_posts = []

    for issue in issues:
        post_id = str(issue["post_id"])
        shortcode = str(issue["shortcode"])

        local_items = local_by_post.get(post_id, [])
        post_eagle_items = eagle_by_shortcode.get(shortcode, [])

        matching = match_post(local_items, post_eagle_items)

        post_result = {
            "post_id": post_id,
            "shortcode": shortcode,
            "canonical_url": issue.get("canonical_url"),
            "comparison_status": issue.get("comparison_status"),
            **matching,
        }

        posts_output.append(post_result)

        if matching["exact_missing_files_resolved"]:
            for component in matching["local_components"]:
                if component["match_status"] == "NOT_FOUND_IN_EAGLE":
                    confirmed_missing_files.append(
                        {
                            "post_id": post_id,
                            "shortcode": shortcode,
                            "canonical_url": issue.get("canonical_url"),
                            "media_id": component.get("media_id"),
                            "component_index": component.get(
                                "component_index"
                            ),
                            "filename": component["filename"],
                            "extension": component["extension"],
                            "size": component["size"],
                            "local_path": component["local_path"],
                        }
                    )
        else:
            unresolved_posts.append(
                {
                    "post_id": post_id,
                    "shortcode": shortcode,
                    "expected_local_count": len(local_items),
                    "eagle_count": len(post_eagle_items),
                    "matched_count": matching["matched_count"],
                    "detected_missing_count": matching[
                        "detected_missing_count"
                    ],
                    "unmatched_eagle_count": matching[
                        "unmatched_eagle_count"
                    ],
                }
            )

    extension_counts: dict[str, int] = defaultdict(int)

    for item in confirmed_missing_files:
        extension_counts[item["extension"]] += 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = (
        REPORTS_PATH
        / f"eagle_instagram_gap_diagnose_{timestamp}.json"
    )
    missing_path = (
        REPORTS_PATH
        / f"eagle_instagram_confirmed_missing_{timestamp}.json"
    )
    unresolved_path = (
        REPORTS_PATH
        / f"eagle_instagram_unresolved_gaps_{timestamp}.json"
    )

    summary = {
        "problem_posts": len(issues),
        "expected_missing_files_from_count_audit": sum(
            max(0, -int(item.get("difference", 0)))
            for item in issues
        ),
        "posts_resolved_exactly": (
            len(issues) - len(unresolved_posts)
        ),
        "posts_unresolved": len(unresolved_posts),
        "confirmed_missing_files": len(confirmed_missing_files),
        "confirmed_missing_extension_counts": dict(
            sorted(extension_counts.items())
        ),
        "eagle_instagram_items_received": len(eagle_items),
        "api": api_info,
    }

    safety = {
        "source_modified": False,
        "eagle_library_modified": False,
        "database_modified": False,
        "eagle_items_created": 0,
        "eagle_items_updated": 0,
        "eagle_items_deleted": 0,
        "physical_files_deleted": 0,
        "physical_files_moved": 0,
    }

    full_report = {
        "generated_at": datetime.now().isoformat(),
        "source_issues_report": str(issues_report),
        "summary": summary,
        "posts": posts_output,
        "unresolved_posts": unresolved_posts,
        "confirmed_missing_files": confirmed_missing_files,
        "safety": safety,
    }

    REPORTS_PATH.mkdir(parents=True, exist_ok=True)

    report_path.write_text(
        json.dumps(full_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    missing_path.write_text(
        json.dumps(
            confirmed_missing_files,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    unresolved_path.write_text(
        json.dumps(
            unresolved_posts,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "summary": summary,
                "unresolved_posts": unresolved_posts,
                "outputs": {
                    "report": str(report_path),
                    "confirmed_missing": str(missing_path),
                    "unresolved": str(unresolved_path),
                },
                "safety": safety,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
