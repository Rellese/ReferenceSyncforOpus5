from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.source_models import NormalizedSourceBundle


class SourceRegistryError(RuntimeError):
    """Raised when normalized source data cannot be registered."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def source_database_id(
    connection: sqlite3.Connection,
    platform: str,
) -> int:
    row = connection.execute(
        """
        SELECT id
        FROM sources
        WHERE code = ?
        """,
        (platform,),
    ).fetchone()

    if row is None:
        raise SourceRegistryError(
            f"Source is not registered: {platform}"
        )

    return int(row["id"])


def existing_container_id(
    connection: sqlite3.Connection,
    source_id: int,
    external_id: str,
) -> int | None:
    row = connection.execute(
        """
        SELECT id
        FROM containers
        WHERE source_id = ?
          AND external_id = ?
        """,
        (source_id, external_id),
    ).fetchone()

    return int(row["id"]) if row else None


def register_containers(
    connection: sqlite3.Connection,
    bundle: NormalizedSourceBundle,
    source_id: int,
    now: str,
    stats: dict[str, int],
) -> dict[str, int]:
    root_external_id = f"root:{bundle.platform}"
    root_name = bundle.platform.replace(
        "_", " "
    ).title()

    root_container_id = existing_container_id(
        connection,
        source_id,
        root_external_id,
    )

    if root_container_id is None:
        cursor = connection.execute(
            """
            INSERT INTO containers (
                source_id,
                external_id,
                parent_container_id,
                container_type,
                original_name,
                display_name,
                metadata_json,
                status,
                discovered_at,
                updated_at
            )
            VALUES (
                ?, ?, NULL, 'ROOT', ?, ?, ?,
                'DISCOVERED', ?, ?
            )
            """,
            (
                source_id,
                root_external_id,
                root_name,
                root_name,
                json_text({
                    "platform": bundle.platform,
                    "virtual": True,
                }),
                now,
                now,
            ),
        )
        root_container_id = int(
            cursor.lastrowid
        )
        stats["containers_inserted"] += 1
    else:
        connection.execute(
            """
            UPDATE containers
            SET
                container_type = 'ROOT',
                original_name = ?,
                display_name = COALESCE(
                    display_name, ?
                ),
                parent_container_id = NULL,
                metadata_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                root_name,
                root_name,
                json_text({
                    "platform": bundle.platform,
                    "virtual": True,
                }),
                now,
                root_container_id,
            ),
        )
        stats["containers_updated"] += 1

    resolved: dict[str, int] = {
        root_external_id: root_container_id,
    }
    pending = list(bundle.containers)

    while pending:
        next_pending = []
        progress = False

        for container in pending:
            external_id = str(container.source_id).strip()

            if not external_id:
                raise SourceRegistryError(
                    "Container has no source ID"
                )

            # Every top-level platform container belongs to
            # the shared platform ROOT. Explicit parents override it.
            parent_id = root_container_id

            if container.parent_source_id:
                parent_external_id = str(
                    container.parent_source_id
                ).strip()

                parent_id = resolved.get(parent_external_id)

                if parent_id is None:
                    parent_id = existing_container_id(
                        connection,
                        source_id,
                        parent_external_id,
                    )

                if parent_id is None:
                    next_pending.append(container)
                    continue

            existing_id = existing_container_id(
                connection,
                source_id,
                external_id,
            )

            metadata = dict(container.metadata or {})
            canonical_url = (
                metadata.get("canonical_url")
                or metadata.get("url")
            )
            privacy = metadata.get("privacy")
            child_count = (
                metadata.get("child_count")
                if metadata.get("child_count") is not None
                else metadata.get("section_count")
            )
            eagle_folder_id = metadata.get(
                "eagle_folder_id"
            )

            if existing_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO containers (
                        source_id,
                        external_id,
                        parent_container_id,
                        container_type,
                        original_name,
                        display_name,
                        canonical_url,
                        privacy,
                        item_count,
                        child_count,
                        eagle_folder_id,
                        metadata_json,
                        status,
                        discovered_at,
                        updated_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'DISCOVERED', ?, ?
                    )
                    """,
                    (
                        source_id,
                        external_id,
                        parent_id,
                        str(container.kind).upper(),
                        container.name,
                        container.name,
                        canonical_url,
                        privacy,
                        container.item_count,
                        child_count,
                        eagle_folder_id,
                        json_text(metadata),
                        now,
                        now,
                    ),
                )
                container_id = int(cursor.lastrowid)
                stats["containers_inserted"] += 1

            else:
                connection.execute(
                    """
                    UPDATE containers
                    SET
                        parent_container_id = ?,
                        container_type = ?,
                        original_name = COALESCE(?, original_name),
                        display_name = COALESCE(display_name, ?),
                        canonical_url = COALESCE(?, canonical_url),
                        privacy = COALESCE(?, privacy),
                        item_count = COALESCE(?, item_count),
                        child_count = COALESCE(?, child_count),
                        eagle_folder_id = COALESCE(
                            ?, eagle_folder_id
                        ),
                        metadata_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        parent_id,
                        str(container.kind).upper(),
                        container.name,
                        container.name,
                        canonical_url,
                        privacy,
                        container.item_count,
                        child_count,
                        eagle_folder_id,
                        json_text(metadata),
                        now,
                        existing_id,
                    ),
                )
                container_id = existing_id
                stats["containers_updated"] += 1

            resolved[external_id] = container_id
            progress = True

        if not progress:
            unresolved = sorted(
                str(item.source_id)
                for item in next_pending
            )
            raise SourceRegistryError(
                "Unresolved parent containers: "
                + ", ".join(unresolved)
            )

        pending = next_pending

    return resolved


