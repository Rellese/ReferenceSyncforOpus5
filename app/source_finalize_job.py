"""Generic finalizer for staged Eagle imports.

Preview performs read-only Eagle and SQLite validation.
Apply requires both the ``apply`` operation and ``--commit``.
Platform-specific legacy tables are never read or modified.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.source_adapter import get_source_adapter


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = (
    PROJECT / "data" / "reference_sync.sqlite3"
)
STAGING_ROOT = PROJECT / "downloads"
DEFAULT_API_URL = "http://localhost:41595"
REPORTS = PROJECT / "reports"
BACKUPS = PROJECT / "data" / "backups"


class SourceFinalizeError(RuntimeError):
    """Raised when a generic finalization is unsafe."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def normalized_text(value: Any) -> str:
    return str(value or "").strip()


def response_items(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    data = payload.get("data", [])

    if isinstance(data, list):
        return [
            item
            for item in data
            if isinstance(item, dict)
        ]

    if isinstance(data, dict):
        nested = data.get("data")

        if isinstance(nested, list):
            return [
                item
                for item in nested
                if isinstance(item, dict)
            ]

        if data.get("id"):
            return [data]

    return []


def load_import_report(
    report_path: Path,
    *,
    source_code: str,
    job_id: str,
) -> dict[str, Any]:
    report_path = Path(
        report_path
    ).expanduser().resolve()

    if not report_path.is_file():
        raise FileNotFoundError(report_path)

    report = json.loads(
        report_path.read_text(encoding="utf-8")
    )

    if not isinstance(report, dict):
        raise SourceFinalizeError(
            "IMPORT_REPORT_ROOT_IS_NOT_OBJECT"
        )

    if report.get("status") != "BATCH_IMPORTED":
        raise SourceFinalizeError(
            "IMPORT_REPORT_IS_NOT_BATCH_IMPORTED"
        )

    report_job_id = normalized_text(
        report.get("job_id")
    )

    if report_job_id != job_id:
        raise SourceFinalizeError(
            "IMPORT_REPORT_JOB_ID_MISMATCH"
        )

    report_source = normalized_text(
        report.get("source_code")
    ).lower()

    if report_source and report_source != source_code:
        raise SourceFinalizeError(
            "IMPORT_REPORT_SOURCE_MISMATCH"
        )

    results = report.get("results")

    if not isinstance(results, list) or not results:
        raise SourceFinalizeError(
            "IMPORT_REPORT_HAS_NO_RESULTS"
        )

    if not all(
        isinstance(result, dict)
        for result in results
    ):
        raise SourceFinalizeError(
            "IMPORT_REPORT_RESULT_IS_NOT_OBJECT"
        )

    if len(results) != int(
        report.get("planned_items") or -1
    ):
        raise SourceFinalizeError(
            "IMPORT_RESULT_COUNT_MISMATCH"
        )

    if len(results) != int(
        report.get("imported_items") or -1
    ):
        raise SourceFinalizeError(
            "IMPORT_IMPORTED_COUNT_MISMATCH"
        )

    required = {
        "eagle_id",
        "media_id",
        "post_id",
        "post_url",
        "name",
        "post_number",
        "component_index",
        "component_count",
    }

    for index, result in enumerate(
        results,
        start=1,
    ):
        missing = sorted(
            key
            for key in required
            if not normalized_text(result.get(key))
        )

        if missing:
            raise SourceFinalizeError(
                f"RESULT_{index}_MISSING_FIELDS:"
                + ",".join(missing)
            )

        result_source = normalized_text(
            result.get("source_code")
        ).lower()

        if result_source and result_source != source_code:
            raise SourceFinalizeError(
                f"RESULT_{index}_SOURCE_MISMATCH"
            )

    return report


def fetch_eagle_items(
    eagle_ids: list[str],
    *,
    api_url: str,
) -> dict[str, dict[str, Any]]:
    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f"{api_url.rstrip('/')}/api/v2/item/get",
            json={
                "ids": eagle_ids,
                "fields": [
                    "id",
                    "name",
                    "ext",
                    "size",
                    "tags",
                    "folders",
                    "url",
                    "annotation",
                    "isDeleted",
                ],
                "offset": 0,
                "limit": max(1000, len(eagle_ids)),
            },
        )
        response.raise_for_status()
        payload = response.json()

    if payload.get("status") != "success":
        raise SourceFinalizeError(
            "EAGLE_READBACK_FAILED"
        )

    return {
        normalized_text(item.get("id")): item
        for item in response_items(payload)
        if normalized_text(item.get("id"))
    }


