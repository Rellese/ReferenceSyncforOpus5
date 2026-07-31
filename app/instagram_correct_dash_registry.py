from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from app.database import Database, utc_now
from app.settings import Settings


DASH_PATTERN = re.compile(
    r"^(?:(?P<post_id>\d+)_)?"
    r"(?P<media_id>\d+)"
    r"\.fdash-(?P<format_id>[^.]+)\.mp4$"
)


def backup_database(path: Path) -> Path:
    directory = path.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = (
        directory
        / f"reference_sync_before_dash_fix_{timestamp}.sqlite3"
    )

    shutil.copy2(path, backup)
    return backup


def main() -> None:
    settings = Settings.load()
    database = Database(settings.database_path)
    database.initialize()

    root = settings.existing_instagram_source
    now = utc_now()
    backup = backup_database(settings.database_path)

    dash_files = sorted(
        path
        for path in root.rglob("*.mp4")
        if DASH_PATTERN.match(path.name)
    )

    removed_from_primary = 0
    registered_dash = 0
    dash_with_main_file = 0
    dash_without_main_file = 0
    affected_post_database_ids: set[int] = set()

    with database.session() as connection:
        for dash_path in dash_files:
            match = DASH_PATTERN.match(dash_path.name)

            post_id = match.group("post_id")
            media_id = match.group("media_id")

            primary_row = connection.execute(
                """
                SELECT
                    m.id AS media_database_id,
                    m.post_id AS post_database_id
                FROM media m
                WHERE m.local_path = ?
                """,
                (str(dash_path.resolve()),),
            ).fetchone()

            related_media = connection.execute(
                """
                SELECT m.id
                FROM media m
                JOIN posts p ON p.id = m.post_id
                WHERE m.external_media_id = ?
                  AND m.local_path != ?
                  AND m.local_path NOT LIKE '%.fdash-%'
                  AND (
                      ? IS NULL
                      OR p.external_id = ?
                  )
                LIMIT 1
                """,
                (
                    media_id,
                    str(dash_path.resolve()),
                    post_id,
                    post_id,
                ),
            ).fetchone()

            base_name = (
                f"{post_id}_{media_id}"
                if post_id
                else media_id
            )

            main_candidates = [
                candidate
                for candidate in dash_path.parent.glob(
                    f"{base_name}.*"
                )
                if candidate.is_file()
                and candidate != dash_path
                and ".fdash-" not in candidate.name
                and not candidate.name.endswith(".json")
            ]

            if main_candidates:
                dash_with_main_file += 1
                status = "AUXILIARY_DASH_STREAM"
            else:
                dash_without_main_file += 1
                status = (
                    "AUXILIARY_DASH_VIDEO_ONLY_NO_MERGED_FILE"
                )

            related_media_id = (
                related_media["id"]
                if related_media
                else None
            )

            connection.execute(
                """
                INSERT INTO auxiliary_files(
                    related_media_id,
                    source_code,
                    kind,
                    local_path,
                    file_size,
                    import_eligible,
                    status,
                    discovered_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(local_path) DO UPDATE SET
                    related_media_id =
                        excluded.related_media_id,
                    kind = excluded.kind,
                    file_size = excluded.file_size,
                    import_eligible = 0,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    related_media_id,
                    "instagram",
                    "DASH_VIDEO_FRAGMENT",
                    str(dash_path.resolve()),
                    dash_path.stat().st_size,
                    status,
                    now,
                    now,
                ),
            )

            registered_dash += 1

            if primary_row:
                affected_post_database_ids.add(
                    primary_row["post_database_id"]
                )

                connection.execute(
                    """
                    DELETE FROM media
                    WHERE id = ?
                    """,
                    (primary_row["media_database_id"],),
                )

                removed_from_primary += 1

        info_path = root / "info.json"

        if info_path.exists():
            connection.execute(
                """
                INSERT INTO auxiliary_files(
                    related_media_id,
                    source_code,
                    kind,
                    local_path,
                    file_size,
                    import_eligible,
                    status,
                    discovered_at,
                    updated_at
                )
                VALUES(NULL, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(local_path) DO UPDATE SET
                    kind = excluded.kind,
                    file_size = excluded.file_size,
                    import_eligible = 0,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    "instagram",
                    "POST_METADATA_INFO",
                    str(info_path.resolve()),
                    info_path.stat().st_size,
                    "AUXILIARY_METADATA",
                    now,
                    now,
                ),
            )

        for post_database_id in affected_post_database_ids:
            media_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM media
                WHERE post_id = ?
                """,
                (post_database_id,),
            ).fetchone()["count"]

            missing_sidecars = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM media
                WHERE post_id = ?
                  AND (
                      sidecar_path IS NULL
                      OR status != 'BASELINE_VERIFIED'
                  )
                """,
                (post_database_id,),
            ).fetchone()["count"]

            post_status = (
                "BASELINE_READY"
                if missing_sidecars == 0
                else "BASELINE_INCOMPLETE"
            )

            connection.execute(
                """
                UPDATE posts
                SET
                    status = ?,
                    expected_media_count = ?,
                    verified_media_count = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    post_status,
                    media_count,
                    media_count - missing_sidecars,
                    now,
                    post_database_id,
                ),
            )

        connection.execute(
            """
            INSERT INTO events(
                level,
                category,
                message,
                details_json,
                created_at
            )
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                "INFO",
                "BASELINE",
                "Instagram DASH classification corrected",
                json.dumps(
                    {
                        "dash_registered": registered_dash,
                        "removed_from_primary": (
                            removed_from_primary
                        ),
                        "info_json_registered": (
                            (root / "info.json").exists()
                        ),
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )

    with database.session() as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM posts) AS posts,
                (SELECT COUNT(*) FROM media) AS primary_media,
                (
                    SELECT COUNT(*)
                    FROM auxiliary_files
                    WHERE kind = 'DASH_VIDEO_FRAGMENT'
                ) AS dash_files,
                (
                    SELECT COUNT(*)
                    FROM auxiliary_files
                    WHERE kind = 'POST_METADATA_INFO'
                ) AS info_files,
                (
                    SELECT COUNT(*)
                    FROM media
                    WHERE status != 'BASELINE_VERIFIED'
                ) AS unverified_primary,
                (
                    SELECT COUNT(*)
                    FROM posts
                    WHERE status != 'BASELINE_READY'
                ) AS non_ready_posts,
                (
                    SELECT COUNT(*)
                    FROM eagle_items
                ) AS eagle_items
            """
        ).fetchone()

    result = {
        "database_backup": str(backup),
        "correction": {
            "dash_files_discovered": len(dash_files),
            "dash_removed_from_primary": removed_from_primary,
            "dash_registered_as_auxiliary": registered_dash,
            "dash_with_main_file": dash_with_main_file,
            "dash_without_main_file": dash_without_main_file,
            "info_json_registered_as_auxiliary": (
                (root / "info.json").exists()
            ),
        },
        "registry": {
            "logical_posts": counts["posts"],
            "primary_media": counts["primary_media"],
            "dash_auxiliary_files": counts["dash_files"],
            "info_metadata_files": counts["info_files"],
            "physical_media_accounted": (
                counts["primary_media"]
                + counts["dash_files"]
            ),
            "unverified_primary_media": (
                counts["unverified_primary"]
            ),
            "non_ready_posts": counts["non_ready_posts"],
            "eagle_items_registered": counts["eagle_items"],
        },
        "safety": {
            "source_modified": False,
            "eagle_library_modified": False,
            "physical_files_deleted": 0,
            "physical_files_moved": 0,
            "eagle_items_created": 0,
        },
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = (
        settings.reports_path
        / f"instagram_dash_correction_{timestamp}.json"
    )

    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