def resolve_container(
    connection: sqlite3.Connection,
    source_id: int,
    resolved: dict[str, int],
    external_id: str | None,
) -> int | None:
    normalized = str(external_id or "").strip()

    if not normalized:
        return None

    container_id = resolved.get(normalized)

    if container_id is not None:
        return container_id

    container_id = existing_container_id(
        connection,
        source_id,
        normalized,
    )

    if container_id is None:
        raise SourceRegistryError(
            f"Publication references unknown container: "
            f"{normalized}"
        )

    return container_id


def find_post_id(
    connection: sqlite3.Connection,
    source_id: int,
    external_id: str,
    canonical_url: str,
) -> int | None:
    row = connection.execute(
        """
        SELECT id
        FROM posts
        WHERE source_id = ?
          AND (
              external_id = ?
              OR canonical_url = ?
          )
        ORDER BY
            CASE WHEN external_id = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (
            source_id,
            external_id,
            canonical_url,
            external_id,
        ),
    ).fetchone()

    return int(row["id"]) if row else None


def register_publications(
    connection: sqlite3.Connection,
    bundle: NormalizedSourceBundle,
    source_id: int,
    containers: dict[str, int],
    now: str,
    stats: dict[str, int],
) -> None:
    for publication in bundle.publications:
        external_id = str(publication.source_id).strip()
        canonical_url = str(
            publication.canonical_url
        ).strip()

        if not external_id or not canonical_url:
            raise SourceRegistryError(
                "Publication requires source_id and canonical_url"
            )

        metadata = dict(publication.metadata or {})
        author = (
            metadata.get("author")
            or metadata.get("username")
            or metadata.get("owner_username")
        )

        post_id = find_post_id(
            connection,
            source_id,
            external_id,
            canonical_url,
        )

        if post_id is None:
            cursor = connection.execute(
                """
                INSERT INTO posts (
                    source_id,
                    external_id,
                    shortcode,
                    canonical_url,
                    original_url,
                    status,
                    title,
                    description,
                    author,
                    published_at,
                    discovered_at,
                    updated_at,
                    expected_media_count
                )
                VALUES (
                    ?, ?, ?, ?, ?, 'DISCOVERED',
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    source_id,
                    external_id,
                    metadata.get("shortcode"),
                    canonical_url,
                    canonical_url,
                    publication.title,
                    publication.description,
                    author,
                    publication.created_at,
                    now,
                    now,
                    len(publication.media),
                ),
            )
            post_id = int(cursor.lastrowid)
            stats["posts_inserted"] += 1

        else:
            connection.execute(
                """
                UPDATE posts
                SET
                    external_id = ?,
                    canonical_url = ?,
                    original_url = ?,
                    title = ?,
                    description = ?,
                    author = COALESCE(?, author),
                    published_at = COALESCE(?, published_at),
                    expected_media_count = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    external_id,
                    canonical_url,
                    canonical_url,
                    publication.title,
                    publication.description,
                    author,
                    publication.created_at,
                    len(publication.media),
                    now,
                    post_id,
                ),
            )
            stats["posts_updated"] += 1

        external_container_ids: list[str] = []

        for value in (
            publication.container_id,
            publication.section_id,
            *publication.container_ids,
        ):
            normalized = str(value or "").strip()

            if (
                normalized
                and normalized not in external_container_ids
            ):
                external_container_ids.append(normalized)

        resolved_container_ids = [
            resolve_container(
                connection,
                source_id,
                containers,
                external_id,
            )
            for external_id in external_container_ids
        ]

        resolved_container_ids = [
            container_id
            for container_id in resolved_container_ids
            if container_id is not None
        ]

        relations = [
            (
                container_id,
                (
                    "PRIMARY"
                    if index == len(resolved_container_ids) - 1
                    else "ANCESTOR"
                ),
                (
                    1
                    if index == len(resolved_container_ids) - 1
                    else 0
                ),
            )
            for index, container_id in enumerate(
                resolved_container_ids
            )
        ]

        for container_id, relation_type, is_primary in relations:
            connection.execute(
                """
                INSERT INTO post_containers (
                    post_id,
                    container_id,
                    relation_type,
                    is_primary,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(post_id, container_id)
                DO UPDATE SET
                    relation_type = excluded.relation_type,
                    is_primary = excluded.is_primary
                """,
                (
                    post_id,
                    container_id,
                    relation_type,
                    is_primary,
                    now,
                ),
            )
            stats["relations_upserted"] += 1

        for fallback_index, media in enumerate(
            publication.media,
            start=1,
        ):
            component_index = int(
                media.index or fallback_index
            )

            if component_index < 1:
                raise SourceRegistryError(
                    f"Invalid media index for {external_id}"
                )

            raw_media_id = str(
                media.source_media_id or ""
            ).strip()

            external_media_id = (
                f"{bundle.platform}:{raw_media_id}"
                if raw_media_id
                else None
            )

            existing_media = connection.execute(
                """
                SELECT id
                FROM media
                WHERE post_id = ?
                  AND component_index = ?
                """,
                (post_id, component_index),
            ).fetchone()

            if existing_media is None:
                connection.execute(
                    """
                    INSERT INTO media (
                        post_id,
                        external_media_id,
                        component_index,
                        media_type,
                        source_url,
                        status,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, 'DISCOVERED', ?, ?
                    )
                    """,
                    (
                        post_id,
                        external_media_id,
                        component_index,
                        media.media_type,
                        media.url,
                        now,
                        now,
                    ),
                )
                stats["media_inserted"] += 1

            else:
                connection.execute(
                    """
                    UPDATE media
                    SET
                        external_media_id = COALESCE(
                            ?, external_media_id
                        ),
                        media_type = ?,
                        source_url = COALESCE(?, source_url),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        external_media_id,
                        media.media_type,
                        media.url,
                        now,
                        int(existing_media["id"]),
                    ),
                )
                stats["media_updated"] += 1


