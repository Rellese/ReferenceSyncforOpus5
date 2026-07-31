from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.database import Database, utc_now
from app.settings import Settings


MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
}


def scalar(value: Any) -> str | None:
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text or None
    return None


def first_value(data: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = scalar(data.get(key))
        if value:
            return value
    return None


def load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))

        if not isinstance(payload, dict):
            return None, "JSON root is not an object"

        return payload, None

    except Exception as exc:
        return None, str(exc)


def filename_identity(path: Path) -> tuple[str | None, str | None]:
    stem = path.stem
    parts = stem.split("_", 1)

    post_id = parts[0] if parts[0].isdigit() else None

    if len(parts) == 2:
        component_candidate = parts[1]
        media_id = (
            component_candidate
            if component_candidate.isdigit()
            else None
        )
    else:
        media_id = post_id

    return post_id, media_id


def media_type(path: Path) -> str:
    extension = path.suffix.lower()

    if extension in {".mp4", ".mov", ".m4v", ".webm"}:
        return "video"

    if extension == ".gif":
        return "animation"

    return "image"


def choose_component_indexes(records: list[dict]) -> None:
    used_indexes: set[int] = set()

    records_with_num = []
    records_without_num = []

    for record in records:
        raw_num = record.get("raw_num")

        try:
            component_index = int(raw_num)
        except (TypeError, ValueError):
            component_index = 0

        if component_index > 0 and component_index not in used_indexes:
            record["component_index"] = component_index
            used_indexes.add(component_index)
            records_with_num.append(record)
        else:
            records_without_num.append(record)

    records_without_num.sort(
        key=lambda item: (
            item.get("media_id") or "",
            item["local_path"],
        )
    )

    next_index = 1

    for record in records_without_num:
        while next_index in used_indexes:
            next_index += 1

        record["component_index"] = next_index
        used_indexes.add(next_index)
        next_index += 1


def create_database_backup(database_path: Path) -> str | None:
    if not database_path.exists():
        return None

    backup_directory = database_path.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = (
        backup_directory
        / f"reference_sync_before_baseline_{timestamp}.sqlite3"
    )

    shutil.copy2(database_path, backup_path)
    return str(backup_path)


def scan_source(root: Path) -> dict:
    media_files: list[Path] = []
    json_files: list[Path] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        if path.name.lower().endswith(".json"):
            json_files.append(path)
        elif path.suffix.lower() in MEDIA_EXTENSIONS:
            media_files.append(path)

    media_resolved = {path.resolve() for path in media_files}
    json_resolved = {path.resolve() for path in json_files}

    posts: dict[str, list[dict]] = defaultdict(list)
    damaged_json: list[dict] = []
    media_without_json: list[str] = []

    for media_path in sorted(media_files):
        if ".fdash-" in media_path.name.lower():
            continue

        filename_post_id, filename_media_id = filename_identity(
            media_path
        )

        sidecar_path = Path(f"{media_path}.json")
        metadata = None
        json_error = None

        if sidecar_path.resolve() in json_resolved:
            metadata, json_error = load_json(sidecar_path)

            if json_error:
                damaged_json.append(
                    {
                        "path": str(sidecar_path.relative_to(root)),
                        "error": json_error,
                    }
                )
        else:
            media_without_json.append(
                str(media_path.relative_to(root))
            )

        metadata = metadata or {}

        post_id = (
            first_value(metadata, ("post_id",))
            or filename_post_id
        )

        if not post_id:
            continue

        media_id = (
            first_value(metadata, ("media_id",))
            or filename_media_id
        )

        record = {
            "post_id": post_id,
            "post_shortcode": first_value(
                metadata,
                (
                    "post_shortcode",
                    "sidecar_shortcode",
                ),
            ),
            "post_url": first_value(
                metadata,
                (
                    "post_url",
                    "webpage_url",
                    "permalink",
                ),
            ),
            "post_date": first_value(
                metadata,
                (
                    "post_date",
                    "date",
                ),
            ),
            "description": first_value(
                metadata,
                (
                    "description",
                    "caption",
                    "content",
                ),
            ),
            "author": first_value(
                metadata,
                (
                    "username",
                    "owner_username",
                    "author",
                ),
            ),
            "media_id": media_id,
            "component_shortcode": first_value(
                metadata,
                ("shortcode",),
            ),
            "raw_num": metadata.get("num"),
            "raw_count": metadata.get("count"),
            "media_type": media_type(media_path),
            "source_url": first_value(
                metadata,
                (
                    "video_url",
                    "display_url",
                ),
            ),
            "local_path": str(media_path.resolve()),
            "sidecar_path": (
                str(sidecar_path.resolve())
                if sidecar_path.resolve() in json_resolved
                else None
            ),
            "file_size": media_path.stat().st_size,
            "has_valid_sidecar": (
                sidecar_path.resolve() in json_resolved
                and json_error is None
            ),
        }

        posts[post_id].append(record)

    for records in posts.values():
        choose_component_indexes(records)

    json_without_media = []

    for json_path in json_files:
        media_candidate = Path(str(json_path)[:-5])

        if media_candidate.resolve() not in media_resolved:
            json_without_media.append(
                str(json_path.relative_to(root))
            )

    return {
        "posts": posts,
        "physical_media_files": len(media_files),
        "json_sidecars": len(json_files),
        "media_without_json": media_without_json,
        "json_without_media": json_without_media,
        "damaged_json": damaged_json,
    }


def first_nonempty(records: list[dict], field: str) -> str | None:
    for record in records:
        value = record.get(field)

        if value:
            return value

    return None


