from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    external_id TEXT,
    shortcode TEXT,
    canonical_url TEXT NOT NULL,
    original_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DISCOVERED',
    title TEXT,
    description TEXT,
    author TEXT,
    published_at TEXT,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error_code TEXT,
    last_error_message TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    expected_media_count INTEGER,
    verified_media_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(source_id) REFERENCES sources(id),
    UNIQUE(source_id, canonical_url)
);

CREATE UNIQUE INDEX IF NOT EXISTS posts_source_shortcode_unique
ON posts(source_id, shortcode)
WHERE shortcode IS NOT NULL AND shortcode != '';

CREATE UNIQUE INDEX IF NOT EXISTS posts_source_external_id_unique
ON posts(source_id, external_id)
WHERE external_id IS NOT NULL AND external_id != '';

CREATE INDEX IF NOT EXISTS posts_status_index
ON posts(status);

CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    external_media_id TEXT,
    component_index INTEGER NOT NULL DEFAULT 1,
    media_type TEXT,
    source_url TEXT,
    local_path TEXT,
    sidecar_path TEXT,
    file_size INTEGER,
    sha256 TEXT,
    status TEXT NOT NULL DEFAULT 'DISCOVERED',
    verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
    UNIQUE(post_id, component_index)
);

CREATE UNIQUE INDEX IF NOT EXISTS media_external_id_unique
ON media(external_media_id)
WHERE external_media_id IS NOT NULL AND external_media_id != '';

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    source_code TEXT,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT,
    result_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS jobs_status_index
ON jobs(status);

CREATE TABLE IF NOT EXISTS import_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_code TEXT NOT NULL,
    eagle_folder_id TEXT,
    status TEXT NOT NULL DEFAULT 'CREATED',
    planned_posts INTEGER NOT NULL DEFAULT 0,
    planned_media INTEGER NOT NULL DEFAULT 0,
    imported_posts INTEGER NOT NULL DEFAULT 0,
    imported_media INTEGER NOT NULL DEFAULT 0,
    failed_media INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS eagle_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL,
    import_session_id INTEGER,
    eagle_item_id TEXT NOT NULL,
    eagle_folder_id TEXT,
    imported_name TEXT,
    imported_url TEXT,
    imported_tags_json TEXT,
    status TEXT NOT NULL DEFAULT 'IMPORTED',
    imported_at TEXT NOT NULL,
    verified_at TEXT,
    FOREIGN KEY(media_id) REFERENCES media(id),
    FOREIGN KEY(import_session_id) REFERENCES import_sessions(id),
    UNIQUE(media_id),
    UNIQUE(eagle_item_id)
);


CREATE TABLE IF NOT EXISTS auxiliary_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    related_media_id INTEGER,
    source_code TEXT NOT NULL,
    kind TEXT NOT NULL,
    local_path TEXT NOT NULL UNIQUE,
    file_size INTEGER,
    import_eligible INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(related_media_id) REFERENCES media(id)
);

CREATE INDEX IF NOT EXISTS auxiliary_files_kind_index
ON auxiliary_files(kind);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    post_id INTEGER,
    job_id INTEGER,
    details_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(post_id) REFERENCES posts(id),
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS events_created_at_index
ON events(created_at);

CREATE TABLE IF NOT EXISTS containers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    external_id TEXT NOT NULL,
    parent_container_id INTEGER,
    container_type TEXT NOT NULL,
    original_name TEXT,
    display_name TEXT,
    canonical_url TEXT,
    privacy TEXT,
    item_count INTEGER,
    child_count INTEGER,
    eagle_folder_id TEXT,
    metadata_json TEXT,
    status TEXT NOT NULL DEFAULT 'DISCOVERED',
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(source_id) REFERENCES sources(id),
    FOREIGN KEY(parent_container_id)
        REFERENCES containers(id) ON DELETE CASCADE,
    UNIQUE(source_id, external_id),
    CHECK(parent_container_id IS NULL OR parent_container_id != id)
);

CREATE INDEX IF NOT EXISTS containers_parent_index
ON containers(parent_container_id);

CREATE INDEX IF NOT EXISTS containers_source_type_index
ON containers(source_id, container_type);

CREATE UNIQUE INDEX IF NOT EXISTS containers_source_url_unique
ON containers(source_id, canonical_url)
WHERE canonical_url IS NOT NULL AND canonical_url != '';

CREATE TABLE IF NOT EXISTS post_containers (
    post_id INTEGER NOT NULL,
    container_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'MEMBER',
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY(post_id, container_id),
    FOREIGN KEY(post_id)
        REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY(container_id)
        REFERENCES containers(id) ON DELETE CASCADE,
    CHECK(is_primary IN (0, 1))
);

CREATE INDEX IF NOT EXISTS post_containers_container_index
ON post_containers(container_id);

CREATE INDEX IF NOT EXISTS post_containers_relation_index
ON post_containers(relation_type);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        now = utc_now()

        with self.session() as connection:
            connection.executescript(SCHEMA)

            connection.execute(
                """
                INSERT INTO schema_meta(key, value)
                VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

            connection.execute(
                """
                INSERT INTO schema_meta(key, value)
                VALUES('initialized_at', ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (now,),
            )

            for code, name in (
                ("instagram", "Instagram"),
                ("pinterest", "Pinterest"),
                ("behance", "Behance"),
                ("dribbble", "Dribbble"),
            ):
                connection.execute(
                    """
                    INSERT INTO sources(
                        code, name, enabled, created_at, updated_at
                    )
                    VALUES(?, ?, 1, ?, ?)
                    ON CONFLICT(code) DO UPDATE SET
                        name = excluded.name,
                        updated_at = excluded.updated_at
                    """,
                    (code, name, now, now),
                )

            connection.execute(
                """
                INSERT INTO events(level, category, message, created_at)
                VALUES('INFO', 'SYSTEM', 'Database initialized', ?)
                """,
                (now,),
            )

    def summary(self) -> dict:
        with self.session() as connection:
            sources = connection.execute(
                "SELECT COUNT(*) AS count FROM sources"
            ).fetchone()["count"]

            posts = connection.execute(
                "SELECT COUNT(*) AS count FROM posts"
            ).fetchone()["count"]

            media = connection.execute(
                "SELECT COUNT(*) AS count FROM media"
            ).fetchone()["count"]

            jobs = connection.execute(
                "SELECT COUNT(*) AS count FROM jobs"
            ).fetchone()["count"]

            eagle_items = connection.execute(
                "SELECT COUNT(*) AS count FROM eagle_items"
            ).fetchone()["count"]

            auxiliary_files = connection.execute(
                "SELECT COUNT(*) AS count FROM auxiliary_files"
            ).fetchone()["count"]

            containers = connection.execute(
                "SELECT COUNT(*) AS count FROM containers"
            ).fetchone()["count"]

            post_containers = connection.execute(
                "SELECT COUNT(*) AS count FROM post_containers"
            ).fetchone()["count"]

            return {
                "schema_version": SCHEMA_VERSION,
                "sources": sources,
                "posts": posts,
                "media": media,
                "jobs": jobs,
                "eagle_items": eagle_items,
                "auxiliary_files": auxiliary_files,
                "containers": containers,
                "post_containers": post_containers,
            }
