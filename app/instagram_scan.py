from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.settings import Settings


MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
}

POST_ID_PATTERN = re.compile(r"^(\d+)")
SHORTCODE_PATTERN = re.compile(
    r"instagram\.com/(?:p|reel|reels|tv)/([^/?#]+)",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_scalar(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text or None

    return None


def first_value(data: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = normalize_scalar(data.get(key))
        if value:
            return value
    return None


def extract_shortcode_from_url(url: str | None) -> str | None:
    if not url:
        return None

    match = SHORTCODE_PATTERN.search(url)
    return match.group(1) if match else None


def extract_filename_post_id(path: Path) -> str | None:
    match = POST_ID_PATTERN.match(path.name)
    return match.group(1) if match else None


def load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))

        if not isinstance(payload, dict):
            return None, "JSON root is not an object"

        return payload, None

    except Exception as exc:
        return None, str(exc)


def identify_post(
    media_path: Path,
    metadata: dict | None,
) -> dict:
    metadata = metadata or {}

    canonical_url = first_value(
        metadata,
        (
            "post_url",
            "webpage_url",
            "permalink",
            "url",
        ),
    )

    shortcode = first_value(
        metadata,
        (
            "shortcode",
            "code",
        ),
    )

    if not shortcode:
        shortcode = extract_shortcode_from_url(canonical_url)

    post_id = first_value(
        metadata,
        (
            "post_id",
            "post_pk",
            "parent_id",
            "id",
            "pk",
        ),
    )

    if not post_id:
        post_id = extract_filename_post_id(media_path)

    media_id = first_value(
        metadata,
        (
            "media_id",
            "id",
            "pk",
        ),
    )

    return {
        "post_id": post_id,
        "shortcode": shortcode,
        "media_id": media_id,
        "canonical_url": canonical_url,
    }


def relative_strings(paths: list[Path], root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in paths)


def scan() -> dict:
    settings = Settings.load()
    root = settings.existing_instagram_source
    reports = settings.reports_path

    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"Instagram source folder not found: {root}")

    media_files: list[Path] = []
    json_files: list[Path] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        lower_name = path.name.lower()

        if lower_name.endswith(".json"):
            json_files.append(path)
        elif path.suffix.lower() in MEDIA_EXTENSIONS:
            media_files.append(path)

    media_set = {path.resolve() for path in media_files}
    json_set = {path.resolve() for path in json_files}

    media_without_json: list[Path] = []
    json_without_media: list[Path] = []
    damaged_json: list[dict] = []
    recognized_records: list[dict] = []

    post_keys: set[str] = set()
    shortcode_keys: set[str] = set()
    post_id_keys: set[str] = set()
    extension_counts = Counter()

    for media_path in media_files:
        extension_counts[media_path.suffix.lower()] += 1

        sidecar_path = Path(f"{media_path}.json")
        metadata = None

        if sidecar_path.resolve() in json_set:
            metadata, error = load_json(sidecar_path)

            if error:
                damaged_json.append(
                    {
                        "path": str(sidecar_path.relative_to(root)),
                        "error": error,
                    }
                )
        else:
            media_without_json.append(media_path)

        identity = identify_post(media_path, metadata)

        if identity["shortcode"]:
            shortcode_keys.add(identity["shortcode"])
            post_keys.add(f"shortcode:{identity['shortcode']}")
        elif identity["post_id"]:
            post_id_keys.add(identity["post_id"])
            post_keys.add(f"id:{identity['post_id']}")

        if identity["post_id"]:
            post_id_keys.add(identity["post_id"])

        recognized_records.append(
            {
                "media": str(media_path.relative_to(root)),
                "sidecar": (
                    str(sidecar_path.relative_to(root))
                    if sidecar_path.resolve() in json_set
                    else None
                ),
                **identity,
            }
        )

    for json_path in json_files:
        media_candidate = Path(str(json_path)[:-5])

        if media_candidate.resolve() not in media_set:
            json_without_media.append(json_path)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports / f"instagram_readonly_scan_{timestamp}.json"

    records_path = (
        reports / f"instagram_readonly_scan_records_{timestamp}.jsonl"
    )

    media_without_json_path = (
        reports / f"instagram_media_without_json_{timestamp}.txt"
    )

    json_without_media_path = (
        reports / f"instagram_json_without_media_{timestamp}.txt"
    )

    damaged_json_path = (
        reports / f"instagram_damaged_json_{timestamp}.json"
    )

    report = {
        "mode": "READ_ONLY",
        "created_at": utc_now(),
        "source_root": str(root),
        "source_modified": False,
        "eagle_library_modified": False,
        "summary": {
            "physical_media_files": len(media_files),
            "json_sidecars": len(json_files),
            "recognized_logical_post_keys": len(post_keys),
            "unique_shortcodes": len(shortcode_keys),
            "unique_numeric_post_ids": len(post_id_keys),
            "media_with_sidecar": (
                len(media_files) - len(media_without_json)
            ),
            "media_without_sidecar": len(media_without_json),
            "json_without_media": len(json_without_media),
            "damaged_json": len(damaged_json),
            "extension_counts": dict(sorted(extension_counts.items())),
        },
        "outputs": {
            "report": str(report_path),
            "records": str(records_path),
            "media_without_json": str(media_without_json_path),
            "json_without_media": str(json_without_media_path),
            "damaged_json": str(damaged_json_path),
        },
        "important_note": (
            "This scan did not modify the Instagram source or Eagle library."
        ),
    }

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with records_path.open("w", encoding="utf-8") as handle:
        for record in recognized_records:
            handle.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )

    media_without_json_path.write_text(
        "\n".join(relative_strings(media_without_json, root)),
        encoding="utf-8",
    )

    json_without_media_path.write_text(
        "\n".join(relative_strings(json_without_media, root)),
        encoding="utf-8",
    )

    damaged_json_path.write_text(
        json.dumps(damaged_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return report


def main() -> None:
    print(json.dumps(scan(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
