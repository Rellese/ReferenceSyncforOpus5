from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.settings import Settings


POST_PREFIX = re.compile(r"^(\d+)")
INSTAGRAM_POST_URL = re.compile(
    r"https?://(?:www\.)?instagram\.com/"
    r"(?:p|reel|reels|tv)/([^/?#]+)",
    re.IGNORECASE,
)

CANDIDATE_WORDS = (
    "id",
    "pk",
    "code",
    "shortcode",
    "url",
    "post",
    "parent",
    "media",
)


def scalar(value: Any) -> str | None:
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text if text else None
    return None


def filename_post_id(path: Path) -> str | None:
    name = path.name

    if name.lower().endswith(".json"):
        name = name[:-5]

    match = POST_PREFIX.match(name)
    return match.group(1) if match else None


def is_candidate_key(key: str) -> bool:
    lower = key.lower()
    return any(word in lower for word in CANDIDATE_WORDS)


def main() -> None:
    settings = Settings.load()
    root = settings.existing_instagram_source
    reports = settings.reports_path

    json_files = sorted(
        path for path in root.rglob("*.json")
        if path.is_file()
    )

    field_present = Counter()
    field_values: dict[str, set[str]] = defaultdict(set)
    field_filename_matches = Counter()
    field_instagram_urls = Counter()
    field_url_shortcodes: dict[str, set[str]] = defaultdict(set)

    filename_groups = Counter()
    unreadable = 0
    non_object = 0

    for json_path in json_files:
        post_id = filename_post_id(json_path)

        if post_id:
            filename_groups[post_id] += 1

        try:
            payload = json.loads(
                json_path.read_text(encoding="utf-8")
            )
        except Exception:
            unreadable += 1
            continue

        if not isinstance(payload, dict):
            non_object += 1
            continue

        for key, raw_value in payload.items():
            if not is_candidate_key(key):
                continue

            value = scalar(raw_value)

            if value is None:
                continue

            field_present[key] += 1
            field_values[key].add(value)

            if post_id and value == post_id:
                field_filename_matches[key] += 1

            match = INSTAGRAM_POST_URL.search(value)

            if match:
                field_instagram_urls[key] += 1
                field_url_shortcodes[key].add(match.group(1))

    component_distribution = Counter(filename_groups.values())

    fields = []

    for key in sorted(
        field_present,
        key=lambda item: (
            -field_present[item],
            item.lower(),
        ),
    ):
        fields.append(
            {
                "field": key,
                "present": field_present[key],
                "unique_values": len(field_values[key]),
                "matches_filename_post_id": (
                    field_filename_matches[key]
                ),
                "instagram_post_urls": (
                    field_instagram_urls[key]
                ),
                "unique_instagram_url_shortcodes": len(
                    field_url_shortcodes[key]
                ),
            }
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = (
        reports
        / f"instagram_metadata_diagnose_{timestamp}.json"
    )

    report = {
        "mode": "READ_ONLY",
        "source_root": str(root),
        "summary": {
            "json_files": len(json_files),
            "unreadable_json": unreadable,
            "non_object_json": non_object,
            "filename_logical_post_ids": len(filename_groups),
            "minimum_components_per_post": (
                min(filename_groups.values())
                if filename_groups else 0
            ),
            "maximum_components_per_post": (
                max(filename_groups.values())
                if filename_groups else 0
            ),
            "component_count_distribution": {
                str(count): posts
                for count, posts in sorted(
                    component_distribution.items()
                )
            },
        },
        "candidate_fields": fields,
        "source_modified": False,
        "eagle_library_modified": False,
    }

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "summary": report["summary"],
                "candidate_fields": fields,
                "report": str(report_path),
                "source_modified": False,
                "eagle_library_modified": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
