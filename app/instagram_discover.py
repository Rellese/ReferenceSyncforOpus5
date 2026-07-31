from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.browser_cookie_source import (
    gallery_dl_browser_spec,
    public_browser_details,
)


PROJECT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT / "config" / "local.json"
REPORTS_PATH = PROJECT / "reports"
DATABASE_PATH = PROJECT / "data" / "reference_sync.sqlite3"

# SMALLEST_INSTAGRAM_PREVIEW_METADATA_V1
PREVIEW_EXTRACTORS_PATH = (
    PROJECT / "app" / "gallery_dl_extractors"
)

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]+$")

SENSITIVE_PATTERNS = [
    re.compile(
        r"(sessionid\s*[=:]\s*)[^;\s,\"']+",
        re.IGNORECASE,
    ),
    re.compile(
        r"(csrftoken\s*[=:]\s*)[^;\s,\"']+",
        re.IGNORECASE,
    ),
    re.compile(
        r"(ds_user_id\s*[=:]\s*)[^;\s,\"']+",
        re.IGNORECASE,
    ),
]


def redact(text: str) -> str:
    result = text

    for pattern in SENSITIVE_PATTERNS:
        result = pattern.sub(r"\1<REDACTED>", result)

    return result


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}

    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def parse_json_stream(text: str) -> list[Any]:
    """
    Parses either:
    - one JSON document;
    - multiple JSON values;
    - JSONL output.
    """
    decoder = json.JSONDecoder()
    values: list[Any] = []
    position = 0
    length = len(text)

    while position < length:
        while position < length and text[position].isspace():
            position += 1

        if position >= length:
            break

        try:
            value, end = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            newline = text.find("\n", position)

            if newline == -1:
                break

            position = newline + 1
            continue

        values.append(value)
        position = end

    return values


def collect_metadata(
    value: Any,
    output: list[dict[str, Any]],
) -> None:
    if isinstance(value, dict):
        if value.get("post_id") or value.get("post_shortcode"):
            output.append(value)

        for child in value.values():
            collect_metadata(child, output)

    elif isinstance(value, list):
        for child in value:
            collect_metadata(child, output)


def normalize_post_id(value: Any) -> str | None:
    if value is None:
        return None

    result = str(value).strip()
    return result or None



def canonical_instagram_url(value: Any) -> str | None:
    text = str(value or "").strip()

    if not text:
        return None

    text = text.split("?", 1)[0].split("#", 1)[0]
    return text.rstrip("/").lower()


def shortcode_from_url(value: Any) -> str | None:
    normalized = canonical_instagram_url(value)

    if not normalized:
        return None

    match = re.search(
        r"instagram\.com/(?:p|reel|tv)/([^/]+)",
        normalized,
        re.IGNORECASE,
    )

    if not match:
        return None

    shortcode = match.group(1).strip()
    return shortcode or None


def load_known_aliases(
    database_path: Path,
) -> tuple[set[str], set[str], set[str]]:
    """
    Return fully known post IDs, shortcodes and canonical URLs.

    This complements the historical post-ID registry. Older
    baseline records and Eagle objects may only have a shortcode
    or Instagram URL.
    """
    post_ids: set[str] = set()
    shortcodes: set[str] = set()
    urls: set[str] = set()

    if not database_path.is_file():
        return post_ids, shortcodes, urls

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table'"
            )
        }

        if "instagram_sync_posts" in tables:
            rows = connection.execute(
                """
                SELECT
                    post_id,
                    shortcode,
                    canonical_url,
                    import_status
                FROM instagram_sync_posts
                """
            ).fetchall()

            for post_id_raw, shortcode_raw, url_raw, status_raw in rows:
                status = str(status_raw or "").strip().upper()

                # Partial publications must remain resumable and
                # must never trigger smart-mode termination.
                if status == "PARTIALLY_IMPORTED":
                    continue

                post_id = normalize_post_id(post_id_raw)

                if post_id:
                    post_ids.add(post_id)

                shortcode = normalize_post_id(shortcode_raw)

                if shortcode:
                    shortcodes.add(shortcode)

                normalized_url = canonical_instagram_url(url_raw)

                if normalized_url:
                    urls.add(normalized_url)

                    url_shortcode = shortcode_from_url(normalized_url)

                    if url_shortcode:
                        shortcodes.add(url_shortcode)

        if "posts" in tables:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(posts)"
                )
            }

            selected = [
                name
                for name in (
                    "external_id",
                    "shortcode",
                    "canonical_url",
                )
                if name in columns
            ]

            if selected:
                sql = (
                    "SELECT "
                    + ", ".join(f'"{name}"' for name in selected)
                    + " FROM posts"
                )

                for row in connection.execute(sql):
                    record = dict(zip(selected, row))

                    post_id = normalize_post_id(
                        record.get("external_id")
                    )

                    if post_id:
                        post_ids.add(post_id)

                    shortcode = normalize_post_id(
                        record.get("shortcode")
                    )

                    if shortcode:
                        shortcodes.add(shortcode)

                    normalized_url = canonical_instagram_url(
                        record.get("canonical_url")
                    )

                    if normalized_url:
                        urls.add(normalized_url)

                        url_shortcode = shortcode_from_url(
                            normalized_url
                        )

                        if url_shortcode:
                            shortcodes.add(url_shortcode)

    return post_ids, shortcodes, urls


