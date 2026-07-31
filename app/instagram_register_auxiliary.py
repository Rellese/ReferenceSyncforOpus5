from __future__ import annotations

import json
import re
from datetime import datetime

from app.database import Database, utc_now
from app.settings import Settings


DASH_PATTERN = re.compile(
    r"^(?P<media_id>\d+)\.fdash-(?P<format_id>[^.]+)\.mp4$"
)


def main() -> None:
    settings = Settings.load()
    database = Database(settings.database_path)
    database.initialize()

    root = settings.existing_instagram_source
    now = utc_now()

    discovered = 0
    mapped = 0
    unmapped = 0
    total_size = 0

    with database.session() as connection:
        for path in sorted(root.rglob("*.mp4")):
            match = DASH_PATTERN.match(path.name)

            if not match:
                continue

            discovered += 1
            total_size += path.stat().st_size

            external_media_id = match.group("media_id")

            media_row = connection.execute(
                """
                SELECT id
                FROM media
                WHERE external_media_id = ?
                """,
                (external_media_id,),
            ).fetchone()

            related_media_id = (
                media_row["id"] if media_row else None
            )

            if related_media_id:
                mapped += 1
                status = "AUXILIARY_DASH_STREAM"
            else:
                unmapped += 1
                status = "AUXILIARY_DASH_UNMATCHED"

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
                    file_size = excluded.file_size,
                    import_eligible = 0,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    related_media_id,
                    "instagram",
                    "DASH_VIDEO_FRAGMENT",
                    str(path.resolve()),
                    path.stat().st_size,
                    status,
                    now,
                    now,
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
                "Instagram DASH auxiliary files registered",
                json.dumps(
                    {
                        "discovered": discovered,
                        "mapped": mapped,
                        "unmapped": unmapped,
                        "import_eligible": 0,
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )

    summary = database.summary()

    result = {
        "schema_version": summary["schema_version"],
        "dash_files_registered": discovered,
        "mapped_to_primary_media": mapped,
        "unmapped": unmapped,
        "import_eligible": 0,
        "total_dash_size_bytes": total_size,
        "registry": {
            "logical_posts": summary["posts"],
            "primary_media": summary["media"],
            "auxiliary_files": summary["auxiliary_files"],
            "total_physical_files_accounted": (
                summary["media"]
                + summary["auxiliary_files"]
            ),
        },
        "safety": {
            "source_modified": False,
            "eagle_library_modified": False,
            "files_deleted": 0,
            "files_moved": 0,
            "eagle_items_created": 0,
        },
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = (
        settings.reports_path
        / f"instagram_auxiliary_registry_{timestamp}.json"
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
