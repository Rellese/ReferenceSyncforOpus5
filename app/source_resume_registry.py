"""Universal read-only resume validation.

This module validates partial import state through the shared
sources/posts/media/eagle_items registry. It never modifies SQLite
or Eagle and contains no platform-specific SQL.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = (
    PROJECT / "data" / "reference_sync.sqlite3"
)


class SourceResumeError(RuntimeError):
    """Raised when a resume plan cannot be trusted."""


def normalized_text(value: Any) -> str:
    return str(value or "").strip()


def single_value(
    values: set[Any],
    error_code: str,
) -> Any:
    if len(values) != 1:
        raise SourceResumeError(error_code)

    return next(iter(values))


def table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def registered_resume_state(
    items: list[dict[str, Any]],
    *,
    database_path: Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Validate one partial publication against shared tables.

    Required shared tables:
      sources, posts, media, eagle_items, post_import_order.

    The post_import_order table will be introduced by the generic
    finalizer. Absence of that table blocks resume instead of falling
    back to Instagram-specific tables.
    """

    if not items:
        raise SourceResumeError("EMPTY_RESUME_PLAN")

    if not all(isinstance(item, dict) for item in items):
        raise SourceResumeError(
            "RESUME_PLAN_CONTAINS_NON_OBJECT"
        )

    source_codes = {
        normalized_text(item.get("source_code")).lower()
        for item in items
    }

    source_code = single_value(
        source_codes,
        "RESUME_PLAN_CONTAINS_MULTIPLE_SOURCES",
    )

    if not source_code:
        raise SourceResumeError(
            "RESUME_PLAN_HAS_NO_SOURCE_CODE"
        )

    external_post_ids = {
        normalized_text(item.get("post_id"))
        for item in items
    }

    external_post_id = single_value(
        external_post_ids,
        "RESUME_PLAN_CONTAINS_MULTIPLE_POSTS",
    )

    if not external_post_id:
        raise SourceResumeError(
            "RESUME_PLAN_HAS_NO_POST_ID"
        )

    if not all(
        bool(item.get("resume_partial"))
        for item in items
    ):
        raise SourceResumeError(
            "INCONSISTENT_RESUME_FLAGS"
        )

    expected_post_number = int(
        single_value(
            {
                int(
                    item.get(
                        "existing_post_number"
                    ) or 0
                )
                for item in items
            },
            "INCONSISTENT_EXISTING_POST_NUMBER",
        )
    )

    if expected_post_number < 1:
        raise SourceResumeError(
            "INVALID_EXISTING_POST_NUMBER"
        )

    expected_total = int(
        single_value(
            {
                int(
                    item.get("component_count")
                    or 0
                )
                for item in items
            },
            "INCONSISTENT_TOTAL_COMPONENT_COUNT",
        )
    )

    if expected_total < 1:
        raise SourceResumeError(
            "INVALID_TOTAL_COMPONENT_COUNT"
        )

    declared_components_tuple = single_value(
        {
            tuple(sorted(
                int(value)
                for value in item.get(
                    "imported_component_numbers",
                    [],
                )
            ))
            for item in items
        },
        "INCONSISTENT_DECLARED_COMPONENTS",
    )

    declared_media_tuple = single_value(
        {
            tuple(sorted(
                normalized_text(value)
                for value in item.get(
                    "imported_media_ids",
                    [],
                )
                if normalized_text(value)
            ))
            for item in items
        },
        "INCONSISTENT_DECLARED_MEDIA_IDS",
    )

    declared_components = set(
        declared_components_tuple
    )
    declared_media_ids = set(
        declared_media_tuple
    )

    if not declared_components or not declared_media_ids:
        raise SourceResumeError(
            "EMPTY_DECLARED_RESUME_STATE"
        )

    database_path = Path(
        database_path
    ).expanduser().resolve()

    if not database_path.is_file():
        raise SourceResumeError(
            f"REGISTRY_DATABASE_NOT_FOUND: "
            f"{database_path}"
        )

    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row

    try:
        required_tables = {
            "sources",
            "posts",
            "media",
            "eagle_items",
            "post_import_order",
        }

        missing_tables = sorted(
            table
            for table in required_tables
            if not table_exists(connection, table)
        )

        if missing_tables:
            raise SourceResumeError(
                "GENERIC_RESUME_TABLES_MISSING: "
                + ",".join(missing_tables)
            )

        post_row = connection.execute(
            """
            SELECT
                posts.id,
                posts.external_id,
                posts.status,
                posts.expected_media_count,
                sources.code AS source_code
            FROM posts
            JOIN sources
              ON sources.id = posts.source_id
            WHERE
                sources.code = ?
                AND posts.external_id = ?
            """,
            (
                source_code,
                external_post_id,
            ),
        ).fetchone()

        if post_row is None:
            raise SourceResumeError(
                "RESUME_POST_NOT_FOUND_IN_REGISTRY"
            )

        post_status = normalized_text(
            post_row["status"]
        ).upper()

        if post_status != "PARTIALLY_IMPORTED":
            raise SourceResumeError(
                "RESUME_POST_IS_NOT_PARTIALLY_IMPORTED"
            )

        registered_total = int(
            post_row["expected_media_count"] or 0
        )

        if registered_total != expected_total:
            raise SourceResumeError(
                "TOTAL_COMPONENT_COUNT_DOES_NOT_MATCH_REGISTRY"
            )

        database_post_id = int(post_row["id"])

        order_row = connection.execute(
            """
            SELECT
                post_number,
                name_marker,
                source_code
            FROM post_import_order
            WHERE post_id = ?
            """,
            (database_post_id,),
        ).fetchone()

        if order_row is None:
            raise SourceResumeError(
                "RESUME_POST_NUMBER_NOT_FOUND"
            )

        if (
            normalized_text(
                order_row["source_code"]
            ).lower()
            != source_code
        ):
            raise SourceResumeError(
                "POST_ORDER_SOURCE_MISMATCH"
            )

        registered_post_number = int(
            order_row["post_number"]
        )

        if (
            registered_post_number
            != expected_post_number
        ):
            raise SourceResumeError(
                "EXISTING_POST_NUMBER_DOES_NOT_MATCH_REGISTRY"
            )

        registered_rows = connection.execute(
            """
            SELECT
                media.id AS database_media_id,
                media.external_media_id,
                media.component_index,
                media.status AS media_status,
                eagle_items.eagle_item_id,
                eagle_items.status AS eagle_status,
                eagle_items.verified_at
            FROM media
            JOIN eagle_items
              ON eagle_items.media_id = media.id
            WHERE
                media.post_id = ?
                AND media.status = 'IMPORTED'
                AND eagle_items.status = 'VERIFIED'
                AND eagle_items.verified_at IS NOT NULL
            ORDER BY media.component_index
            """,
            (database_post_id,),
        ).fetchall()

        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        if foreign_key_errors:
            raise SourceResumeError(
                "REGISTRY_FOREIGN_KEY_ERRORS"
            )

    finally:
        connection.close()

    if not registered_rows:
        raise SourceResumeError(
            "NO_VERIFIED_IMPORTED_COMPONENTS"
        )

    registered_components = {
        int(row["component_index"])
        for row in registered_rows
    }

    registered_external_media_ids = {
        normalized_text(
            row["external_media_id"]
        )
        for row in registered_rows
    }

    normalized_registered_media_ids = {
        value.removeprefix(
            f"{source_code}:"
        )
        for value in registered_external_media_ids
    }

    if registered_components != declared_components:
        raise SourceResumeError(
            "IMPORTED_COMPONENTS_DO_NOT_MATCH_REGISTRY"
        )

    if (
        normalized_registered_media_ids
        != declared_media_ids
    ):
        raise SourceResumeError(
            "IMPORTED_MEDIA_IDS_DO_NOT_MATCH_REGISTRY"
        )

    registered_eagle_ids = {
        normalized_text(row["eagle_item_id"])
        for row in registered_rows
    }

    if (
        "" in registered_eagle_ids
        or len(registered_eagle_ids)
        != len(registered_rows)
    ):
        raise SourceResumeError(
            "DUPLICATE_OR_MISSING_EAGLE_IDS"
        )

    planned_components = {
        int(item.get("component_index") or 0)
        for item in items
    }

    planned_media_ids = {
        normalized_text(item.get("media_id"))
        for item in items
    }

    if planned_components & registered_components:
        raise SourceResumeError(
            "PLANNED_COMPONENT_ALREADY_IMPORTED"
        )

    if planned_media_ids & declared_media_ids:
        raise SourceResumeError(
            "PLANNED_MEDIA_ID_ALREADY_IMPORTED"
        )

    if any(
        int(item.get("post_number") or 0)
        != registered_post_number
        for item in items
    ):
        raise SourceResumeError(
            "PLAN_DOES_NOT_PRESERVE_POST_NUMBER"
        )

    return {
        "source_code": source_code,
        "post_id": external_post_id,
        "database_post_id": database_post_id,
        "post_number": registered_post_number,
        "name_marker": normalized_text(
            order_row["name_marker"]
        ),
        "component_count": expected_total,
        "registered_component_numbers": sorted(
            registered_components
        ),
        "registered_media_ids": sorted(
            declared_media_ids
        ),
        "registered_eagle_ids": sorted(
            registered_eagle_ids
        ),
        "database_open_mode": "READ_ONLY",
        "database_modified": False,
        "eagle_api_requests": 0,
    }