def table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    return connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone() is not None


def build_finalize_plan(
    *,
    database_path: Path,
    import_report_path: Path,
    source_code: str,
    job_id: str,
    api_url: str,
) -> dict[str, Any]:
    source_code = normalized_text(
        source_code
    ).lower()
    job_id = normalized_text(job_id)

    if not source_code:
        raise SourceFinalizeError(
            "SOURCE_CODE_REQUIRED"
        )

    if not job_id:
        raise SourceFinalizeError(
            "JOB_ID_REQUIRED"
        )

    adapter = get_source_adapter(source_code)

    report = load_import_report(
        import_report_path,
        source_code=source_code,
        job_id=job_id,
    )
    results = report["results"]

    eagle_ids = [
        normalized_text(result["eagle_id"])
        for result in results
    ]

    if len(eagle_ids) != len(set(eagle_ids)):
        raise SourceFinalizeError(
            "DUPLICATE_EAGLE_IDS_IN_REPORT"
        )

    media_ids = [
        normalized_text(result["media_id"])
        for result in results
    ]

    if len(media_ids) != len(set(media_ids)):
        raise SourceFinalizeError(
            "DUPLICATE_MEDIA_IDS_IN_REPORT"
        )

    live_items = fetch_eagle_items(
        eagle_ids,
        api_url=api_url,
    )

    blockers: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    posts: dict[int, dict[str, Any]] = {}

    for result in results:
        eagle_id = normalized_text(
            result["eagle_id"]
        )
        item = live_items.get(eagle_id)

        if item is None:
            blockers.append({
                "code": "EAGLE_ITEM_MISSING",
                "eagle_id": eagle_id,
                "media_id": result["media_id"],
            })
            continue

        if item.get("isDeleted"):
            blockers.append({
                "code": "EAGLE_ITEM_DELETED",
                "eagle_id": eagle_id,
            })
            continue

        if normalized_text(item.get("name")) != normalized_text(
            result["name"]
        ):
            blockers.append({
                "code": "EAGLE_NAME_MISMATCH",
                "eagle_id": eagle_id,
            })

        if normalized_text(item.get("url")) != normalized_text(
            result["post_url"]
        ):
            blockers.append({
                "code": "EAGLE_URL_MISMATCH",
                "eagle_id": eagle_id,
            })

        expected_tags = result.get("tags")

        if not isinstance(expected_tags, list):
            expected_tags = list(
                adapter.default_eagle_tags
            )

        expected_folders = result.get("folders")

        if not isinstance(expected_folders, list):
            expected_folders = list(
                adapter.default_eagle_folder_ids
            )

        live_tags = {
            normalized_text(value)
            for value in (item.get("tags") or [])
        }
        live_folders = {
            normalized_text(value)
            for value in (item.get("folders") or [])
        }

        missing_tags = sorted(
            {
                normalized_text(value)
                for value in expected_tags
                if normalized_text(value)
            }
            - live_tags
        )

        missing_folders = sorted(
            {
                normalized_text(value)
                for value in expected_folders
                if normalized_text(value)
            }
            - live_folders
        )

        if missing_tags:
            blockers.append({
                "code": "EAGLE_TAGS_MISSING",
                "eagle_id": eagle_id,
                "values": missing_tags,
            })

        if missing_folders:
            blockers.append({
                "code": "EAGLE_FOLDERS_MISSING",
                "eagle_id": eagle_id,
                "values": missing_folders,
            })

    database_path = Path(
        database_path
    ).expanduser().resolve()

    if not database_path.is_file():
        raise FileNotFoundError(database_path)

    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row

    try:
        source_row = connection.execute(
            """
            SELECT id
            FROM sources
            WHERE code = ? AND enabled = 1
            """,
            (source_code,),
        ).fetchone()

        if source_row is None:
            raise SourceFinalizeError(
                "SOURCE_NOT_REGISTERED_OR_DISABLED"
            )

        source_id = int(source_row["id"])

        for result in results:
            external_post_id = normalized_text(
                result["post_id"]
            )
            post_url = normalized_text(
                result["post_url"]
            )
            shortcode = normalized_text(
                result.get("shortcode")
            )

            post_rows = connection.execute(
                """
                SELECT
                    id,
                    expected_media_count,
                    status
                FROM posts
                WHERE
                    source_id = ?
                    AND (
                        external_id = ?
                        OR canonical_url = ?
                        OR (
                            ? != ''
                            AND shortcode = ?
                        )
                    )
                """,
                (
                    source_id,
                    external_post_id,
                    post_url,
                    shortcode,
                    shortcode,
                ),
            ).fetchall()

            unique_post_ids = {
                int(row["id"])
                for row in post_rows
            }

            if len(unique_post_ids) != 1:
                blockers.append({
                    "code": (
                        "POST_NOT_FOUND"
                        if not unique_post_ids
                        else "POST_AMBIGUOUS"
                    ),
                    "post_id": external_post_id,
                    "post_url": post_url,
                })
                continue

            database_post_id = next(
                iter(unique_post_ids)
            )
            component_index = int(
                result["component_index"]
            )
            external_media_id = normalized_text(
                result["media_id"]
            )

            media_rows = connection.execute(
                """
                SELECT
                    id,
                    external_media_id,
                    status
                FROM media
                WHERE
                    post_id = ?
                    AND (
                        component_index = ?
                        OR external_media_id = ?
                        OR external_media_id = ?
                    )
                """,
                (
                    database_post_id,
                    component_index,
                    external_media_id,
                    f"{source_code}:{external_media_id}",
                ),
            ).fetchall()

            unique_database_media_ids = {
                int(row["id"])
                for row in media_rows
            }

            if len(unique_database_media_ids) != 1:
                blockers.append({
                    "code": (
                        "MEDIA_NOT_FOUND"
                        if not unique_database_media_ids
                        else "MEDIA_AMBIGUOUS"
                    ),
                    "media_id": external_media_id,
                    "post_id": external_post_id,
                    "component_index": component_index,
                })
                continue

            database_media_id = next(
                iter(unique_database_media_ids)
            )

            existing_rows = connection.execute(
                """
                SELECT
                    media_id,
                    eagle_item_id,
                    status
                FROM eagle_items
                WHERE
                    media_id = ?
                    OR eagle_item_id = ?
                """,
                (
                    database_media_id,
                    normalized_text(
                        result["eagle_id"]
                    ),
                ),
            ).fetchall()

            for existing in existing_rows:
                if (
                    int(existing["media_id"])
                    != database_media_id
                    or normalized_text(
                        existing["eagle_item_id"]
                    )
                    != normalized_text(
                        result["eagle_id"]
                    )
                ):
                    blockers.append({
                        "code": "EAGLE_MAPPING_CONFLICT",
                        "media_id": external_media_id,
                        "eagle_id": result["eagle_id"],
                    })

            post_number = int(
                result["post_number"]
            )
            component_count = int(
                result["component_count"]
            )
            name_marker = normalized_text(
                result.get("name_marker")
            ) or adapter.default_name_marker

            post_state = posts.setdefault(
                database_post_id,
                {
                    "database_post_id": database_post_id,
                    "external_post_id": external_post_id,
                    "post_number": post_number,
                    "component_count": component_count,
                    "name_marker": name_marker,
                    "media_count": 0,
                },
            )

            if (
                post_state["post_number"] != post_number
                or post_state["component_count"]
                != component_count
                or post_state["name_marker"]
                != name_marker
            ):
                blockers.append({
                    "code": "INCONSISTENT_POST_RESULT_STATE",
                    "post_id": external_post_id,
                })

            post_state["media_count"] += 1

            mappings.append({
                "database_post_id": database_post_id,
                "database_media_id": database_media_id,
                "external_post_id": external_post_id,
                "external_media_id": external_media_id,
                "eagle_item_id": normalized_text(
                    result["eagle_id"]
                ),
                "eagle_folder_id": (
                    normalized_text(expected_folders[0])
                    if expected_folders
                    else None
                ),
                "imported_name": normalized_text(
                    result["name"]
                ),
                "imported_url": post_url,
                "tags": [
                    normalized_text(value)
                    for value in expected_tags
                    if normalized_text(value)
                ],
                "post_number": post_number,
                "component_index": component_index,
                "component_count": component_count,
                "name_marker": name_marker,
            })

        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        quick_check = connection.execute(
            "PRAGMA quick_check"
        ).fetchone()[0]

        existing_order_table = table_exists(
            connection,
            "post_import_order",
        )

    finally:
        connection.close()

    if foreign_key_errors:
        blockers.append({
            "code": "DATABASE_FOREIGN_KEY_ERRORS",
            "count": len(foreign_key_errors),
        })

    if quick_check != "ok":
        blockers.append({
            "code": "DATABASE_QUICK_CHECK_FAILED",
            "value": quick_check,
        })

    blocker_counts: dict[str, int] = defaultdict(int)

    for blocker in blockers:
        blocker_counts[
            normalized_text(blocker.get("code"))
        ] += 1

    return {
        "operation": "preview",
        "status": (
            "READY_TO_FINALIZE"
            if not blockers
            else "BLOCKED"
        ),
        "source_code": source_code,
        "job_id": job_id,
        "import_report": str(
            Path(import_report_path).resolve()
        ),
        "database": str(database_path),
        "summary": {
            "report_items": len(results),
            "logical_posts": len(posts),
            "eagle_items_found": len(live_items),
            "resolved_mappings": len(mappings),
            "blockers": len(blockers),
            "blocker_counts": dict(
                sorted(blocker_counts.items())
            ),
            "post_import_order_exists": (
                existing_order_table
            ),
            "eagle_api_read_requests": 1,
            "eagle_api_write_requests": 0,
            "database_modified": False,
        },
        "posts": list(posts.values()),
        "mappings": mappings,
        "blockers": blockers,
    }