def register_bundle(
    database_path: Path,
    bundle: NormalizedSourceBundle,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    if not isinstance(bundle, NormalizedSourceBundle):
        raise TypeError(
            "bundle must be NormalizedSourceBundle"
        )

    database_path = Path(database_path).expanduser().resolve()

    if not database_path.is_file():
        raise FileNotFoundError(database_path)

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")

    stats = {
        "containers_inserted": 0,
        "containers_updated": 0,
        "posts_inserted": 0,
        "posts_updated": 0,
        "media_inserted": 0,
        "media_updated": 0,
        "relations_upserted": 0,
    }

    try:
        connection.execute("BEGIN")
        now = utc_now()

        source_id = source_database_id(
            connection,
            bundle.platform,
        )

        resolved = register_containers(
            connection,
            bundle,
            source_id,
            now,
            stats,
        )

        register_publications(
            connection,
            bundle,
            source_id,
            resolved,
            now,
            stats,
        )

        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        if foreign_key_errors:
            raise SourceRegistryError(
                f"Foreign-key errors: "
                f"{len(foreign_key_errors)}"
            )

        if dry_run:
            connection.rollback()
        else:
            connection.commit()

        return {
            "platform": bundle.platform,
            "dry_run": dry_run,
            **stats,
            "warnings": list(bundle.warnings),
            "foreign_key_errors": 0,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
