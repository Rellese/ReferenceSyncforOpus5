from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
DATABASE = PROJECT / "data/reference_sync.sqlite3"
REPORTS = PROJECT / "reports"

EAGLE_API = "http://localhost:41595"
INSTAGRAM_FOLDER_ID = "MRWRIOJO42ER5"

ORDER_PATTERN = re.compile(
    r"inst(?:agram)?poporder[-_\s]*(\d+)[-_\s]+(\d+)",
    re.IGNORECASE,
)


def rows_as_dicts(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    cursor = connection.execute(query, parameters)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def table_columns(
    connection: sqlite3.Connection,
    table: str,
) -> list[str]:
    if not table_exists(connection, table):
        return []
    return [
        row[1]
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]


def read_registry(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = [
        "instagram_sync_jobs",
        "instagram_sync_posts",
        "instagram_sync_media",
        "instagram_sync_imports",
    ]

    result: dict[str, Any] = {}

    for table in tables:
        columns = table_columns(connection, table)

        if not columns:
            result[table] = {
                "exists": False,
                "columns": [],
                "row_count": 0,
                "rows": [],
            }
            continue

        count = connection.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0]

        order_column = next(
            (
                column
                for column in (
                    "created_at",
                    "imported_at",
                    "discovered_at",
                    "id",
                    "rowid",
                )
                if column in columns
            ),
            None,
        )

        order_sql = (
            f' ORDER BY "{order_column}" ASC'
            if order_column and order_column != "rowid"
            else " ORDER BY rowid ASC"
        )

        rows = rows_as_dicts(
            connection,
            f'SELECT * FROM "{table}"{order_sql}',
        )

        result[table] = {
            "exists": True,
            "columns": columns,
            "row_count": count,
            "rows": rows,
        }

    return result


def find_eagle_ids(registry: dict[str, Any]) -> list[str]:
    found: list[str] = []

    for table_data in registry.values():
        for row in table_data.get("rows", []):
            for key, value in row.items():
                normalized_key = key.lower()

                if (
                    "eagle" in normalized_key
                    and "id" in normalized_key
                    and isinstance(value, str)
                    and value.startswith("M")
                ):
                    found.append(value)

    return list(dict.fromkeys(found))


def eagle_get(path: str, parameters: dict[str, Any]) -> Any:
    query = urllib.parse.urlencode(parameters)
    url = f"{EAGLE_API}{path}?{query}"

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def unwrap_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        nested = data.get("data")

        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]

        if "id" in data:
            return [data]

    return []


def fetch_registered_items(eagle_ids: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for eagle_id in eagle_ids:
        try:
            payload = eagle_get(
                "/api/v2/item/get",
                {
                    "id": eagle_id,
                    "fields": (
                        "id,name,ext,url,tags,folders,size,"
                        "annotation,modificationTime"
                    ),
                },
            )

            received = unwrap_items(payload)

            if received:
                item = received[0]
                items.append(
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "ext": item.get("ext"),
                        "url": item.get("url"),
                        "tags": item.get("tags"),
                        "folders": item.get("folders"),
                        "size": item.get("size"),
                    }
                )
            else:
                items.append(
                    {
                        "id": eagle_id,
                        "error": "ITEM_NOT_RETURNED",
                    }
                )

        except Exception as error:
            items.append(
                {
                    "id": eagle_id,
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    return items


def scan_existing_order_names() -> dict[str, Any]:
    """
    Ищет старые имена instpoporder.

    Eagle иногда возвращает HTTP 500 при одновременном использовании
    folders + fields в GET /api/v2/item/get. Поэтому читаем всю библиотеку
    постранично и фильтруем папку локально.
    """
    matched: list[dict[str, Any]] = []
    offset = 0
    limit = 1000
    total_received = 0
    pages_received = 0
    api_errors: list[dict[str, Any]] = []

    while True:
        try:
            # Намеренно не передаём fields и folders:
            # на некоторых версиях Eagle 4.0 это вызывает HTTP 500.
            payload = eagle_get(
                "/api/v2/item/get",
                {
                    "offset": offset,
                    "limit": limit,
                },
            )
        except Exception as error:
            api_errors.append(
                {
                    "offset": offset,
                    "limit": limit,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
            break

        page = unwrap_items(payload)

        if not page:
            break

        pages_received += 1
        total_received += len(page)

        for item in page:
            folders = item.get("folders") or []

            if isinstance(folders, str):
                folders = [folders]

            # Если folders присутствует в ответе, оставляем только Instagram.
            # Если поле отсутствует, всё равно проверяем имя — операция read-only.
            if folders and INSTAGRAM_FOLDER_ID not in folders:
                continue

            name = str(item.get("name") or "")
            match = ORDER_PATTERN.search(name)

            if not match:
                continue

            matched.append(
                {
                    "id": item.get("id"),
                    "name": name,
                    "url": item.get("url") or item.get("website"),
                    "ext": item.get("ext"),
                    "folders": folders,
                    "post_number": int(match.group(1)),
                    "component_number": int(match.group(2)),
                }
            )

        if len(page) < limit:
            break

        offset += limit

    post_numbers = [
        item["post_number"]
        for item in matched
    ]

    matched.sort(
        key=lambda item: (
            item["post_number"],
            item["component_number"],
            str(item["id"]),
        )
    )

    return {
        "folder_id": INSTAGRAM_FOLDER_ID,
        "scan_strategy": "ALL_LIBRARY_ITEMS_FILTERED_LOCALLY",
        "pages_received": pages_received,
        "items_received": total_received,
        "matching_items": len(matched),
        "minimum_post_number": min(post_numbers) if post_numbers else None,
        "maximum_post_number": max(post_numbers) if post_numbers else None,
        "next_post_number": max(post_numbers) + 1 if post_numbers else None,
        "api_errors": api_errors,
        "last_30_matches": matched[-30:],
    }


def main() -> None:
    if not DATABASE.exists():
        raise SystemExit(f"Database not found: {DATABASE}")

    REPORTS.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row

    try:
        registry = read_registry(connection)
    finally:
        connection.close()

    eagle_ids = find_eagle_ids(registry)
    registered_items = fetch_registered_items(eagle_ids)
    existing_order = scan_existing_order_names()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS / f"instagram_order_diagnose_{timestamp}.json"

    report = {
        "status": "READ_ONLY_DIAGNOSIS_COMPLETE",
        "database": str(DATABASE),
        "registry": registry,
        "registered_eagle_ids": eagle_ids,
        "registered_eagle_items": registered_items,
        "existing_instpoporder": existing_order,
        "safety": {
            "database_modified": False,
            "eagle_items_created": 0,
            "eagle_items_updated": 0,
            "eagle_items_deleted": 0,
            "files_downloaded": 0,
        },
        "report_path": str(report_path),
    }

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    concise = {
        "status": report["status"],
        "registry_counts": {
            table: data["row_count"]
            for table, data in registry.items()
        },
        "registry_columns": {
            table: data["columns"]
            for table, data in registry.items()
        },
        "registered_eagle_ids_found": len(eagle_ids),
        "registered_eagle_items_received": sum(
            1
            for item in registered_items
            if not item.get("error")
        ),
        "existing_instpoporder": existing_order,
        "registered_eagle_items": registered_items,
        "report_path": str(report_path),
        "safety": report["safety"],
    }

    print(json.dumps(concise, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
