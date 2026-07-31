from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


PROJECT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT / "reports"
INCOMING = (
    PROJECT
    / "downloads"
    / "instagram"
    / "incoming"
)
DATABASE = (
    PROJECT
    / "data"
    / "reference_sync.sqlite3"
)
BACKUPS = PROJECT / "data" / "backups"

API_URL = "http://localhost:41595"
INSTAGRAM_TAG = "Instagram"
INSTAGRAM_FOLDER_ID = "MRWRIOJO42ER5"


def response_items(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    data = payload.get("data", [])

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        nested = data.get("data", [])

        if isinstance(nested, list):
            return nested

        if data.get("id"):
            return [data]

    return []


def latest_import_report() -> Path:
    candidates = sorted(
        REPORTS.glob("eagle_staging_import_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not candidates:
        raise FileNotFoundError(
            "No Eagle staging import report found"
        )

    return candidates[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for block in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def read_eagle_items(
    ids: list[str],
) -> dict[str, dict[str, Any]]:
    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f"{API_URL}/api/v2/item/get",
            json={
                "ids": ids,
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
                "limit": 1000,
            },
        )
        response.raise_for_status()

        payload = response.json()

        if payload.get("status") != "success":
            raise RuntimeError(
                f"Eagle verification failed: {payload}"
            )

    return {
        item["id"]: item
        for item in response_items(payload)
        if item.get("id")
    }


def backup_database() -> Path:
    BACKUPS.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup_path = (
        BACKUPS
        / f"reference_sync_before_finalize_{timestamp}.sqlite3"
    )

    shutil.copy2(DATABASE, backup_path)
    return backup_path


def create_registry_schema(
    connection: sqlite3.Connection,
) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS instagram_sync_jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            discovery_report TEXT,
            staging_directory TEXT,
            import_report TEXT,
            logical_posts INTEGER NOT NULL DEFAULT 0,
            media_components INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            finalized_at TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS instagram_sync_posts (
            post_id TEXT PRIMARY KEY,
            shortcode TEXT,
            canonical_url TEXT NOT NULL,
            owner_username TEXT,
            description TEXT,
            component_count INTEGER NOT NULL DEFAULT 0,
            discovery_status TEXT NOT NULL,
            import_status TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            imported_at TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_instagram_sync_posts_shortcode
        ON instagram_sync_posts(shortcode)
        WHERE shortcode IS NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_instagram_sync_posts_url
        ON instagram_sync_posts(canonical_url);

        CREATE TABLE IF NOT EXISTS instagram_sync_media (
            media_id TEXT PRIMARY KEY,
            post_id TEXT NOT NULL,
            component_index INTEGER,
            extension TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            staging_path TEXT NOT NULL,
            sidecar_path TEXT NOT NULL,
            validation_status TEXT NOT NULL,
            import_status TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            imported_at TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(post_id)
                REFERENCES instagram_sync_posts(post_id)
        );

        CREATE INDEX IF NOT EXISTS
        idx_instagram_sync_media_post_id
        ON instagram_sync_media(post_id);

        CREATE TABLE IF NOT EXISTS instagram_sync_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id TEXT NOT NULL UNIQUE,
            eagle_item_id TEXT NOT NULL UNIQUE,
            job_id TEXT NOT NULL,
            verification_status TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            verified_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(media_id)
                REFERENCES instagram_sync_media(media_id),
            FOREIGN KEY(job_id)
                REFERENCES instagram_sync_jobs(job_id)
        );

        CREATE INDEX IF NOT EXISTS
        idx_instagram_sync_imports_job_id
        ON instagram_sync_imports(job_id);

        CREATE TABLE IF NOT EXISTS
        instagram_sync_post_order (
            post_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            post_number INTEGER NOT NULL,
            order_marker TEXT NOT NULL
                DEFAULT 'instpoporder',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            UNIQUE(job_id, post_number),

            FOREIGN KEY(post_id)
                REFERENCES instagram_sync_posts(post_id),

            FOREIGN KEY(job_id)
                REFERENCES instagram_sync_jobs(job_id)
        );

        CREATE INDEX IF NOT EXISTS
        idx_instagram_sync_post_order_job
        ON instagram_sync_post_order(
            job_id,
            post_number
        );
        """
    )


def main() -> None:
    import_report_path = latest_import_report()

    import_report = json.loads(
        import_report_path.read_text(encoding="utf-8")
    )

    if import_report.get("status") != "BATCH_IMPORTED":
        raise RuntimeError(
            "Latest import report is not BATCH_IMPORTED"
        )

    job_id = str(import_report["job_id"])
    job_path = INCOMING / job_id / "job.json"

    if not job_path.is_file():
        raise FileNotFoundError(job_path)

    job = json.loads(
        job_path.read_text(encoding="utf-8")
    )

    results = import_report.get("results", [])

    if len(results) != import_report.get(
        "planned_items"
    ):
        raise RuntimeError(
            "Import result count does not match plan"
        )

    eagle_ids = [
        str(result["eagle_id"])
        for result in results
    ]

    eagle_items = read_eagle_items(eagle_ids)

    job_records = {
        str(record["media_id"]): record
        for record in job.get("records", [])
        if record.get("media_id")
    }

    verified_results = []
    verification_failures = []

    for result in results:
        eagle_id = str(result["eagle_id"])
        media_id = str(result["media_id"])
        eagle_item = eagle_items.get(eagle_id)
        record = job_records.get(media_id)

        checks = {
            "eagle_item_found": bool(eagle_item),
            "staging_record_found": bool(record),
            "not_deleted": bool(
                eagle_item
                and not eagle_item.get("isDeleted")
            ),
            "name_correct": bool(
                eagle_item
                and eagle_item.get("name")
                == result.get("name")
            ),
            "url_correct": bool(
                eagle_item
                and eagle_item.get("url")
                == result["post_url"]
            ),
            "tag_correct": bool(
                eagle_item
                and INSTAGRAM_TAG
                in (eagle_item.get("tags") or [])
            ),
            "folder_correct": bool(
                eagle_item
                and INSTAGRAM_FOLDER_ID
                in (eagle_item.get("folders") or [])
            ),
            "extension_correct": bool(
                eagle_item
                and str(
                    eagle_item.get("ext") or ""
                ).lower()
                == Path(result["source_path"])
                .suffix.lower()
                .lstrip(".")
            ),
        }

        checks["verified"] = all(checks.values())

        row = {
            "eagle_id": eagle_id,
            "media_id": media_id,
            "post_id": str(result["post_id"]),
            "checks": checks,
        }

        if checks["verified"]:
            verified_results.append(row)
        else:
            verification_failures.append(row)

    if verification_failures:
        print(json.dumps({
            "status": "FINALIZE_ABORTED",
            "reason": (
                "Not all Eagle items passed delayed verification"
            ),
            "verified_items": len(verified_results),
            "verification_failures": verification_failures,
            "database_modified": False,
        }, ensure_ascii=False, indent=2))
        return

    backup_path = backup_database()
    now = datetime.now().isoformat()

    posts_from_job = {
        str(post["post_id"]): post
        for post in job.get("posts", [])
    }

    results_by_post: dict[str, list[dict[str, Any]]] = {}

    for result in results:
        post_id = str(result["post_id"])
        results_by_post.setdefault(post_id, []).append(
            result
        )

    with sqlite3.connect(DATABASE) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        create_registry_schema(connection)

        connection.execute(
            """
            INSERT INTO instagram_sync_jobs (
                job_id,
                status,
                discovery_report,
                staging_directory,
                import_report,
                logical_posts,
                media_components,
                created_at,
                finalized_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                discovery_report =
                    excluded.discovery_report,
                staging_directory =
                    excluded.staging_directory,
                import_report =
                    excluded.import_report,
                logical_posts =
                    excluded.logical_posts,
                media_components =
                    excluded.media_components,
                finalized_at =
                    excluded.finalized_at,
                updated_at =
                    excluded.updated_at
            """,
            (
                job_id,
                "IMPORTED_REGISTERED",
                job.get("source_discovery_report"),
                str(job_path.parent),
                str(import_report_path),
                len(results_by_post),
                len(results),
                job.get("created_at") or now,
                now,
                now,
            ),
        )

        for post_id, post_results in results_by_post.items():
            source_post = posts_from_job.get(
                post_id,
                {},
            )

            first_result = post_results[0]
            first_media_id = str(
                first_result["media_id"]
            )
            first_record = job_records[first_media_id]

            sidecar_path = Path(
                first_record["sidecar_path"]
            )
            metadata = json.loads(
                sidecar_path.read_text(
                    encoding="utf-8"
                )
            )

            username = (
                metadata.get("username")
                or metadata.get("owner_username")
                or source_post.get("username")
            )

            description = (
                metadata.get("description")
                or metadata.get("caption")
                or metadata.get("text")
            )

            total_component_count = int(
                source_post.get(
                    "total_component_count"
                )
                or source_post.get(
                    "component_count_returned"
                )
                or first_result.get(
                    "component_count"
                )
                or len(post_results)
            )

            imported_component_count = len(
                post_results
            )

            post_import_status = (
                "IMPORTED"
                if imported_component_count
                >= total_component_count
                else "PARTIALLY_IMPORTED"
            )

            connection.execute(
                """
                INSERT INTO instagram_sync_posts (
                    post_id,
                    shortcode,
                    canonical_url,
                    owner_username,
                    description,
                    component_count,
                    discovery_status,
                    import_status,
                    first_seen_at,
                    imported_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    shortcode = excluded.shortcode,
                    canonical_url =
                        excluded.canonical_url,
                    owner_username =
                        excluded.owner_username,
                    description =
                        excluded.description,
                    component_count =
                        excluded.component_count,
                    discovery_status =
                        excluded.discovery_status,
                    import_status =
                        excluded.import_status,
                    imported_at =
                        excluded.imported_at,
                    updated_at =
                        excluded.updated_at
                """,
                (
                    post_id,
                    first_result.get("shortcode"),
                    first_result["post_url"],
                    username,
                    description,
                    total_component_count,
                    "DISCOVERED",
                    post_import_status,
                    job.get("created_at") or now,
                    now,
                    now,
                ),
            )

        order_rows_registered = 0

        for post_id, post_results in results_by_post.items():
            first_result = post_results[0]

            post_number = int(
                first_result["post_number"]
            )

            connection.execute(
                """
                INSERT INTO instagram_sync_post_order (
                    post_id,
                    job_id,
                    post_number,
                    order_marker,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)

                ON CONFLICT(post_id) DO UPDATE SET
                    job_id = excluded.job_id,
                    post_number = excluded.post_number,
                    order_marker = excluded.order_marker,
                    updated_at = excluded.updated_at
                """,
                (
                    post_id,
                    job_id,
                    post_number,
                    "instpoporder",
                    now,
                    now,
                ),
            )

            order_rows_registered += 1

        for result in results:
            media_id = str(result["media_id"])
            post_id = str(result["post_id"])
            record = job_records[media_id]

            source_path = Path(record["local_path"])
            sidecar_path = Path(record["sidecar_path"])

            if not source_path.is_file():
                raise FileNotFoundError(source_path)

            current_hash = sha256_file(source_path)

            if (
                record.get("sha256")
                and current_hash
                != record["sha256"]
            ):
                raise RuntimeError(
                    f"Staging file changed: {source_path}"
                )

            connection.execute(
                """
                INSERT INTO instagram_sync_media (
                    media_id,
                    post_id,
                    component_index,
                    extension,
                    file_size,
                    sha256,
                    staging_path,
                    sidecar_path,
                    validation_status,
                    import_status,
                    discovered_at,
                    imported_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(media_id) DO UPDATE SET
                    post_id = excluded.post_id,
                    component_index =
                        excluded.component_index,
                    extension =
                        excluded.extension,
                    file_size =
                        excluded.file_size,
                    sha256 =
                        excluded.sha256,
                    staging_path =
                        excluded.staging_path,
                    sidecar_path =
                        excluded.sidecar_path,
                    validation_status =
                        excluded.validation_status,
                    import_status =
                        excluded.import_status,
                    imported_at =
                        excluded.imported_at,
                    updated_at =
                        excluded.updated_at
                """,
                (
                    media_id,
                    post_id,
                    result.get("component_index"),
                    record["extension"],
                    record["size"],
                    current_hash,
                    str(source_path),
                    str(sidecar_path),
                    "VERIFIED",
                    "IMPORTED",
                    job.get("created_at") or now,
                    now,
                    now,
                ),
            )

            connection.execute(
                """
                INSERT INTO instagram_sync_imports (
                    media_id,
                    eagle_item_id,
                    job_id,
                    verification_status,
                    imported_at,
                    verified_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(media_id) DO UPDATE SET
                    eagle_item_id =
                        excluded.eagle_item_id,
                    job_id =
                        excluded.job_id,
                    verification_status =
                        excluded.verification_status,
                    verified_at =
                        excluded.verified_at,
                    updated_at =
                        excluded.updated_at
                """,
                (
                    media_id,
                    result["eagle_id"],
                    job_id,
                    "VERIFIED",
                    now,
                    now,
                    now,
                    now,
                ),
            )

        connection.commit()

        counts = {
            "jobs": connection.execute(
                "SELECT COUNT(*) "
                "FROM instagram_sync_jobs"
            ).fetchone()[0],
            "posts": connection.execute(
                "SELECT COUNT(*) "
                "FROM instagram_sync_posts"
            ).fetchone()[0],
            "media": connection.execute(
                "SELECT COUNT(*) "
                "FROM instagram_sync_media"
            ).fetchone()[0],
            "imports": connection.execute(
                "SELECT COUNT(*) "
                "FROM instagram_sync_imports"
            ).fetchone()[0],
            "post_order": connection.execute(
                "SELECT COUNT(*) "
                "FROM instagram_sync_post_order"
            ).fetchone()[0],
        }

    job["status"] = "IMPORTED_REGISTERED"
    job["finalized_at"] = now
    job["eagle_import_report"] = str(
        import_report_path
    )
    job["registered_eagle_ids"] = eagle_ids

    job_path.write_text(
        json.dumps(
            job,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report = {
        "status": "FINALIZED",
        "job_id": job_id,
        "eagle_items_verified": len(
            verified_results
        ),
        "posts_registered": len(
            results_by_post
        ),
        "media_registered": len(results),
        "imports_registered": len(results),
        "order_rows_registered": (
            order_rows_registered
        ),
        "registry_totals": counts,
        "database_backup": str(backup_path),
        "database": str(DATABASE),
        "staging_status": "IMPORTED_REGISTERED",
        "safety": {
            "eagle_library_modified": False,
            "eagle_items_created": 0,
            "eagle_items_updated": 0,
            "eagle_items_deleted": 0,
            "media_files_deleted": 0,
            "media_files_moved": 0,
            "database_modified": True,
        },
    }

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    report_path = (
        REPORTS
        / f"instagram_finalize_{timestamp}.json"
    )

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report["report"] = str(report_path)

    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
