from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.settings import Settings


MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp",
    ".gif", ".mp4", ".mov", ".m4v", ".webm",
}


def load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def main() -> None:
    settings = Settings.load()
    root = settings.existing_instagram_source

    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row

    primary_media = connection.execute(
        """
        SELECT
            m.id,
            m.external_media_id,
            m.component_index,
            m.local_path,
            m.sidecar_path,
            m.file_size,
            m.status,
            p.external_id AS post_id,
            p.shortcode AS post_shortcode,
            p.canonical_url,
            p.status AS post_status
        FROM media m
        JOIN posts p ON p.id = m.post_id
        ORDER BY p.external_id, m.component_index
        """
    ).fetchall()

    missing_physical_files = []
    missing_sidecars = []

    for row in primary_media:
        record = dict(row)
        media_path = Path(record["local_path"])

        if not media_path.exists():
            missing_physical_files.append(record)

        sidecar_path = record.get("sidecar_path")

        if not sidecar_path or not Path(sidecar_path).exists():
            missing_sidecars.append(record)

    physical_media = {
        path.resolve()
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in MEDIA_EXTENSIONS
    }

    json_files = {
        path.resolve()
        for path in root.rglob("*.json")
        if path.is_file()
    }

    orphan_json = []

    for json_path in sorted(json_files):
        expected_media = Path(str(json_path)[:-5]).resolve()

        if expected_media in physical_media:
            continue

        metadata = load_json(json_path)

        orphan_json.append(
            {
                "json": str(json_path.relative_to(root)),
                "expected_media": str(
                    expected_media.relative_to(root)
                ),
                "post_id": metadata.get("post_id"),
                "post_shortcode": metadata.get(
                    "post_shortcode"
                ),
                "post_url": metadata.get("post_url"),
                "media_id": metadata.get("media_id"),
                "component_shortcode": metadata.get(
                    "shortcode"
                ),
                "num": metadata.get("num"),
                "count": metadata.get("count"),
            }
        )

    post_statuses = {
        row["status"]: row["count"]
        for row in connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM posts
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
    }

    media_statuses = {
        row["status"]: row["count"]
        for row in connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM media
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
    }

    auxiliary_statuses = {
        row["status"]: row["count"]
        for row in connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM auxiliary_files
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
    }

    counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM posts) AS posts,
            (SELECT COUNT(*) FROM media) AS primary_media,
            (
                SELECT COUNT(*)
                FROM auxiliary_files
            ) AS auxiliary_files,
            (
                SELECT COUNT(*)
                FROM eagle_items
            ) AS eagle_items
        """
    ).fetchone()

    connection.close()

    result = {
        "summary": {
            "logical_posts": counts["posts"],
            "primary_media": counts["primary_media"],
            "auxiliary_files": counts["auxiliary_files"],
            "total_physical_accounted": (
                counts["primary_media"]
                + counts["auxiliary_files"]
            ),
            "missing_physical_primary_files": len(
                missing_physical_files
            ),
            "primary_media_without_sidecar": len(
                missing_sidecars
            ),
            "orphan_json_without_media": len(orphan_json),
            "eagle_items_registered": counts["eagle_items"],
        },
        "statuses": {
            "posts": post_statuses,
            "primary_media": media_statuses,
            "auxiliary_files": auxiliary_statuses,
        },
        "primary_media_without_sidecar": [
            {
                "file": str(
                    Path(row["local_path"]).relative_to(root)
                ),
                "size": row["file_size"],
                "post_id": row["post_id"],
                "post_shortcode": row["post_shortcode"],
                "post_url": row["canonical_url"],
                "component_index": row["component_index"],
                "external_media_id": row["external_media_id"],
                "status": row["status"],
            }
            for row in missing_sidecars
        ],
        "orphan_json_without_media": orphan_json,
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
        / f"instagram_baseline_audit_{timestamp}.json"
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