def load_eagle_known_aliases(
    partial_urls: set[str],
) -> tuple[set[str], set[str], dict[str, Any]]:
    """
    Read existing numbered Instagram publications from Eagle.

    Eagle remains read-only. Failure to connect does not abort
    discovery; SQLite identities are still used.
    """
    shortcodes: set[str] = set()
    urls: set[str] = set()

    try:
        from app.instagram_order_eagle_scan import (
            scan_order_names,
        )

        scan = scan_order_names()
        rows = scan.get("matched", [])

        if not isinstance(rows, list):
            rows = []

        for row in rows:
            if not isinstance(row, dict):
                continue

            normalized_url = canonical_instagram_url(
                row.get("url")
            )

            if (
                not normalized_url
                or normalized_url in partial_urls
            ):
                continue

            urls.add(normalized_url)

            shortcode = shortcode_from_url(normalized_url)

            if shortcode:
                shortcodes.add(shortcode)

        return shortcodes, urls, {
            "available": True,
            "strategy": scan.get("strategy"),
            "items_received": scan.get("items_received"),
            "numbered_items_found": scan.get(
                "numbered_items_found"
            ),
            "known_urls": len(urls),
            "known_shortcodes": len(shortcodes),
        }

    except Exception as error:
        return shortcodes, urls, {
            "available": False,
            "error": str(error),
            "known_urls": 0,
            "known_shortcodes": 0,
        }


