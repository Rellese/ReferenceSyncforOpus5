from __future__ import annotations

"""
V6.4.6 live reconciliation of Instagram registry and Eagle.

This module is read-only:
- no SQLite writes;
- no Eagle writes;
- no staging changes.
"""

import sqlite3
from pathlib import Path
from typing import Any

import httpx


API_URL = "http://localhost:41595"


def _response_items(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], int | None]:
    data = payload.get("data")

    if isinstance(data, list):
        return [
            item for item in data
            if isinstance(item, dict)
        ], None

    if isinstance(data, dict):
        items = data.get("data")

        if not isinstance(items, list):
            items = data.get("items")

        if not isinstance(items, list):
            items = []

        try:
            total = int(data.get("total"))
        except (TypeError, ValueError):
            total = None

        return [
            item for item in items
            if isinstance(item, dict)
        ], total

    return [], None


def _canonical_url(value: Any) -> str | None:
    text = str(value or "").strip()

    if not text:
        return None

    return (
        text.split("?", 1)[0]
        .split("#", 1)[0]
        .rstrip("/")
        .lower()
    )


def _fetch_active_eagle_items() -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
]:
    items: list[dict[str, Any]] = []
    offset = 0
    limit = 1000
    requests = 0

    with httpx.Client(timeout=120.0) as client:
        while True:
            response = client.post(
                f"{API_URL}/api/v2/item/get",
                json={
                    "fields": [
                        "id",
                        "url",
                        "name",
                        "isDeleted",
                    ],
                    "offset": offset,
                    "limit": limit,
                },
            )
            response.raise_for_status()

            payload = response.json()
            requests += 1

            if payload.get("status") != "success":
                raise RuntimeError(
                    f"EAGLE_RECONCILIATION_FAILED: {payload}"
                )

            page, total = _response_items(payload)

            if not page:
                break

            items.extend(
                item
                for item in page
                if not item.get("isDeleted")
            )

            offset += len(page)

            if total is not None and offset >= total:
                break

            if len(page) < limit:
                break

    return items, {
        "endpoint": "/api/v2/item/get",
        "requests": requests,
        "active_items_received": len(items),
    }


def reconcile_instagram_registry(
    database_path: Path,
) -> dict[str, Any]:
    """
    Return live state for every post in instagram_sync_posts.

    A registry component counts as imported only when its saved
    Eagle ID is present in the currently active Eagle library.
    """
    if not database_path.is_file():
        return {
            "available": True,
            "registry_state": {},
            "inactive_post_ids": set(),
            "inactive_shortcodes": set(),
            "inactive_urls": set(),
            "reimport_deleted_posts": 0,
            "resume_partial_posts": 0,
            "fully_present_posts": 0,
            "eagle": {
                "requests": 0,
                "active_items_received": 0,
            },
        }

    active_items, eagle_info = _fetch_active_eagle_items()

    active_ids = {
        str(item.get("id") or "").strip()
        for item in active_items
        if str(item.get("id") or "").strip()
    }

    connection = sqlite3.connect(
        f"file:{database_path.resolve()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row

    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table'"
            )
        }

        required = {
            "instagram_sync_posts",
            "instagram_sync_media",
            "instagram_sync_imports",
            "instagram_sync_post_order",
        }

        if not required.issubset(tables):
            return {
                "available": True,
                "registry_state": {},
                "inactive_post_ids": set(),
                "inactive_shortcodes": set(),
                "inactive_urls": set(),
                "reimport_deleted_posts": 0,
                "resume_partial_posts": 0,
                "fully_present_posts": 0,
                "eagle": eagle_info,
                "note": "Instagram sync registry is incomplete",
            }

        post_rows = connection.execute(
            """
            SELECT
                p.post_id,
                p.shortcode,
                p.canonical_url,
                p.import_status,
                p.component_count,
                o.post_number
            FROM instagram_sync_posts AS p
            LEFT JOIN instagram_sync_post_order AS o
                ON o.post_id = p.post_id
               AND o.order_marker = 'instpoporder'
            """
        ).fetchall()

        component_rows = connection.execute(
            """
            SELECT
                m.post_id,
                m.media_id,
                m.component_index,
                m.import_status,
                i.eagle_item_id,
                i.verification_status
            FROM instagram_sync_media AS m
            LEFT JOIN instagram_sync_imports AS i
                ON i.media_id = m.media_id
            ORDER BY m.post_id, m.component_index
            """
        ).fetchall()

    finally:
        connection.close()

    components_by_post: dict[str, list[sqlite3.Row]] = {}

    for row in component_rows:
        post_id = str(row["post_id"] or "").strip()
        components_by_post.setdefault(
            post_id,
            [],
        ).append(row)

    registry_state: dict[str, dict[str, Any]] = {}
    inactive_post_ids: set[str] = set()
    inactive_shortcodes: set[str] = set()
    inactive_urls: set[str] = set()

    restore_count = 0
    partial_count = 0
    full_count = 0

    for post in post_rows:
        post_id = str(post["post_id"] or "").strip()

        if not post_id:
            continue

        try:
            expected_count = max(
                1,
                int(post["component_count"] or 1),
            )
        except (TypeError, ValueError):
            expected_count = 1

        rows = components_by_post.get(post_id, [])

        active_rows = []

        for row in rows:
            eagle_id = str(
                row["eagle_item_id"] or ""
            ).strip()

            if (
                eagle_id
                and eagle_id in active_ids
                and str(
                    row["verification_status"] or ""
                ).strip().upper() == "VERIFIED"
            ):
                active_rows.append(row)

        active_components = sorted({
            int(row["component_index"])
            for row in active_rows
            if row["component_index"] is not None
            and int(row["component_index"]) > 0
        })

        active_media_ids = sorted({
            str(row["media_id"])
            for row in active_rows
            if str(row["media_id"] or "").strip()
        })

        fully_present = (
            len(active_components) >= expected_count
            and len(active_media_ids) >= expected_count
        )

        if fully_present:
            full_count += 1
            continue

        inactive_post_ids.add(post_id)

        shortcode = str(
            post["shortcode"] or ""
        ).strip()

        if shortcode:
            inactive_shortcodes.add(shortcode)

        normalized_url = _canonical_url(
            post["canonical_url"]
        )

        if normalized_url:
            inactive_urls.add(normalized_url)

        try:
            post_number = int(post["post_number"])
        except (TypeError, ValueError):
            post_number = None

        if active_components:
            mode = "RESUME_PARTIAL"
            partial_count += 1
        else:
            mode = "REIMPORT_DELETED_AS_NEW"
            restore_count += 1
            # A fully deleted publication releases its old
            # number. SQLite keeps history only.
            post_number = None

        registry_state[post_id] = {
            "live_mode": mode,
            "import_status": str(
                post["import_status"] or ""
            ).strip().upper(),
            "component_count": expected_count,
            "imported_component_numbers": active_components,
            "imported_media_ids": active_media_ids,
            "post_number": post_number,
            "registered_component_rows": len(rows),
            "active_eagle_components": len(active_components),
        }

    return {
        "available": True,
        "registry_state": registry_state,
        "inactive_post_ids": inactive_post_ids,
        "inactive_shortcodes": inactive_shortcodes,
        "inactive_urls": inactive_urls,
        "reimport_deleted_posts": restore_count,
        "resume_partial_posts": partial_count,
        "fully_present_posts": full_count,
        "registered_posts_checked": len(post_rows),
        "registered_components_checked": len(component_rows),
        "eagle": eagle_info,
    }
