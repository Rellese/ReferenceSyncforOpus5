from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.settings import Settings


INSTAGRAM_URL = re.compile(
    r"instagram\.com/(?:p|reel|reels|tv)/([^/?#]+)",
    re.IGNORECASE,
)


def extract_shortcode(url: str | None) -> str | None:
    if not url:
        return None

    match = INSTAGRAM_URL.search(url)
    return match.group(1) if match else None


def canonical_url(shortcode: str) -> str:
    return f"https://www.instagram.com/p/{shortcode}/"


def safe_url_host(url: str | None) -> str | None:
    if not url:
        return None

    try:
        return urlsplit(url).hostname
    except Exception:
        return None


def fetch_instagram_items(
    api_url: str,
    tag: str,
) -> tuple[list[dict], dict]:
    limit = 1000
    offset = 0
    items = []
    requests_made = 0
    api_total = None

    with httpx.Client(timeout=60.0) as client:
        while True:
            response = client.post(
                f"{api_url}/api/v2/item/get",
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
                raise RuntimeError(
                    f"Eagle API V2 error: {payload}"
                )

            envelope = payload.get("data", {})

            if isinstance(envelope, dict):
                page = envelope.get("data", [])
                api_total = envelope.get("total", api_total)
            elif isinstance(envelope, list):
                page = envelope
            else:
                raise RuntimeError(
                    "Unexpected Eagle V2 item/get response"
                )

            if not isinstance(page, list):
                raise RuntimeError(
                    "Unexpected Eagle V2 page data"
                )

            items.extend(page)
            offset += len(page)

            if not page:
                break

            if api_total is not None and offset >= api_total:
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

def load_local_posts(database_path: Path) -> dict[str, dict]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row

    rows = connection.execute(
        """
        SELECT
            p.id,
            p.external_id AS post_id,
            p.shortcode,
            p.canonical_url,
            p.status,
            COUNT(m.id) AS media_count
        FROM posts p
        JOIN sources s ON s.id = p.source_id
        LEFT JOIN media m ON m.post_id = p.id
        WHERE s.code = 'instagram'
        GROUP BY p.id
        """
    ).fetchall()

    connection.close()

    return {
        row["shortcode"]: dict(row)
        for row in rows
        if row["shortcode"]
    }


def main() -> None:
    settings = Settings.load()
    reports = settings.reports_path

    eagle_items, request_info = fetch_instagram_items(
        settings.eagle_api_url,
        settings.instagram_tag,
    )

    local_posts = load_local_posts(settings.database_path)

    active_items = [
        item
        for item in eagle_items
        if not item.get("isDeleted", False)
    ]

    deleted_items = [
        item
        for item in eagle_items
        if item.get("isDeleted", False)
    ]

    eagle_by_shortcode: dict[str, list[dict]] = defaultdict(list)
    items_without_instagram_url = []
    extension_counts = Counter()
    folder_counts = Counter()

    for item in active_items:
        extension = str(item.get("ext") or "").lower()
        extension_counts[extension or "(empty)"] += 1

        for folder_id in item.get("folders") or []:
            folder_counts[str(folder_id)] += 1

        shortcode = extract_shortcode(item.get("url"))

        if shortcode:
            eagle_by_shortcode[shortcode].append(item)
        else:
            items_without_instagram_url.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "ext": item.get("ext"),
                    "url": item.get("url"),
                    "url_host": safe_url_host(
                        item.get("url")
                    ),
                    "tags": item.get("tags") or [],
                    "folders": item.get("folders") or [],
                    "size": item.get("size"),
                }
            )

    comparisons = []

    exact_posts = 0
    missing_posts = 0
    fewer_items_posts = 0
    excess_items_posts = 0
    total_local_expected = 0
    total_matched_eagle_items = 0

    for shortcode, local in sorted(local_posts.items()):
        expected = int(local["media_count"])
        eagle_count = len(eagle_by_shortcode.get(shortcode, []))

        total_local_expected += expected
        total_matched_eagle_items += eagle_count

        if eagle_count == 0:
            comparison_status = "MISSING_IN_EAGLE"
            missing_posts += 1
        elif eagle_count < expected:
            comparison_status = "FEWER_ITEMS_IN_EAGLE"
            fewer_items_posts += 1
        elif eagle_count > expected:
            comparison_status = "EXCESS_ITEMS_POSSIBLE_DUPLICATES"
            excess_items_posts += 1
        else:
            comparison_status = "EXACT_COUNT_MATCH"
            exact_posts += 1

        comparisons.append(
            {
                "shortcode": shortcode,
                "post_id": local["post_id"],
                "canonical_url": canonical_url(shortcode),
                "local_status": local["status"],
                "expected_primary_media": expected,
                "eagle_items": eagle_count,
                "difference": eagle_count - expected,
                "comparison_status": comparison_status,
                "eagle_item_ids": [
                    item.get("id")
                    for item in eagle_by_shortcode.get(
                        shortcode, []
                    )
                ],
            }
        )

    local_shortcodes = set(local_posts)
    eagle_shortcodes = set(eagle_by_shortcode)

    unknown_eagle_posts = sorted(
        eagle_shortcodes - local_shortcodes
    )

    unknown_eagle_items = [
        {
            "shortcode": shortcode,
            "items": len(eagle_by_shortcode[shortcode]),
            "item_ids": [
                item.get("id")
                for item in eagle_by_shortcode[shortcode]
            ],
            "url": canonical_url(shortcode),
        }
        for shortcode in unknown_eagle_posts
    ]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    report_path = (
        reports
        / f"eagle_instagram_audit_{timestamp}.json"
    )

    csv_path = (
        reports
        / f"eagle_instagram_comparison_{timestamp}.csv"
    )

    no_url_path = (
        reports
        / f"eagle_instagram_items_without_url_{timestamp}.json"
    )

    unknown_path = (
        reports
        / f"eagle_instagram_unknown_posts_{timestamp}.json"
    )

    issues_path = (
        reports
        / f"eagle_instagram_count_issues_{timestamp}.json"
    )

    issue_rows = [
        row
        for row in comparisons
        if row["comparison_status"] != "EXACT_COUNT_MATCH"
    ]

    summary = {
        "tag_used": settings.instagram_tag,
        "eagle_items_returned": len(eagle_items),
        "active_eagle_items": len(active_items),
        "deleted_eagle_items_returned": len(deleted_items),
        "eagle_items_with_instagram_url": (
            len(active_items)
            - len(items_without_instagram_url)
        ),
        "eagle_items_without_instagram_url": len(
            items_without_instagram_url
        ),
        "unique_eagle_instagram_shortcodes": len(
            eagle_by_shortcode
        ),
        "local_logical_posts": len(local_posts),
        "local_expected_primary_media": total_local_expected,
        "eagle_items_matched_to_local_posts": (
            total_matched_eagle_items
        ),
        "posts_with_exact_item_count": exact_posts,
        "posts_missing_in_eagle": missing_posts,
        "posts_with_fewer_items_in_eagle": fewer_items_posts,
        "posts_with_excess_items_possible_duplicates": (
            excess_items_posts
        ),
        "eagle_posts_not_found_in_local_baseline": len(
            unknown_eagle_posts
        ),
        "extension_counts": dict(
            sorted(extension_counts.items())
        ),
        "folder_id_counts": dict(
            folder_counts.most_common()
        ),
        "api_requests": request_info,
    }

    report = {
        "mode": "EAGLE_READ_ONLY",
        "summary": summary,
        "outputs": {
            "report": str(report_path),
            "comparison_csv": str(csv_path),
            "items_without_instagram_url": str(no_url_path),
            "unknown_eagle_posts": str(unknown_path),
            "count_issues": str(issues_path),
        },
        "safety": {
            "eagle_library_modified": False,
            "source_modified": False,
            "eagle_items_created": 0,
            "eagle_items_updated": 0,
            "eagle_items_deleted": 0,
        },
    }

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    no_url_path.write_text(
        json.dumps(
            items_without_instagram_url,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    unknown_path.write_text(
        json.dumps(
            unknown_eagle_items,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    issues_path.write_text(
        json.dumps(
            issue_rows,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with csv_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "shortcode",
                "post_id",
                "canonical_url",
                "local_status",
                "expected_primary_media",
                "eagle_items",
                "difference",
                "comparison_status",
                "eagle_item_ids",
            ),
        )

        writer.writeheader()

        for row in comparisons:
            output = dict(row)
            output["eagle_item_ids"] = ",".join(
                value
                for value in row["eagle_item_ids"]
                if value
            )
            writer.writerow(output)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