def emit_discovery_progress(
    mode: str,
    elapsed_seconds: int,
    posts_scanned: int,
) -> None:
    if mode == "full":
        stage = "Полное сканирование Saved"
    elif mode == "smart":
        stage = "Поиск новых до известной публикации"
    else:
        stage = "Проверка последних публикаций"

    payload = {
        "percent": 8,
        "stage": stage,
        "indeterminate": True,
        "elapsed_seconds": elapsed_seconds,
        "posts_scanned": max(
            0,
            int(posts_scanned),
        ),
        "current": max(
            0,
            int(posts_scanned),
        ),
        "total": None,
        "search_mode": mode,
    }

    print(
        "RS_PROGRESS "
        + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def run_gallery_dl(
    command: list[str],
    *,
    mode: str,
    scan_speed: str,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    """
    Execute gallery-dl and read its RS_GDL_POST side channel.

    The main --dump-json output is still written to a temporary
    file and parsed after completion, preserving all existing
    discovery metadata and carousel handling.
    """
    marker_prefix = "RS_GDL_POST "

    with tempfile.TemporaryDirectory(
        prefix="reference_sync_discovery_"
    ) as temporary_directory:
        temporary = Path(temporary_directory)
        stdout_path = temporary / "stdout.json"
        stderr_path = temporary / "stderr.txt"

        started = time.monotonic()
        timed_out = False
        posts_scanned = 0

        environment = os.environ.copy()
        environment[
            "REFERENCE_SYNC_DISCOVERY_SPEED"
        ] = str(scan_speed)
        marker_buffer = ""
        stderr_offset = 0

        def read_progress_markers() -> None:
            nonlocal posts_scanned
            nonlocal marker_buffer
            nonlocal stderr_offset

            if not stderr_path.exists():
                return

            try:
                with stderr_path.open("rb") as stream:
                    stream.seek(stderr_offset)
                    chunk = stream.read()
                    stderr_offset = stream.tell()
            except OSError:
                return

            if not chunk:
                return

            marker_buffer += chunk.decode(
                "utf-8",
                errors="replace",
            )

            while "\n" in marker_buffer:
                line, marker_buffer = (
                    marker_buffer.split(
                        "\n",
                        1,
                    )
                )

                stripped = line.strip()

                if not stripped.startswith(
                    marker_prefix
                ):
                    continue

                raw_payload = stripped[
                    len(marker_prefix):
                ]

                try:
                    payload = json.loads(
                        raw_payload
                    )
                    count = int(
                        payload.get("count") or 0
                    )
                except (
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                ):
                    continue

                posts_scanned = max(
                    posts_scanned,
                    count,
                )

        with (
            stdout_path.open("wb") as stdout_file,
            stderr_path.open("wb") as stderr_file,
        ):
            process = subprocess.Popen(
                command,
                cwd=PROJECT,
                stdout=stdout_file,
                stderr=stderr_file,
                env=environment,
            )

            last_emitted_second = -1

            while process.poll() is None:
                read_progress_markers()

                elapsed = int(
                    time.monotonic() - started
                )

                if (
                    last_emitted_second < 0
                    or elapsed - last_emitted_second >= 1
                ):
                    emit_discovery_progress(
                        mode,
                        elapsed,
                        posts_scanned,
                    )
                    last_emitted_second = elapsed

                if elapsed >= timeout_seconds:
                    timed_out = True
                    process.terminate()

                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

                    break

                time.sleep(0.2)

            returncode = process.wait()

        # Consume progress markers written immediately before exit.
        read_progress_markers()

        emit_discovery_progress(
            mode,
            int(time.monotonic() - started),
            posts_scanned,
        )

        stdout_text = stdout_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
        stderr_text = stderr_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        # Do not retain thousands of internal progress records in
        # the diagnostic log. Real gallery-dl warnings remain.
        stderr_text = "\n".join(
            line
            for line in stderr_text.splitlines()
            if not line.strip().startswith(
                marker_prefix
            )
        )

        if timed_out:
            stderr_text += (
                "\nReferenceSync discovery timeout after "
                f"{timeout_seconds} seconds.\n"
            )

            if returncode == 0:
                returncode = 124

        return subprocess.CompletedProcess(
            args=command,
            returncode=returncode,
            stdout=stdout_text,
            stderr=stderr_text,
        )


def build_smart_stop_filter(
    known_post_ids: set[str],
    known_shortcodes: set[str],
) -> str | None:
    """
    gallery-dl's abort() stops pagination as soon as the Saved
    stream reaches a fully known publication.

    Values are JSON-quoted and contain only registry identities;
    no cookie or authentication data is placed in the command.
    """
    clauses = []

    numeric_ids = sorted({
        value
        for value in known_post_ids
        if str(value).isdigit()
    })

    if numeric_ids:
        values = ",".join(
            json.dumps(value)
            for value in numeric_ids
        )
        clauses.append(
            f"str(post_id) not in ({values},)"
        )

    clean_shortcodes = sorted({
        value
        for value in known_shortcodes
        if re.fullmatch(r"[A-Za-z0-9_-]+", value)
    })

    if clean_shortcodes:
        values = ",".join(
            json.dumps(value)
            for value in clean_shortcodes
        )
        clauses.append(
            f"str(post_shortcode) not in ({values},)"
        )

    if not clauses:
        return None

    return "(" + " and ".join(clauses) + ") or abort()"


def load_known_post_ids(
    database_path: Path,
) -> tuple[set[str], dict, dict[str, dict[str, Any]]]:
    """
    Return:
    - post IDs that are completely imported;
    - public diagnostic information;
    - registry state for partially imported posts.
    """
    known: set[str] = set()
    methods: list[str] = []
    registry_state: dict[str, dict[str, Any]] = {}

    if not database_path.is_file():
        return known, {
            "database_exists": False,
            "methods": [],
            "known_post_ids": 0,
            "partial_post_ids": 0,
        }, registry_state

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table'"
            )
        }

        if "instagram_sync_posts" in tables:
            rows = connection.execute(
                """
                SELECT
                    post_id,
                    import_status,
                    component_count
                FROM instagram_sync_posts
                WHERE post_id IS NOT NULL
                """
            ).fetchall()

            for post_id_raw, status_raw, count_raw in rows:
                post_id = normalize_post_id(post_id_raw)

                if not post_id:
                    continue

                status = str(
                    status_raw or ""
                ).strip().upper()

                try:
                    component_count = int(
                        count_raw or 0
                    )
                except (TypeError, ValueError):
                    component_count = 0

                if status == "PARTIALLY_IMPORTED":
                    registry_state[post_id] = {
                        "import_status": status,
                        "component_count": component_count,
                        "imported_component_numbers": [],
                        "imported_media_ids": [],
                        "post_number": None,
                    }
                else:
                    known.add(post_id)

            methods.append(
                "instagram_sync_posts.post_id+import_status"
            )

        if (
            registry_state
            and "instagram_sync_media" in tables
        ):
            placeholders = ",".join(
                "?"
                for _value in registry_state
            )

            rows = connection.execute(
                f"""
                SELECT
                    post_id,
                    media_id,
                    component_index
                FROM instagram_sync_media
                WHERE post_id IN ({placeholders})
                  AND import_status = 'IMPORTED'
                ORDER BY post_id, component_index
                """,
                tuple(registry_state),
            ).fetchall()

            for post_id_raw, media_id_raw, index_raw in rows:
                post_id = normalize_post_id(post_id_raw)

                if post_id not in registry_state:
                    continue

                media_id = normalize_post_id(media_id_raw)

                if (
                    media_id
                    and media_id not in registry_state[
                        post_id
                    ]["imported_media_ids"]
                ):
                    registry_state[
                        post_id
                    ]["imported_media_ids"].append(media_id)

                try:
                    component_index = int(index_raw)
                except (TypeError, ValueError):
                    component_index = None

                if (
                    component_index is not None
                    and component_index > 0
                    and component_index not in registry_state[
                        post_id
                    ]["imported_component_numbers"]
                ):
                    registry_state[
                        post_id
                    ][
                        "imported_component_numbers"
                    ].append(component_index)

            methods.append(
                "instagram_sync_media.component_index"
            )

        if (
            registry_state
            and "instagram_sync_post_order" in tables
        ):
            placeholders = ",".join(
                "?"
                for _value in registry_state
            )

            rows = connection.execute(
                f"""
                SELECT post_id, post_number
                FROM instagram_sync_post_order
                WHERE post_id IN ({placeholders})
                  AND order_marker = 'instpoporder'
                """,
                tuple(registry_state),
            ).fetchall()

            for post_id_raw, number_raw in rows:
                post_id = normalize_post_id(post_id_raw)

                if post_id not in registry_state:
                    continue

                try:
                    post_number = int(number_raw)
                except (TypeError, ValueError):
                    post_number = None

                registry_state[post_id][
                    "post_number"
                ] = post_number

            methods.append(
                "instagram_sync_post_order.post_number"
            )

        legacy_columns_found = []

        if "posts" in tables:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(posts)"
                )
            }

            possible_columns = [
                "external_post_id",
                "post_id",
                "instagram_post_id",
                "external_id",
            ]

            for column in possible_columns:
                if column not in columns:
                    continue

                legacy_columns_found.append(column)

                rows = connection.execute(
                    f'SELECT "{column}" FROM posts '
                    f'WHERE "{column}" IS NOT NULL'
                ).fetchall()

                for row in rows:
                    post_id = normalize_post_id(row[0])

                    if (
                        post_id
                        and post_id not in registry_state
                    ):
                        known.add(post_id)

            if legacy_columns_found:
                methods.append(
                    "posts."
                    + ",".join(legacy_columns_found)
                )

    for state in registry_state.values():
        state[
            "imported_component_numbers"
        ].sort()

    return known, {
        "database_exists": True,
        "methods": methods,
        "legacy_columns_found": legacy_columns_found,
        "known_post_ids": len(known),
        "partial_post_ids": len(registry_state),
    }, registry_state

