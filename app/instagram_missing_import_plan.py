from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT / "reports"
SOURCE = Path(
    "/Users/mbp16/Downloads/InstagramToEagle/instagram_downloaded"
)

INSTAGRAM_FOLDER_ID = "MRWRIOJO42ER5"
INSTAGRAM_TAG = "Instagram"

INFERRED_MISSING_FILENAMES = [
    # C7hglbYgx-Y — components 8, 9, 10
    "3378124505015852952_3378124490654593306.jpg",
    "3378124505015852952_3378124490394543058.jpg",
    "3378124505015852952_3378124490394475884.jpg",

    # DEPPdT-tq4z — component 17
    "3535112217524874803_3535112203557816083.jpg",

    # DKe1o--N3YH — components 3 and 7
    "3647588661076653575_3647588647512056244.jpg",
    "3647588661076653575_3647588647696836087.jpg",

    # DXwug_DDA4B — component 6
    "3886811055280360961_3886811030726956949.jpg",
]


def latest_confirmed_report() -> Path:
    candidates = sorted(
        REPORTS.glob("eagle_instagram_confirmed_missing_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not candidates:
        raise FileNotFoundError(
            "No eagle_instagram_confirmed_missing report found"
        )

    return candidates[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def first_value(metadata: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = metadata.get(key)

        if value not in (None, "", [], {}):
            return value

    return None


def load_sidecar(media_path: Path) -> tuple[Path, dict[str, Any]]:
    sidecar_path = Path(f"{media_path}.json")

    if not sidecar_path.is_file():
        raise FileNotFoundError(
            f"Missing sidecar for import candidate: {media_path}"
        )

    metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))

    if not isinstance(metadata, dict):
        raise ValueError(f"Invalid sidecar object: {sidecar_path}")

    return sidecar_path, metadata


def normalize_existing_item(row: dict[str, Any]) -> dict[str, Any]:
    local_path = Path(str(row["local_path"])).expanduser()

    return {
        "local_path": str(local_path),
        "identity_resolution": "EXACT_LOCAL_EAGLE_MATCH_DIFFERENCE",
        "audit_post_id": str(row.get("post_id") or ""),
        "audit_shortcode": row.get("shortcode"),
        "audit_component_index": row.get("component_index"),
        "audit_media_id": row.get("media_id"),
    }


def inferred_item(filename: str) -> dict[str, Any]:
    local_path = SOURCE / filename

    return {
        "local_path": str(local_path),
        "identity_resolution": "IDENTICAL_CONTENT_IDENTITY_INFERRED",
        "audit_post_id": filename.split("_", 1)[0],
        "audit_shortcode": None,
        "audit_component_index": None,
        "audit_media_id": None,
    }


def build_item(candidate: dict[str, Any]) -> dict[str, Any]:
    media_path = Path(candidate["local_path"])

    if not media_path.is_file():
        raise FileNotFoundError(media_path)

    if ".fdash-" in media_path.name.lower():
        raise RuntimeError(
            f"DASH auxiliary file cannot enter import plan: {media_path}"
        )

    sidecar_path, metadata = load_sidecar(media_path)

    post_id = str(
        metadata.get("post_id")
        or candidate.get("audit_post_id")
        or ""
    ).strip()

    media_id = str(
        metadata.get("media_id")
        or candidate.get("audit_media_id")
        or ""
    ).strip()

    shortcode = str(
        metadata.get("post_shortcode")
        or candidate.get("audit_shortcode")
        or ""
    ).strip()

    post_url = first_value(
        metadata,
        "post_url",
        "url",
    )

    username = first_value(
        metadata,
        "username",
        "owner_username",
        "account",
        "user",
    )

    description = first_value(
        metadata,
        "description",
        "caption",
        "text",
    )

    raw_component_index = (
        metadata.get("num")
        if metadata.get("num") is not None
        else candidate.get("audit_component_index")
    )

    try:
        component_index = int(raw_component_index)
    except (TypeError, ValueError):
        component_index = None

    proposed_name = (
        f"@{str(username).lstrip('@')}"
        if username
        else media_path.stem
    )

    return {
        "post_id": post_id,
        "post_shortcode": shortcode or None,
        "post_url": post_url,
        "media_id": media_id or None,
        "component_index": component_index,
        "filename": media_path.name,
        "extension": media_path.suffix.lower().lstrip("."),
        "size": media_path.stat().st_size,
        "sha256": sha256_file(media_path),
        "local_path": str(media_path),
        "sidecar_path": str(sidecar_path),
        "identity_resolution": candidate["identity_resolution"],

        "proposed_eagle_item": {
            "name": proposed_name,
            "tags": [INSTAGRAM_TAG],
            "folders": [INSTAGRAM_FOLDER_ID],
            "url": post_url,
            "annotation": description or "",
            "path": str(media_path),
        },

        "validation": {
            "media_exists": True,
            "sidecar_exists": True,
            "post_id_present": bool(post_id),
            "media_id_present": bool(media_id),
            "post_url_present": bool(post_url),
            "eligible_for_import": bool(
                post_id
                and media_id
                and post_url
                and media_path.is_file()
                and sidecar_path.is_file()
            ),
        },
    }


def main() -> None:
    confirmed_report = latest_confirmed_report()
    confirmed = json.loads(
        confirmed_report.read_text(encoding="utf-8")
    )

    candidates = [
        normalize_existing_item(row)
        for row in confirmed
    ]

    candidates.extend(
        inferred_item(filename)
        for filename in INFERRED_MISSING_FILENAMES
    )

    unique_candidates: dict[str, dict[str, Any]] = {}

    for candidate in candidates:
        path = str(Path(candidate["local_path"]).resolve())
        unique_candidates[path] = candidate

    items = [
        build_item(candidate)
        for candidate in unique_candidates.values()
    ]

    items.sort(
        key=lambda item: (
            item["post_id"],
            item["component_index"] is None,
            item["component_index"] or 999999,
            item["filename"],
        )
    )

    extension_counts = Counter(
        item["extension"]
        for item in items
    )

    resolution_counts = Counter(
        item["identity_resolution"]
        for item in items
    )

    post_ids = {
        item["post_id"]
        for item in items
    }

    ineligible = [
        {
            "filename": item["filename"],
            "validation": item["validation"],
        }
        for item in items
        if not item["validation"]["eligible_for_import"]
    ]

    summary = {
        "dry_run": True,
        "source_confirmed_report": str(confirmed_report),
        "missing_items_planned": len(items),
        "affected_logical_posts": len(post_ids),
        "extension_counts": dict(sorted(extension_counts.items())),
        "identity_resolution_counts": dict(
            sorted(resolution_counts.items())
        ),
        "eligible_for_import": (
            len(items) - len(ineligible)
        ),
        "ineligible_for_import": len(ineligible),
        "target_tag": INSTAGRAM_TAG,
        "target_folder_id": INSTAGRAM_FOLDER_ID,
        "eagle_write_requests_made": 0,
    }

    expected = {
        "missing_items": 34,
        "affected_posts": 26,
        "jpg": 32,
        "mp4": 2,
        "exact": 27,
        "inferred_identical": 7,
    }

    verification = {
        "item_count_correct": (
            summary["missing_items_planned"]
            == expected["missing_items"]
        ),
        "post_count_correct": (
            summary["affected_logical_posts"]
            == expected["affected_posts"]
        ),
        "extension_counts_correct": (
            extension_counts.get("jpg", 0) == expected["jpg"]
            and extension_counts.get("mp4", 0) == expected["mp4"]
        ),
        "resolution_counts_correct": (
            resolution_counts.get(
                "EXACT_LOCAL_EAGLE_MATCH_DIFFERENCE",
                0,
            ) == expected["exact"]
            and resolution_counts.get(
                "IDENTICAL_CONTENT_IDENTITY_INFERRED",
                0,
            ) == expected["inferred_identical"]
        ),
        "all_items_eligible": len(ineligible) == 0,
        "ready_for_controlled_import": all(
            [
                summary["missing_items_planned"]
                == expected["missing_items"],
                summary["affected_logical_posts"]
                == expected["affected_posts"],
                extension_counts.get("jpg", 0) == expected["jpg"],
                extension_counts.get("mp4", 0) == expected["mp4"],
                len(ineligible) == 0,
            ]
        ),
    }

    safety = {
        "dry_run": True,
        "source_modified": False,
        "eagle_library_modified": False,
        "database_modified": False,
        "eagle_api_write_requests": 0,
        "eagle_items_created": 0,
        "eagle_items_updated": 0,
        "eagle_items_deleted": 0,
        "physical_files_deleted": 0,
        "physical_files_moved": 0,
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    report_path = (
        REPORTS
        / f"instagram_missing_import_plan_{timestamp}.json"
    )

    manifest_path = (
        REPORTS
        / f"instagram_missing_import_manifest_{timestamp}.json"
    )

    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": summary,
        "verification": verification,
        "ineligible_items": ineligible,
        "items": items,
        "safety": safety,
    }

    REPORTS.mkdir(parents=True, exist_ok=True)

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    manifest_path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "summary": summary,
                "verification": verification,
                "ineligible_items": ineligible,
                "outputs": {
                    "report": str(report_path),
                    "manifest": str(manifest_path),
                },
                "safety": safety,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