def create_generic_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS post_import_order (
            post_id INTEGER PRIMARY KEY,
            source_code TEXT NOT NULL,
            job_id TEXT NOT NULL,
            post_number INTEGER NOT NULL,
            name_marker TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(post_id)
                REFERENCES posts(id) ON DELETE CASCADE,
            UNIQUE(source_code, post_number)
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        post_import_order_job_index
        ON post_import_order(job_id)
        """
    )


def apply_finalize_plan(
    plan: dict[str, Any],
    *,
    database_path: Path,
    commit: bool,
) -> dict[str, Any]:
    if not commit:
        raise SourceFinalizeError(
            "APPLY_REQUIRES_EXPLICIT_COMMIT"
        )

    if plan.get("status") != "READY_TO_FINALIZE":
        raise SourceFinalizeError(
            "BLOCKED_PLAN_CANNOT_BE_APPLIED"
        )

    database_path = Path(
        database_path
    ).expanduser().resolve()

    BACKUPS.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )
    backup_path = (
        BACKUPS
        / (
            "reference_sync_before_generic_finalize_"
            f"{timestamp}.sqlite3"
        )
    )
    shutil.copy2(database_path, backup_path)

    now = utc_now()
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")

    try:
        connection.execute("BEGIN IMMEDIATE")
        create_generic_schema(connection)

        source_code = plan["source_code"]
        posts = plan["posts"]
        mappings = plan["mappings"]

        default_folder = next(
            (
                mapping["eagle_folder_id"]
                for mapping in mappings
                if mapping["eagle_folder_id"]
            ),
            None,
        )

        cursor = connection.execute(
            """
            INSERT INTO import_sessions (
                source_code,
                eagle_folder_id,
                status,
                planned_posts,
                planned_media,
                imported_posts,
                imported_media,
                failed_media,
                created_at,
                started_at,
                finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                source_code,
                default_folder,
                "FINALIZED",
                len(posts),
                len(mappings),
                len(posts),
                len(mappings),
                now,
                now,
                now,
            ),
        )
        session_id = int(cursor.lastrowid)

        for mapping in mappings:
            media_id = int(
                mapping["database_media_id"]
            )

            connection.execute(
                """
                UPDATE media
                SET
                    status = 'IMPORTED',
                    verified_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, media_id),
            )

            existing = connection.execute(
                """
                SELECT id, media_id, eagle_item_id
                FROM eagle_items
                WHERE media_id = ?
                   OR eagle_item_id = ?
                """,
                (
                    media_id,
                    mapping["eagle_item_id"],
                ),
            ).fetchall()

            if existing:
                if any(
                    int(row["media_id"]) != media_id
                    or normalized_text(
                        row["eagle_item_id"]
                    )
                    != mapping["eagle_item_id"]
                    for row in existing
                ):
                    raise SourceFinalizeError(
                        "EAGLE_MAPPING_CHANGED_DURING_APPLY"
                    )

                connection.execute(
                    """
                    UPDATE eagle_items
                    SET
                        import_session_id = ?,
                        eagle_folder_id = ?,
                        imported_name = ?,
                        imported_url = ?,
                        imported_tags_json = ?,
                        status = 'VERIFIED',
                        verified_at = ?
                    WHERE media_id = ?
                    """,
                    (
                        session_id,
                        mapping["eagle_folder_id"],
                        mapping["imported_name"],
                        mapping["imported_url"],
                        json.dumps(
                            mapping["tags"],
                            ensure_ascii=False,
                        ),
                        now,
                        media_id,
                    ),
                )

            else:
                connection.execute(
                    """
                    INSERT INTO eagle_items (
                        media_id,
                        import_session_id,
                        eagle_item_id,
                        eagle_folder_id,
                        imported_name,
                        imported_url,
                        imported_tags_json,
                        status,
                        imported_at,
                        verified_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?,
                        'VERIFIED', ?, ?
                    )
                    """,
                    (
                        media_id,
                        session_id,
                        mapping["eagle_item_id"],
                        mapping["eagle_folder_id"],
                        mapping["imported_name"],
                        mapping["imported_url"],
                        json.dumps(
                            mapping["tags"],
                            ensure_ascii=False,
                        ),
                        now,
                        now,
                    ),
                )

        for post in posts:
            database_post_id = int(
                post["database_post_id"]
            )

            verified_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM media
                JOIN eagle_items
                  ON eagle_items.media_id = media.id
                WHERE
                    media.post_id = ?
                    AND media.status = 'IMPORTED'
                    AND eagle_items.status = 'VERIFIED'
                    AND eagle_items.verified_at IS NOT NULL
                """,
                (database_post_id,),
            ).fetchone()[0]

            expected_count = int(
                post["component_count"]
            )
            post_status = (
                "IMPORTED"
                if verified_count >= expected_count
                else "PARTIALLY_IMPORTED"
            )

            connection.execute(
                """
                UPDATE posts
                SET
                    status = ?,
                    expected_media_count = ?,
                    verified_media_count = ?,
                    updated_at = ?,
                    last_error_code = NULL,
                    last_error_message = NULL
                WHERE id = ?
                """,
                (
                    post_status,
                    expected_count,
                    verified_count,
                    now,
                    database_post_id,
                ),
            )

            existing_order = connection.execute(
                """
                SELECT
                    source_code,
                    post_number,
                    name_marker
                FROM post_import_order
                WHERE post_id = ?
                """,
                (database_post_id,),
            ).fetchone()

            if existing_order is not None and (
                normalized_text(
                    existing_order["source_code"]
                )
                != plan["source_code"]
                or int(
                    existing_order["post_number"]
                )
                != int(post["post_number"])
                or normalized_text(
                    existing_order["name_marker"]
                )
                != normalized_text(
                    post["name_marker"]
                )
            ):
                raise SourceFinalizeError(
                    "POST_ORDER_MAPPING_CONFLICT"
                )

            connection.execute(
                """
                INSERT INTO post_import_order (
                    post_id,
                    source_code,
                    job_id,
                    post_number,
                    name_marker,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    job_id = excluded.job_id,
                    updated_at = excluded.updated_at
                """,
                (
                    database_post_id,
                    plan["source_code"],
                    plan["job_id"],
                    int(post["post_number"]),
                    post["name_marker"],
                    now,
                    now,
                ),
            )

        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        if foreign_key_errors:
            raise SourceFinalizeError(
                "FOREIGN_KEY_ERRORS_AFTER_APPLY"
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    result = dict(plan)
    result["operation"] = "apply"
    result["status"] = "FINALIZED"
    result["database_backup"] = str(
        backup_path
    )
    result["import_session_id"] = session_id
    result["summary"] = dict(plan["summary"])
    result["summary"].update({
        "database_modified": True,
        "database_mappings_written": len(
            plan["mappings"]
        ),
        "post_orders_written": len(
            plan["posts"]
        ),
    })

    return result



def finalize_staging_job(
    *,
    source_code: str,
    job_id: str,
    import_report_path: Path,
    eagle_ids: list[str],
) -> dict[str, Any]:
    """Mark the exact staging job finalized after SQLite commit."""

    job_path = (
        STAGING_ROOT
        / source_code
        / "incoming"
        / job_id
        / "job.json"
    )

    if not job_path.is_file():
        raise SourceFinalizeError(
            f"STAGING_JOB_NOT_FOUND: {job_path}"
        )

    job = json.loads(
        job_path.read_text(encoding="utf-8")
    )

    if not isinstance(job, dict):
        raise SourceFinalizeError(
            "STAGING_JOB_ROOT_IS_NOT_OBJECT"
        )

    if normalized_text(
        job.get("job_id")
    ) != job_id:
        raise SourceFinalizeError(
            "STAGING_JOB_ID_MISMATCH"
        )

    job_source = normalized_text(
        job.get("source_code")
    ).lower()

    if job_source != source_code:
        raise SourceFinalizeError(
            "STAGING_JOB_SOURCE_MISMATCH"
        )

    backup_path = job_path.with_name(
        "job.before_generic_finalize.json"
    )

    if not backup_path.exists():
        shutil.copy2(
            job_path,
            backup_path,
        )

    now = utc_now()
    job["status"] = "IMPORTED_REGISTERED"
    job["finalized_at"] = now
    job["eagle_import_report"] = str(
        Path(import_report_path).resolve()
    )
    job["registered_eagle_ids"] = list(
        eagle_ids
    )

    temporary = job_path.with_suffix(
        ".json.tmp"
    )
    temporary.write_text(
        json.dumps(
            job,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    temporary.replace(job_path)

    return {
        "job_path": str(job_path),
        "job_backup": str(backup_path),
        "job_status": job["status"],
    }

def write_output(
    result: dict[str, Any],
    output_path: Path | None,
) -> None:
    text = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    )

    if output_path is not None:
        output_path = Path(
            output_path
        ).expanduser().resolve()
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        output_path.write_text(
            text + "\n",
            encoding="utf-8",
        )

    print(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=("preview", "apply"),
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE),
    )
    parser.add_argument(
        "--source-code",
        required=True,
    )
    parser.add_argument(
        "--job-id",
        required=True,
    )
    parser.add_argument(
        "--import-report",
        required=True,
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
    )
    parser.add_argument(
        "--output",
        default=None,
    )
    parser.add_argument(
        "--commit",
        action="store_true",
    )
    args = parser.parse_args()

    if args.operation == "preview" and args.commit:
        raise SystemExit(
            "--commit is only valid with apply"
        )

    if args.operation == "apply" and not args.commit:
        raise SystemExit(
            "apply requires --commit"
        )

    plan = build_finalize_plan(
        database_path=Path(args.database),
        import_report_path=Path(
            args.import_report
        ),
        source_code=args.source_code,
        job_id=args.job_id,
        api_url=args.api_url,
    )

    if args.operation == "apply":
        result = apply_finalize_plan(
            plan,
            database_path=Path(args.database),
            commit=args.commit,
        )

        eagle_ids = [
            normalized_text(
                mapping.get("eagle_item_id")
            )
            for mapping in result.get(
                "mappings",
                []
            )
            if normalized_text(
                mapping.get("eagle_item_id")
            )
        ]

        staging_result = finalize_staging_job(
            source_code=result["source_code"],
            job_id=result["job_id"],
            import_report_path=Path(
                result["import_report"]
            ),
            eagle_ids=eagle_ids,
        )
        result["staging_job"] = staging_result

    else:
        result = plan

    write_output(
        result,
        Path(args.output)
        if args.output
        else None,
    )


if __name__ == "__main__":
    main()