def classify_failure(
    returncode: int,
    stdout: str,
    stderr: str,
    records_found: int,
) -> dict[str, Any]:
    # gallery-dl --dump-json can encode extraction failures in
    # stdout while still returning process exit code 0.
    lower = f"{stdout}\n{stderr}".lower()

    # Do not classify every occurrence of the digits "429"
    # as a rate limit. Instagram post IDs, media IDs and signed
    # URLs can naturally contain this sequence.
    rate_limit_patterns = (
        r"\bhttp(?:error| error| status)?[: ]+429\b",
        r"\b429\s+(?:too many requests|client error)\b",
        r"\bstatus(?:_code| code)?[\"']?\s*[:=]\s*429\b",
        r"\bresponse(?:_code| code)?[\"']?\s*[:=]\s*429\b",
    )

    rate_limited = (
        "too many requests" in lower
        or "rate limited" in lower
        or "ratelimit" in lower
        or "please wait a few minutes" in lower
        or "temporarily blocked" in lower
        or any(
            re.search(pattern, lower)
            for pattern in rate_limit_patterns
        )
    )

    if rate_limited:
        return {
            "status": "RATE_LIMITED",
            "retryable": True,
            "recommendation": (
                "Stop requests and retry later. "
                "Do not repeatedly restart immediately."
            ),
        }

    if "challenge" in lower or "checkpoint" in lower:
        return {
            "status": "INSTAGRAM_CHALLENGE",
            "retryable": False,
            "recommendation": (
                "Open Instagram in Firefox and complete "
                "the security challenge manually."
            ),
        }

    if (
        "login required" in lower
        or "not logged in" in lower
        or "no session cookies" in lower
        or "session cookie" in lower
        or "accounts/login" in lower
        or "redirect to login page" in lower
        or (
            "abortextraction" in lower
            and "login" in lower
        )
    ):
        return {
            "status": "AUTHENTICATION_REQUIRED",
            "retryable": False,
            "recommendation": (
                "Open Instagram in the browser selected in "
                "ReferenceSync, sign in, verify that Saved "
                "opens, and run the search again."
            ),
        }

    if (
        (returncode != 0 or records_found == 0)
        and "cookie" in lower
        and (
            "error" in lower
            or "failed" in lower
            or "unable" in lower
        )
    ):
        return {
            "status": "FIREFOX_COOKIE_READ_FAILED",
            "retryable": True,
            "recommendation": (
                "Close Firefox completely and retry. "
                "Do not export or send cookies."
            ),
        }

    if returncode != 0:
        return {
            "status": "GALLERY_DL_ERROR",
            "retryable": True,
            "recommendation": (
                "Review the sanitized error report."
            ),
        }

    if records_found == 0:
        return {
            "status": "NO_POSTS_RETURNED",
            "retryable": True,
            "recommendation": (
                "Check the Instagram username and whether "
                "the account has saved posts."
            ),
        }

    return {
        "status": "OK",
        "retryable": False,
        "recommendation": None,
    }