def write_baseline(database: Database, scan: dict) -> dict:
    now = utc_now()

    inserted_posts = 0
    inserted_media = 0
    posts_with_missing_sidecars = 0
    count_plus_one_posts = 0
    raw_count_mismatch_posts = 0

    with database.session() as connection:
        source_row = connection.execute(
            "SELECT id FROM sources WHERE code = 'instagram'"
        ).fetchone()

        if not source_row:
            raise RuntimeError("Instagram source is not initialized")

        source_id = source_row["id"]

        for post_id, records in sorted(scan["posts"].items()):
            shortcode = first_nonempty(
                records,
                "post_shortcode",
            )

            post_url = first_nonempty(records, "post_url")

            if not post_url and shortcode:
                post_url = (
                    f"https://www.instagram.com/p/{shortcode}/"
                )

            if not post_url:
                post_url = f"instagram://post/{post_id}"

            description = first_nonempty(
                records,
                "description",
            )

            author = first_nonempty(records, "author")
            published_at = first_nonempty(records, "post_date")

            physical_count = len(records)
            valid_sidecars = sum(
                1
                for record in records
                if record["has_valid_sidecar"]
            )

            raw_counts = {
                int(record["raw_count"])
                for record in records
                if str(record.get("raw_count", "")).isdigit()
            }

            if len(raw_counts) == 1:
                raw_count = next(iter(raw_counts))

                if raw_count == physical_count + 1:
                    count_plus_one_posts += 1
                elif raw_count != physical_count:
                    raw_count_mismatch_posts += 1

            if valid_sidecars == physical_count:
                status = "BASELINE_READY"
            else:
                status = "BASELINE_MEDIA_ONLY"
                posts_with_missing_sidecars += 1

            connection.execute(
                """
                INSERT INTO posts(
                    source_id,
                    external_id,
                    shortcode,
                    canonical_url,
                    original_url,
                    status,
                    description,
                    author,
                    published_at,
                    discovered_at,
                    updated_at,
                    expected_media_count,
                    verified_media_count
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, canonical_url) DO UPDATE SET
                    external_id = excluded.external_id,
                    shortcode = excluded.shortcode,
                    status = excluded.status,
                    description = excluded.description,
                    author = excluded.author,
                    published_at = excluded.published_at,
                    updated_at = excluded.updated_at,
                    expected_media_count =
                        excluded.expected_media_count,
                    verified_media_count =
                        excluded.verified_media_count
                """,
                (
                    source_id,
                    post_id,
                    shortcode,
                    post_url,
                    post_url,
                    status,
                    description,
                    author,
                    published_at,
                    now,
                    now,
                    physical_count,
                    physical_count,
                ),
            )

            post_row = connection.execute(
                """
                SELECT id
                FROM posts
                WHERE source_id = ?
                  AND canonical_url = ?
                """,
                (source_id, post_url),
            ).fetchone()

            database_post_id = post_row["id"]
            inserted_posts += 1

            for record in sorted(
                records,
                key=lambda item: item["component_index"],
            ):
                media_status = (
                    "BASELINE_VERIFIED"
                    if record["has_valid_sidecar"]
                    else "BASELINE_MISSING_SIDECAR"
                )

                connection.execute(
                    """
                    INSERT INTO media(
                        post_id,
                        external_media_id,
                        component_index,
                        media_type,
                        source_url,
                        local_path,
                        sidecar_path,
                        file_size,
                        status,
                        verified_at,
                        created_at,
                        updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(post_id, component_index)
                    DO UPDATE SET
                        external_media_id =
                            excluded.external_media_id,
                        media_type = excluded.media_type,
                        source_url = excluded.source_url,
                        local_path = excluded.local_path,
                        sidecar_path = excluded.sidecar_path,
                        file_size = excluded.file_size,
                        status = excluded.status,
                        verified_at = excluded.verified_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        database_post_id,
                        record["media_id"],
                        record["component_index"],
                        record["media_type"],
                        record["source_url"],
                        record["local_path"],
                        record["sidecar_path"],
                        record["file_size"],
                        media_status,
                        now,
                        now,
                        now,
                    ),
                )

                inserted_media += 1

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
                "Instagram read-only baseline created",
                json.dumps(
                    {
                        "posts": inserted_posts,
                        "media": inserted_media,
                        "source_modified": False,
                        "eagle_library_modified": False,
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )

    return {
        "logical_posts_written": inserted_posts,
        "physical_media_written": inserted_media,
        "posts_with_missing_sidecars": (
            posts_with_missing_sidecars
        ),
        "count_plus_one_posts": count_plus_one_posts,
        "other_raw_count_mismatch_posts": (
            raw_count_mismatch_posts
        ),
    }


def main() -> None:
    settings = Settings.load()
    database = Database(settings.database_path)
    database.initialize()

    backup = create_database_backup(settings.database_path)
    scan = scan_source(settings.existing_instagram_source)
    baseline = write_baseline(database, scan)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = (
        settings.reports_path
        / f"instagram_baseline_{timestamp}.json"
    )

    report = {
        "mode": "READ_SOURCE_WRITE_REGISTRY_ONLY",
        "source_root": str(settings.existing_instagram_source),
        "database": str(settings.database_path),
        "database_backup": backup,
        "scan": {
            "logical_filename_post_groups": len(scan["posts"]),
            "physical_media_files": scan["physical_media_files"],
            "json_sidecars": scan["json_sidecars"],
            "media_without_json": len(
                scan["media_without_json"]
            ),
            "json_without_media": len(
                scan["json_without_media"]
            ),
            "damaged_json": len(scan["damaged_json"]),
        },
        "baseline": baseline,
        "safety": {
            "source_modified": False,
            "eagle_library_modified": False,
            "files_moved": 0,
            "files_deleted": 0,
            "eagle_items_created": 0,
        },
    }

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print()
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