def build_post_records(
    metadata_records: list[dict[str, Any]],
    known_post_ids: set[str],
    known_shortcodes: set[str],
    known_urls: set[str],
    registry_state: dict[
        str,
        dict[str, Any],
    ] | None = None,
) -> list[dict[str, Any]]:
    registry_state = registry_state or {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for metadata in metadata_records:
        post_id = normalize_post_id(metadata.get("post_id"))

        if not post_id:
            continue

        grouped[post_id].append(metadata)

    posts: list[dict[str, Any]] = []

    for post_id, components in grouped.items():
        first = components[0]

        shortcode = (
            first.get("post_shortcode")
            or first.get("sidecar_shortcode")
        )

        post_url = first.get("post_url")

        if not post_url and shortcode:
            post_url = (
                f"https://www.instagram.com/p/{shortcode}/"
            )

        media_ids = []
        extensions = []
        component_numbers = []
        normalized_components = []

        # collect_metadata() also finds the parent post
        # dictionary. It describes the logical publication,
        # not a downloadable carousel component. A real media
        # component must have both media_id and an extension.
        downloadable_components = []

        for component in components:
            component_media_id = normalize_post_id(
                component.get("media_id")
            )
            component_extension = str(
                component.get("extension") or ""
            ).lower().lstrip(".")

            if (
                component_media_id
                and component_extension
            ):
                downloadable_components.append(component)

        # Do not invent a component from the parent metadata.
        # A post without downloadable component metadata is
        # skipped and will be visible in diagnostics instead
        # of producing a false "Медиафайл" row.
        if not downloadable_components:
            continue

        seen_component_keys: set[
            tuple[str, int]
        ] = set()

        for fallback_index, component in enumerate(
            downloadable_components,
            start=1,
        ):
            media_id = normalize_post_id(
                component.get("media_id")
            )

            if media_id and media_id not in media_ids:
                media_ids.append(media_id)

            extension = str(
                component.get("extension") or ""
            ).lower().lstrip(".")

            if extension:
                extensions.append(extension)

            raw_num = component.get("num")

            try:
                component_index = int(raw_num)
            except (TypeError, ValueError):
                component_index = fallback_index

            component_key = (
                str(media_id),
                component_index,
            )

            if component_key in seen_component_keys:
                continue

            seen_component_keys.add(component_key)
            component_numbers.append(component_index)

            if extension in {
                "jpg",
                "jpeg",
                "png",
                "webp",
                "gif",
                "avif",
                "heic",
            }:
                media_type = "image"
            elif extension in {
                "mp4",
                "mov",
                "webm",
                "mkv",
                "m4v",
            }:
                media_type = "video"
            else:
                raw_type = str(
                    component.get("type")
                    or component.get("media_type")
                    or component.get("original_media_type")
                    or ""
                ).lower()

                media_type = (
                    "video"
                    if (
                        "video" in raw_type
                        or raw_type in {"2", "reel"}
                    )
                    else "image"
                    if (
                        "image" in raw_type
                        or raw_type in {"1", "photo"}
                    )
                    else "unknown"
                )

            normalized_components.append({
                "component_index": component_index,
                "media_id": media_id,
                "extension": extension or None,
                "media_type": media_type,
                "preview_url": (
                    component.get("preview_url")
                ),
                "preview_width": (
                    component.get("preview_width")
                ),
                "preview_height": (
                    component.get("preview_height")
                ),
                "preview_candidate_count": (
                    component.get(
                        "preview_candidate_count"
                    )
                ),
                "preview_source": (
                    component.get("preview_source")
                ),
                "selected_by_default": True,
            })

        normalized_components.sort(
            key=lambda item: (
                item["component_index"],
                str(item.get("media_id") or ""),
            )
        )

        partial_state = registry_state.get(
            post_id,
            {},
        )

        imported_component_numbers = sorted({
            int(value)
            for value in partial_state.get(
                "imported_component_numbers",
                [],
            )
            if str(value).isdigit()
            and int(value) > 0
        })

        all_component_numbers = sorted({
            int(component["component_index"])
            for component in normalized_components
        })

        available_component_numbers = [
            component_index
            for component_index in all_component_numbers
            if component_index
            not in imported_component_numbers
        ]

        live_mode = str(
            partial_state.get("live_mode") or ""
        ).strip().upper()

        reimport_deleted_as_new = (
            live_mode == "REIMPORT_DELETED_AS_NEW"
        )
        restore_deleted = False
        resume_partial = (
            live_mode == "RESUME_PARTIAL"
        )

        if reimport_deleted_as_new:
            imported_component_numbers = []
            available_component_numbers = list(
                all_component_numbers
            )

        normalized_shortcode = normalize_post_id(shortcode)
        normalized_url = canonical_instagram_url(post_url)

        identity_is_known = bool(
            post_id in known_post_ids
            or (
                normalized_shortcode
                and normalized_shortcode in known_shortcodes
            )
            or (
                normalized_url
                and normalized_url in known_urls
            )
        )

        if reimport_deleted_as_new:
            status = "NEW_POST_CANDIDATE"
        elif (
            resume_partial
            and not available_component_numbers
        ):
            status = "KNOWN_BASELINE_POST"
        elif resume_partial:
            # A partial carousel must remain available for resume,
            # even if Eagle already contains some components.
            status = "NEW_POST_CANDIDATE"
        else:
            status = (
                "KNOWN_BASELINE_POST"
                if identity_is_known
                else "NEW_POST_CANDIDATE"
            )

        posts.append({
            "post_id": post_id,
            "post_shortcode": shortcode,
            "post_url": post_url,
            "username": first.get("username"),
            "post_date": (
                first.get("post_date")
                or first.get("date")
            ),
            "description": first.get("description"),
            "component_count_returned": len(
                normalized_components
            ),
            "media_ids": media_ids,
            "component_numbers": sorted(
                set(component_numbers)
            ),
            "components": normalized_components,
            # Stable alias consumed by instagram_sync and GUI.
            "component_items": normalized_components,
            "resume_partial": resume_partial,
            "restore_deleted": False,
            "reimport_deleted_as_new": (
                reimport_deleted_as_new
            ),
            "existing_post_number": (
                None
                if reimport_deleted_as_new
                else partial_state.get("post_number")
            ),
            "imported_component_numbers": (
                imported_component_numbers
            ),
            "imported_media_ids": list(
                partial_state.get(
                    "imported_media_ids",
                    [],
                )
            ),
            "available_component_numbers": (
                available_component_numbers
            ),
            "extension_counts": dict(
                sorted(Counter(extensions).items())
            ),
            "discovery_status": status,
        })

    posts.sort(
        key=lambda item: (
            item.get("post_date") is None,
            str(item.get("post_date") or ""),
            item["post_id"],
        ),
        reverse=True,
    )

    return posts


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Instagram saved-post discovery. "
            "No media files are downloaded."
        )
    )
    parser.add_argument(
        "--username",
        required=True,
        help="Your own Instagram username",
    )
    # UNLIMITED_SAVED_RETRIEVAL_V61
    parser.add_argument(
        "--search-mode",
        choices=("recent", "smart", "full"),
        default="recent",
        help=(
            "Saved discovery strategy. Full mode omits "
            "--post-range and follows pagination to the end."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help=(
            "Number of newest saved posts to inspect. "
            "Ignored in full mode."
        ),
    )
    parser.add_argument(
        "--browser",
        default=None,
        help="Browser name for gallery-dl cookies",
    )
    parser.add_argument(
        "--collection",
        action="append",
        default=[],
        metavar="ID:NAME",
        help=(
            "Scan a saved collection instead of the "
            "general saved feed. Can be repeated."
        ),
    )
    parser.add_argument(
        "--scan-speed",
        choices=("safe", "balanced"),
        default="safe",
        help=(
            "Instagram Saved pagination speed. "
            "Balanced reduces randomized request intervals."
        ),
    )
    args = parser.parse_args()

    username = args.username.strip().lstrip("@")

    if not USERNAME_RE.fullmatch(username):
        raise SystemExit("Invalid Instagram username format")

    if args.search_mode in {"full", "smart"}:
        if args.limit < 0:
            raise SystemExit(
                "--limit cannot be negative in unlimited modes"
            )
    elif args.limit < 1 or args.limit > 500:
        raise SystemExit(
            "--limit must be between 1 and 500 "
            "for recent mode"
        )

    config = load_config()
    browser = (
        args.browser
        or config.get("browser")
        or "firefox"
    )

    browser_cookie_source = (
        gallery_dl_browser_spec(browser)
    )
    browser_details = public_browser_details(
        browser
    )

    gallery_dl = shutil.which("gallery-dl")
    if not gallery_dl:
        raise FileNotFoundError(
            "gallery-dl executable not found"
        )

    saved_url = (
        f"https://www.instagram.com/"
        f"{username}/saved/all-posts/"
    )

    collection_targets: list[tuple[str, str, str]] = []

    for entry in args.collection or []:
        raw_id, _, raw_name = str(entry).partition(":")
        collection_id = raw_id.strip()

        if not collection_id:
            continue

        collection_targets.append((
            collection_id,
            raw_name.strip() or collection_id,
            (
                f"https://www.instagram.com/"
                f"{username}/saved/collection/"
                f"{collection_id}/"
            ),
        ))

    # UNLIMITED_SAVED_RETRIEVAL_V61
    command = [
        sys.executable,
        "-m",
        "app.gallery_dl_progress",
        "-X",
        str(PREVIEW_EXTRACTORS_PATH),
        "--config-ignore",
        "--no-input",
        "--cookies-from-browser",
        browser_cookie_source,
        "--simulate",
        "--dump-json",
    ]

    # gallery-dl continues Instagram cursor pagination itself.
    # Omitting --post-range is what makes full Saved discovery
    # unlimited; 50 is not used as a total ceiling.
    if args.search_mode == "recent":
        command.extend([
            "--post-range",
            f"1-{args.limit}",
        ])

    command.extend([
        "--retries",
        "2",
        "--http-timeout",
        "30",
    ])

    (
        known_post_ids,
        database_info,
        registry_state,
    ) = load_known_post_ids(
        DATABASE_PATH
    )

    (
        alias_post_ids,
        known_shortcodes,
        known_urls,
    ) = load_known_aliases(
        DATABASE_PATH
    )

    known_post_ids.update(alias_post_ids)

    # V6.4.6: SQLite stores history; Eagle determines live presence.
    from app.instagram_eagle_reconcile import (
        reconcile_instagram_registry,
    )

    live_registry = reconcile_instagram_registry(
        DATABASE_PATH
    )

    inactive_post_ids = set(
        live_registry.get("inactive_post_ids", set())
    )
    inactive_shortcodes = set(
        live_registry.get("inactive_shortcodes", set())
    )
    inactive_urls = set(
        live_registry.get("inactive_urls", set())
    )

    known_post_ids.difference_update(
        inactive_post_ids
    )
    known_shortcodes.difference_update(
        inactive_shortcodes
    )
    known_urls.difference_update(
        inactive_urls
    )

    # Replace stale SQLite partial state with live Eagle state.
    registry_state = dict(
        live_registry.get("registry_state", {})
    )

    database_info["live_eagle_reconciliation"] = {
        key: value
        for key, value in live_registry.items()
        if key not in {
            "registry_state",
            "inactive_post_ids",
            "inactive_shortcodes",
            "inactive_urls",
        }
    }

    partial_urls = set()

    if DATABASE_PATH.is_file():
        try:
            with sqlite3.connect(DATABASE_PATH) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table'"
                    )
                }

                if "instagram_sync_posts" in tables:
                    rows = connection.execute(
                        """
                        SELECT canonical_url
                        FROM instagram_sync_posts
                        WHERE import_status = 'PARTIALLY_IMPORTED'
                        """
                    ).fetchall()

                    partial_urls = {
                        normalized
                        for row in rows
                        if (
                            normalized
                            := canonical_instagram_url(row[0])
                        )
                    }
        except Exception:
            partial_urls = set()

    # Exclude both live partial and fully deleted publications
    # from Eagle alias re-addition and smart-stop identities.
    partial_urls.update(
        normalized_url
        for state_post_id, state in registry_state.items()
        if state.get("live_mode") in {
            "RESUME_PARTIAL",
            "REIMPORT_DELETED_AS_NEW",
        }
        for normalized_url in [
            next(
                (
                    url
                    for url in inactive_urls
                    if state_post_id in inactive_post_ids
                ),
                None,
            )
        ]
        if normalized_url
    )
    partial_urls.update(inactive_urls)

    (
        eagle_shortcodes,
        eagle_urls,
        eagle_identity_info,
    ) = load_eagle_known_aliases(
        partial_urls
    )

    known_shortcodes.update(eagle_shortcodes)
    known_urls.update(eagle_urls)

    database_info["known_identity_post_ids"] = len(
        known_post_ids
    )
    database_info["known_identity_shortcodes"] = len(
        known_shortcodes
    )
    database_info["known_identity_urls"] = len(
        known_urls
    )
    database_info["eagle_identity_scan"] = (
        eagle_identity_info
    )

    smart_filter_applied = False

    if args.search_mode == "smart":
        smart_filter = build_smart_stop_filter(
            known_post_ids,
            known_shortcodes,
        )

        if smart_filter:
            command.extend([
                "--filter",
                smart_filter,
            ])
            smart_filter_applied = True

    scan_targets = (
        list(collection_targets)
        if collection_targets
        else [(None, None, saved_url)]
    )

    started_at = datetime.now()

    metadata_records: list[dict[str, Any]] = []
    container_map: dict[str, list[dict[str, str]]] = {}
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    scanned_urls: list[str] = []
    last_returncode = 0

    for (
        container_id,
        container_name,
        target_url,
    ) in scan_targets:
        target_command = list(command)
        target_command.append(target_url)
        scanned_urls.append(target_url)

        process = run_gallery_dl(
            target_command,
            mode=args.search_mode,
            scan_speed=args.scan_speed,
            timeout_seconds=(
                2 * 60 * 60
                if args.search_mode in {"full", "smart"}
                else 15 * 60
            ),
        )

        stdout_parts.append(process.stdout)
        stderr_parts.append(process.stderr)

        if process.returncode:
            last_returncode = process.returncode

        target_records: list[dict[str, Any]] = []

        for value in parse_json_stream(process.stdout):
            collect_metadata(value, target_records)

        if container_id is not None:
            for metadata in target_records:
                post_id = normalize_post_id(
                    metadata.get("post_id")
                )

                if not post_id:
                    continue

                entries = container_map.setdefault(
                    post_id,
                    [],
                )

                if any(
                    entry["id"] == container_id
                    for entry in entries
                ):
                    continue

                entries.append({
                    "platform": "instagram",
                    "kind": "collection",
                    "id": container_id,
                    "name": container_name,
                })

        metadata_records.extend(target_records)

    finished_at = datetime.now()

    sanitized_stderr = redact("\n".join(stderr_parts))
    sanitized_stdout = redact("\n".join(stdout_parts))

    # Deduplicate repeated dictionary representations.
    unique_metadata: dict[tuple[str, str, str], dict] = {}

    for metadata in metadata_records:
        key = (
            str(metadata.get("post_id") or ""),
            str(metadata.get("media_id") or ""),
            str(metadata.get("num") or ""),
        )
        unique_metadata[key] = metadata

    metadata_records = list(unique_metadata.values())

    posts = build_post_records(
        metadata_records,
        known_post_ids,
        known_shortcodes,
        known_urls,
        registry_state,
    )

    for post in posts:
        post["containers"] = list(
            container_map.get(
                str(post.get("post_id") or ""),
                [],
            )
        )

    status_info = classify_failure(
        last_returncode,
        sanitized_stdout,
        sanitized_stderr,
        len(posts),
    )

    status_counts = Counter(
        post["discovery_status"]
        for post in posts
    )

    component_count = sum(
        post["component_count_returned"]
        for post in posts
    )

    summary = {
        "status": status_info["status"],
        "retryable": status_info["retryable"],
        "recommendation": status_info["recommendation"],
        "instagram_username": username,
        "saved_url": saved_url,
        "browser": browser,
        "browser_details": browser_details,
        "search_mode": args.search_mode,
        "discovery_speed_profile": args.scan_speed,
        "discovery_request_interval_seconds": (
            [6.0, 12.0]
            if args.scan_speed == "safe"
            else [3.0, 6.0]
        ),
        "saved_page_size_requested": 50,
        "requested_post_limit": (
            args.limit
            if args.search_mode == "recent"
            else None
        ),
        "post_range_applied": (
            args.search_mode == "recent"
        ),
        "smart_stop_filter_applied": (
            smart_filter_applied
        ),
        "smart_stop_strategy": (
            "FIRST_FULLY_KNOWN_POST"
            if smart_filter_applied
            else None
        ),
        "gallery_dl_returncode": last_returncode,
        "container_mode": bool(collection_targets),
        "containers_requested": [
            {
                "id": container_id,
                "name": container_name,
            }
            for container_id, container_name, _ in (
                collection_targets
            )
        ],
        "scanned_urls": scanned_urls,
        "logical_posts_returned": len(posts),
        "media_components_returned": component_count,
        "known_baseline_posts": status_counts.get(
            "KNOWN_BASELINE_POST",
            0,
        ),
        "new_post_candidates": (
            status_counts.get(
                "NEW_POST_CANDIDATE",
                0,
            )
            + status_counts.get(
                "RESTORE_DELETED_POST",
                0,
            )
        ),
        "restore_deleted_posts": status_counts.get(
            "RESTORE_DELETED_POST",
            0,
        ),
        "database": database_info,
        "duration_seconds": round(
            (finished_at - started_at).total_seconds(),
            2,
        ),
        "files_downloaded": 0,
    }

    safety = {
        "simulation_only": True,
        "media_downloaded": 0,
        "source_modified": False,
        "eagle_library_modified": False,
        "database_modified": False,
        "eagle_api_requests": 0,
        "eagle_items_created": 0,
        "cookies_exported": False,
        "cookie_values_written_to_report": False,
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    report_path = (
        REPORTS_PATH
        / f"instagram_discovery_{timestamp}.json"
    )

    posts_path = (
        REPORTS_PATH
        / f"instagram_discovery_posts_{timestamp}.json"
    )

    log_path = (
        REPORTS_PATH
        / f"instagram_discovery_log_{timestamp}.txt"
    )

    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": summary,
        "posts": posts,
        "safety": safety,
    }

    REPORTS_PATH.mkdir(parents=True, exist_ok=True)

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    posts_path.write_text(
        json.dumps(posts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_path.write_text(
        (
            "STDERR\n"
            "======\n"
            f"{sanitized_stderr[-10000:]}\n\n"
            "STDOUT PREVIEW\n"
            "==============\n"
            f"{sanitized_stdout[:5000]}"
        ),
        encoding="utf-8",
    )

    print(
        "RS_PROGRESS "
        + json.dumps(
            {
                "percent": 100,
                "stage": (
                    "Сканирование завершено"
                ),
                "indeterminate": False,
                "current": len(posts),
                "total": len(posts),
                "new_posts": summary[
                    "new_post_candidates"
                ],
                "known_posts": summary[
                    "known_baseline_posts"
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )

    print(json.dumps({
        "summary": summary,
        "new_post_candidates": [
            {
                "post_id": post["post_id"],
                "containers": post.get("containers", []),
                "shortcode": post["post_shortcode"],
                "post_url": post["post_url"],
                "components": post[
                    "component_count_returned"
                ],
                "component_items": post.get(
                    "components",
                    [],
                ),
                "media_ids": post.get(
                    "media_ids",
                    [],
                ),
                "component_numbers": post.get(
                    "component_numbers",
                    [],
                ),
                "resume_partial": post.get(
                    "resume_partial",
                    False,
                ),
                "restore_deleted": False,
                "reimport_deleted_as_new": post.get(
                    "reimport_deleted_as_new",
                    False,
                ),
                "discovery_status": post.get(
                    "discovery_status"
                ),
                "existing_post_number": post.get(
                    "existing_post_number"
                ),
                "imported_component_numbers": post.get(
                    "imported_component_numbers",
                    [],
                ),
                "imported_media_ids": post.get(
                    "imported_media_ids",
                    [],
                ),
                "available_component_numbers": post.get(
                    "available_component_numbers",
                    [],
                ),
                "extensions": post["extension_counts"],
                "username": post["username"],
                "description": (
                    post.get("description") or ""
                ),
            }
            for post in posts
            if post["discovery_status"]
            in {
                "NEW_POST_CANDIDATE",
                "RESTORE_DELETED_POST",
            }
        ],
        "outputs": {
            "report": str(report_path),
            "posts": str(posts_path),
            "sanitized_log": str(log_path),
        },
        "safety": safety,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
