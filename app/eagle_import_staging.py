from __future__ import annotations

from app.source_adapter import get_source_adapter
from app.source_staging_contract import ensure_registered_staging_contract

from app.source_resume_registry import (
    registered_resume_state as generic_registered_resume_state,
)

import argparse
import importlib
import hashlib
import json
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


PROJECT = Path(__file__).resolve().parents[1]
STAGING_ROOT = PROJECT / "downloads"

# Compatibility alias for older Instagram-specific helpers.
INCOMING = (
    STAGING_ROOT
    / "instagram"
    / "incoming"
)
REPORTS = PROJECT / "reports"
DATABASE = PROJECT / "data" / "reference_sync.sqlite3"

API_URL = "http://localhost:41595"
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for block in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def latest_ready_job(
    job_id: str | None = None,
    source_code: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    normalized_job_id = str(job_id or "").strip()
    normalized_source = str(
        source_code or ""
    ).strip().lower()

    if normalized_job_id and (
        "/" in normalized_job_id
        or "\\" in normalized_job_id
        or normalized_job_id in {".", ".."}
    ):
        raise ValueError("Invalid staging job ID")

    if normalized_source and (
        not normalized_source.replace(
            "_", ""
        ).replace("-", "").isalnum()
    ):
        raise ValueError("Invalid staging source code")

    if normalized_job_id:
        candidates = list(
            STAGING_ROOT.glob(
                f"*/incoming/{normalized_job_id}/job.json"
            )
        )
    else:
        candidates = list(
            STAGING_ROOT.glob(
                "*/incoming/*/job.json"
            )
        )

    candidates = sorted(
        candidates,
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    matches = []

    for job_path in candidates:
        try:
            job = json.loads(
                job_path.read_text(encoding="utf-8")
            )
        except Exception:
            continue

        if not isinstance(job, dict):
            continue

        if job.get("status") != "STAGING_READY":
            continue

        path_source = job_path.parents[2].name.lower()
        job_source = str(
            job.get("source_code")
            or path_source
        ).strip().lower()

        if (
            normalized_source
            and job_source != normalized_source
        ):
            continue

        payload_job_id = str(
            job.get("job_id") or ""
        ).strip()

        if (
            normalized_job_id
            and payload_job_id != normalized_job_id
        ):
            continue

        matches.append((job_path, job))

    if normalized_job_id:
        if not matches:
            raise FileNotFoundError(
                "STAGING_READY job not found: "
                f"{normalized_source or '*'}"
                f"/{normalized_job_id}"
            )

        if len(matches) != 1:
            raise RuntimeError(
                "Staging job ID is ambiguous across sources: "
                f"{normalized_job_id}"
            )

        return matches[0]

    if matches:
        return matches[0]

    raise FileNotFoundError(
        "No STAGING_READY source job found"
    )


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


def find_created_id(value: Any) -> str | None:
    if isinstance(value, dict):
        item_id = value.get("id")

        if isinstance(item_id, str) and item_id:
            return item_id

        for child in value.values():
            result = find_created_id(child)

            if result:
                return result

    elif isinstance(value, list):
        for child in value:
            result = find_created_id(child)

            if result:
                return result

    return None


def query_by_url(
    client: httpx.Client,
    url: str,
) -> list[dict[str, Any]]:
    response = client.post(
        f"{API_URL}/api/v2/item/get",
        json={
            "url": url,
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
            "limit": 100,
        },
    )
    response.raise_for_status()

    payload = response.json()

    if payload.get("status") != "success":
        raise RuntimeError(
            f"Eagle URL query failed: {payload}"
        )

    return [
        item
        for item in response_items(payload)
        if not item.get("isDeleted")
        and item.get("url") == url
    ]


def read_created_item(
    client: httpx.Client,
    item_id: str,
) -> dict[str, Any] | None:
    delays = [0.5, 1.0, 2.0, 4.0]

    for delay in delays:
        time.sleep(delay)

        response = client.post(
            f"{API_URL}/api/v2/item/get",
            json={
                "ids": [item_id],
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
                "limit": 10,
            },
        )
        response.raise_for_status()

        payload = response.json()
        items = response_items(payload)

        for item in items:
            if item.get("id") == item_id:
                return item

    return None


def first_value(
    metadata: dict[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        value = metadata.get(key)

        if value not in (None, "", [], {}):
            return value

    return None



def normalized_staging_metadata(
    *,
    source_code: str,
    metadata: dict[str, Any],
    record: dict[str, Any],
    source_path: Path,
) -> dict[str, Any]:
    """Apply a source normalizer to one staging sidecar.

    New adapters expose ``staging_metadata_from_sidecar``. Adapters
    without that helper retain compatibility with their existing
    already-normalized staging sidecars.
    """

    adapter = get_source_adapter(source_code)
    module = importlib.import_module(
        adapter.normalizer_module
    )
    normalizer = getattr(
        module,
        "staging_metadata_from_sidecar",
        None,
    )

    if normalizer is None:
        return dict(metadata)

    normalized = normalizer(
        metadata,
        local_filename=source_path.name,
    )

    if not isinstance(normalized, dict):
        raise RuntimeError(
            f"{source_code} staging normalizer "
            "must return a dictionary"
        )

    merged = dict(metadata)
    merged.update(normalized)

    record_container_ids = record.get(
        "container_ids"
    )

    if (
        isinstance(record_container_ids, list)
        and not merged.get("container_ids")
    ):
        merged["container_ids"] = list(
            record_container_ids
        )

    return merged


def resolve_job_eagle_folder_ids(
    job: dict[str, Any],
    *,
    database_path: Path = DATABASE,
) -> dict[str, Any]:
    """Resolve an empty Eagle folder list from the deepest container.

    Existing explicit folder IDs are preserved for legacy jobs.
    SQLite is opened read-only. Missing mappings block import instead
    of allowing items to fall into the Eagle library root.
    """

    eagle = job.get("eagle")

    if not isinstance(eagle, dict):
        raise RuntimeError(
            "STAGING_JOB_HAS_NO_EAGLE_SETTINGS"
        )

    existing_folder_ids = [
        str(value).strip()
        for value in eagle.get(
            "folder_ids",
            [],
        )
        if str(value).strip()
    ]

    if existing_folder_ids:
        eagle["folder_ids"] = existing_folder_ids
        return job

    source_code = str(
        job.get("source_code") or ""
    ).strip().lower()

    container_chain = [
        str(value).strip()
        for value in job.get(
            "container_chain",
            [],
        )
        if str(value).strip()
    ]

    if not source_code:
        raise RuntimeError(
            "STAGING_JOB_HAS_NO_SOURCE_CODE"
        )

    if not container_chain:
        raise RuntimeError(
            "STAGING_JOB_HAS_NO_CONTAINER_CHAIN"
        )

    target_external_id = container_chain[-1]
    database_path = Path(
        database_path
    ).expanduser().resolve()

    if not database_path.is_file():
        raise RuntimeError(
            f"REGISTRY_DATABASE_NOT_FOUND: "
            f"{database_path}"
        )

    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row

    try:
        rows = connection.execute(
            """
            SELECT
                containers.id,
                containers.external_id,
                containers.container_type,
                containers.eagle_folder_id
            FROM containers
            JOIN sources
              ON sources.id = containers.source_id
            WHERE
                sources.code = ?
                AND containers.external_id = ?
            """,
            (
                source_code,
                target_external_id,
            ),
        ).fetchall()

    finally:
        connection.close()

    if not rows:
        raise RuntimeError(
            "TARGET_CONTAINER_NOT_REGISTERED: "
            f"{source_code}:{target_external_id}"
        )

    if len(rows) != 1:
        raise RuntimeError(
            "TARGET_CONTAINER_MAPPING_AMBIGUOUS: "
            f"{source_code}:{target_external_id}"
        )

    eagle_folder_id = str(
        rows[0]["eagle_folder_id"] or ""
    ).strip()

    if not eagle_folder_id:
        raise RuntimeError(
            "TARGET_CONTAINER_HAS_NO_EAGLE_FOLDER_ID: "
            f"{source_code}:{target_external_id}"
        )

    eagle["folder_ids"] = [eagle_folder_id]
    job["eagle"] = eagle
    job["resolved_eagle_folder"] = {
        "container_external_id": (
            target_external_id
        ),
        "container_type": str(
            rows[0]["container_type"]
        ),
        "eagle_folder_id": eagle_folder_id,
        "resolution": "CONTAINER_REGISTRY",
    }

    return job


def next_registered_post_number(
    source_code: str,
    *,
    database_path: Path = DATABASE,
) -> int:
    """Return the next source-specific import number read-only."""

    source_code = str(
        source_code or ""
    ).strip().lower()

    if not source_code:
        raise ValueError(
            "source_code is required"
        )

    database_path = Path(
        database_path
    ).expanduser().resolve()

    if not database_path.is_file():
        return 1

    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
    )

    try:
        existing_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name IN (
                      'post_import_order',
                      'source_order_counters'
                  )
                """
            )
        }

        registered_maximum = 0
        counter_maximum = 0

        if "post_import_order" in existing_tables:
            row = connection.execute(
                """
                SELECT COALESCE(
                    MAX(post_number),
                    0
                )
                FROM post_import_order
                WHERE lower(source_code) = ?
                """,
                (source_code,),
            ).fetchone()

            registered_maximum = int(
                row[0] or 0
            )

        if "source_order_counters" in existing_tables:
            row = connection.execute(
                """
                SELECT COALESCE(
                    MAX(last_number),
                    0
                )
                FROM source_order_counters
                WHERE lower(source_code) = ?
                """,
                (source_code,),
            ).fetchone()

            counter_maximum = int(
                row[0] or 0
            )

    finally:
        connection.close()

    number = max(
        registered_maximum,
        counter_maximum,
    ) + 1

    if number < 1:
        raise RuntimeError(
            "Invalid next post number"
        )

    return number

def build_plan(
    job: dict[str, Any],
    start_number: int = 1,
    naming_manifest: dict[str, Any] | None = None,
    *,
    database_path: Path = DATABASE,
) -> list[dict[str, Any]]:
    if start_number < 1:
        raise ValueError("start_number must be at least 1")

    job = ensure_registered_staging_contract(job)
    job = resolve_job_eagle_folder_ids(
        job,
        database_path=database_path,
    )

    source_code = job["source_code"]
    eagle_settings = job["eagle"]
    eagle_tags = list(eagle_settings["tags"])
    eagle_folder_ids = list(
        eagle_settings["folder_ids"]
    )
    name_marker = str(
        eagle_settings["name_marker"]
    )

    plan = []

    for record in job.get("records", []):
        source_path = Path(record["local_path"])
        sidecar_path = Path(record["sidecar_path"])

        if not source_path.is_file():
            raise FileNotFoundError(source_path)

        if not sidecar_path.is_file():
            raise FileNotFoundError(sidecar_path)

        metadata = json.loads(
            sidecar_path.read_text(encoding="utf-8")
        )

        if not isinstance(metadata, dict):
            raise RuntimeError(
                f"Sidecar root must be an object: "
                f"{sidecar_path}"
            )

        metadata = normalized_staging_metadata(
            source_code=source_code,
            metadata=metadata,
            record=record,
            source_path=source_path,
        )

        post_id = str(
            metadata.get("post_id")
            or record.get("post_id")
            or ""
        ).strip()

        media_id = str(
            metadata.get("media_id")
            or record.get("media_id")
            or ""
        ).strip()

        shortcode = str(
            metadata.get("post_shortcode")
            or metadata.get("sidecar_shortcode")
            or record.get("post_shortcode")
            or ""
        ).strip()

        post_url = first_value(
            metadata,
            "canonical_url",
            "post_url",
            "url",
        )

        if not post_url and shortcode:
            post_url = (
                f"https://www.instagram.com/p/"
                f"{shortcode}/"
            )

        username = first_value(
            metadata,
            "username",
            "owner_username",
            "account",
            "user",
        )

        description = first_value(
            metadata,
            "description",
            "caption",
            "text",
        )

        raw_num = first_value(
            metadata,
            "component_index",
            "num",
        )

        if raw_num is None:
            raw_num = record.get(
                "component_index"
            )

        try:
            component_index = int(raw_num)
        except (TypeError, ValueError):
            component_index = 1

        normalized_display_name = first_value(
            metadata,
            "eagle_name",
            "display_name",
            "title",
        )

        name = (
            str(normalized_display_name)
            if normalized_display_name
            else (
                f"@{str(username).lstrip('@')}"
                if username
                else source_path.stem
            )
        )

        current_hash = sha256_file(source_path)

        if (
            record.get("sha256")
            and current_hash != record["sha256"]
        ):
            raise RuntimeError(
                f"Staging file changed: {source_path}"
            )

        if not post_id or not media_id or not post_url:
            raise RuntimeError(
                f"Incomplete metadata: {source_path}"
            )

        plan.append({
            "post_id": post_id,
            "media_id": media_id,
            "shortcode": shortcode,
            "post_url": post_url,
            "component_index": component_index,
            "total_component_count": (
                int(
                    metadata.get(
                        "total_component_count"
                    )
                )
                if metadata.get(
                    "total_component_count"
                ) is not None
                else None
            ),
            "name": name,
            "annotation": description or "",
            "source_code": source_code,
            "tags": list(eagle_tags),
            "folders": list(eagle_folder_ids),
            "name_marker": name_marker,
            "extension": source_path.suffix.lower().lstrip("."),
            "size": source_path.stat().st_size,
            "sha256": current_hash,
            "source_path": str(source_path),
            "sidecar_path": str(sidecar_path),
        })

    # Назначаем номера только тем публикациям,
    # которые реально вошли в текущую пачку.
    present_post_ids = {
        str(item["post_id"])
        for item in plan
    }

    ordered_post_ids: list[str] = []
    seen_post_ids: set[str] = set()

    for post in job.get("posts", []):
        post_id = str(
            post.get("post_id") or ""
        ).strip()

        if (
            post_id
            and post_id in present_post_ids
            and post_id not in seen_post_ids
        ):
            seen_post_ids.add(post_id)
            ordered_post_ids.append(post_id)

    # Резервный вариант для записей, отсутствующих
    # в job["posts"]: сохраняем первый порядок media.
    for item in plan:
        post_id = str(item["post_id"])

        if post_id not in seen_post_ids:
            seen_post_ids.add(post_id)
            ordered_post_ids.append(post_id)

    post_number_by_id = {
        post_id: number
        for number, post_id in enumerate(
            ordered_post_ids,
            start=start_number,
        )
    }

    component_count_by_post: dict[str, int] = {}

    for item in plan:
        post_id = str(item["post_id"])
        component_count_by_post[post_id] = (
            component_count_by_post.get(post_id, 0)
            + 1
        )

    for item in plan:
        post_id = str(item["post_id"])
        post_number = post_number_by_id[post_id]
        observed_component_count = (
            component_count_by_post[post_id]
        )

        declared_counts = {
            int(candidate["total_component_count"])
            for candidate in plan
            if (
                str(candidate["post_id"]) == post_id
                and candidate.get(
                    "total_component_count"
                ) is not None
            )
        }

        if len(declared_counts) > 1:
            raise RuntimeError(
                f"Inconsistent total component count "
                f"for post {post_id}"
            )

        component_count = (
            next(iter(declared_counts))
            if declared_counts
            else observed_component_count
        )

        if component_count < observed_component_count:
            raise RuntimeError(
                f"Declared component count is smaller "
                f"than staged media count for post {post_id}"
            )

        base_name = str(item["name"]).strip()

        item["post_number"] = post_number
        item["component_count"] = component_count
        item["base_name"] = base_name

        if component_count <= 1:
            item["name"] = (
                f"{base_name} "
                f"{name_marker}-{post_number}"
            )
        else:
            item["name"] = (
                f"{base_name} "
                f"{name_marker}-{post_number}-"
                f"{item['component_index']}"
            )

    if naming_manifest is not None:
        if not isinstance(naming_manifest, dict):
            raise ValueError(
                "Naming manifest root must be an object"
            )

        manifest_posts = naming_manifest.get("posts")

        if not isinstance(manifest_posts, dict):
            raise ValueError(
                "Naming manifest must contain a posts object"
            )

        present_post_ids = {
            str(item["post_id"])
            for item in plan
        }
        manifest_post_ids = {
            str(post_id)
            for post_id in manifest_posts
        }

        if manifest_post_ids != present_post_ids:
            missing = sorted(
                present_post_ids - manifest_post_ids
            )
            unexpected = sorted(
                manifest_post_ids - present_post_ids
            )
            raise ValueError(
                "Naming manifest post IDs do not match the "
                "staging job. "
                f"Missing: {missing}; "
                f"unexpected: {unexpected}"
            )

        for item in plan:
            post_id = str(item["post_id"])
            entry = manifest_posts.get(post_id)

            if not isinstance(entry, dict):
                raise ValueError(
                    f"Invalid naming entry for post {post_id}"
                )

            component_index = int(
                item["component_index"]
            )

            names_by_component = entry.get(
                "names_by_component"
            )

            if isinstance(names_by_component, dict):
                raw_name = names_by_component.get(
                    str(component_index)
                )

                normalized_name = str(
                    raw_name or ""
                ).strip()

                if (
                    not normalized_name
                    or len(normalized_name) > 500
                ):
                    raise ValueError(
                        f"Post {post_id}, component "
                        f"{component_index} contains an empty "
                        "or overly long Eagle name"
                    )

                item["name"] = normalized_name
            else:
                names = entry.get("names")

                if not isinstance(names, list):
                    raise ValueError(
                        f"Names must be a list for "
                        f"post {post_id}"
                    )

                expected_count = int(
                    item["component_count"]
                )

                if len(names) != expected_count:
                    raise ValueError(
                        f"Post {post_id} requires "
                        f"{expected_count} names, received "
                        f"{len(names)}"
                    )

                normalized_names = [
                    str(name).strip()
                    for name in names
                ]

                if any(
                    not name or len(name) > 500
                    for name in normalized_names
                ):
                    raise ValueError(
                        f"Post {post_id} contains an empty "
                        "or overly long Eagle name"
                    )

                if (
                    component_index < 1
                    or component_index > len(
                        normalized_names
                    )
                ):
                    raise ValueError(
                        f"Invalid component index "
                        f"{component_index} for post {post_id}"
                    )

                item["name"] = normalized_names[
                    component_index - 1
                ]

            total_component_count = entry.get(
                "total_component_count"
            )

            if total_component_count is not None:
                try:
                    total_component_count = int(
                        total_component_count
                    )
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Invalid total component count "
                        f"for post {post_id}"
                    )

                if (
                    total_component_count < 1
                    or component_index
                    > total_component_count
                ):
                    raise ValueError(
                        f"Component {component_index} is "
                        f"outside post {post_id}"
                    )

                item["component_count"] = (
                    total_component_count
                )

            if "description" in entry:
                description = str(
                    entry.get("description") or ""
                )

                if len(description) > 10000:
                    raise ValueError(
                        f"Description is too long for "
                        f"post {post_id}"
                    )

                item["annotation"] = description

            resume_partial = bool(
                entry.get("resume_partial")
            )
            restore_deleted = bool(
                entry.get("restore_deleted")
            )

            if resume_partial and restore_deleted:
                raise ValueError(
                    f"Post {post_id} cannot be both "
                    "resume_partial and restore_deleted"
                )

            item["resume_partial"] = resume_partial
            item["restore_deleted"] = restore_deleted

            imported_component_numbers = entry.get(
                "imported_component_numbers",
                [],
            )
            imported_media_ids = entry.get(
                "imported_media_ids",
                [],
            )

            if not isinstance(
                imported_component_numbers,
                list,
            ):
                raise ValueError(
                    f"Post {post_id} has invalid "
                    "imported_component_numbers"
                )

            if not isinstance(imported_media_ids, list):
                raise ValueError(
                    f"Post {post_id} has invalid "
                    "imported_media_ids"
                )

            normalized_imported_components = []

            for value in imported_component_numbers:
                try:
                    normalized_value = int(value)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Post {post_id} contains invalid "
                        "imported component index"
                    )

                if (
                    normalized_value < 1
                    or normalized_value
                    in normalized_imported_components
                ):
                    raise ValueError(
                        f"Post {post_id} contains invalid or "
                        "duplicate imported component index"
                    )

                normalized_imported_components.append(
                    normalized_value
                )

            normalized_imported_media_ids = []

            for value in imported_media_ids:
                normalized_value = str(value or "").strip()

                if (
                    not normalized_value
                    or normalized_value
                    in normalized_imported_media_ids
                ):
                    raise ValueError(
                        f"Post {post_id} contains invalid or "
                        "duplicate imported media_id"
                    )

                normalized_imported_media_ids.append(
                    normalized_value
                )

            item["imported_component_numbers"] = sorted(
                normalized_imported_components
            )
            item["imported_media_ids"] = sorted(
                normalized_imported_media_ids
            )

            if resume_partial or restore_deleted:
                try:
                    existing_post_number = int(
                        entry.get("existing_post_number")
                    )
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Post {post_id} has no valid "
                        "existing_post_number"
                    )

                if existing_post_number < 1:
                    raise ValueError(
                        f"Post {post_id} has invalid "
                        "existing_post_number"
                    )

                if resume_partial and (
                    not normalized_imported_components
                    or not normalized_imported_media_ids
                ):
                    raise ValueError(
                        f"Post {post_id} resume state is empty"
                    )

                if restore_deleted and (
                    normalized_imported_components
                    or normalized_imported_media_ids
                ):
                    raise ValueError(
                        f"Post {post_id} restore state must "
                        "not declare imported components"
                    )

                item["existing_post_number"] = (
                    existing_post_number
                )
                item["post_number"] = existing_post_number
            else:
                item["existing_post_number"] = None

    plan.sort(
        key=lambda item: (
            item["post_number"],
            item["component_index"],
            item["media_id"],
        )
    )

    return plan


def registered_resume_state(
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compatibility entry point for generic resume."""

    return generic_registered_resume_state(
        items,
        database_path=DATABASE,
    )


def eagle_id_set(
    items: list[dict[str, Any]],
) -> set[str]:
    return {
        str(item.get("id") or "").strip()
        for item in items
        if str(item.get("id") or "").strip()
    }


def verify_source(
    item: dict[str, Any],
) -> bool:
    path = Path(item["source_path"])

    return (
        path.is_file()
        and path.stat().st_size == item["size"]
        and sha256_file(path) == item["sha256"]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--job-id",
        default=None,
        help="Exact staging job ID",
    )
    parser.add_argument(
        "--source-code",
        default=None,
        help="Expected staging source code",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually import items into Eagle",
    )
    parser.add_argument(
        "--start-number",
        type=int,
        default=None,
        help=(
            "Optional first publication number. "
            "Defaults to the next number in the "
            "universal source registry."
        ),
    )
    parser.add_argument(
        "--naming-manifest",
        default=None,
        help=(
            "JSON manifest with exact names and "
            "descriptions"
        ),
    )
    args = parser.parse_args()

    if (
        args.start_number is not None
        and args.start_number < 1
    ):
        raise SystemExit(
            "--start-number must be at least 1"
        )

    job_path, job = latest_ready_job(
        job_id=args.job_id,
        source_code=args.source_code,
    )

    job = ensure_registered_staging_contract(
        job,
        source_code=args.source_code,
    )

    start_number = (
        args.start_number
        if args.start_number is not None
        else next_registered_post_number(
            job["source_code"],
            database_path=DATABASE,
        )
    )

    naming_manifest = None

    if args.naming_manifest:
        manifest_path = Path(
            args.naming_manifest
        ).expanduser().resolve()

        if not manifest_path.is_file():
            raise FileNotFoundError(manifest_path)

        naming_manifest = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )

    plan = build_plan(
        job,
        start_number=start_number,
        naming_manifest=naming_manifest,
    )

    grouped: dict[str, list[dict[str, Any]]] = (
        defaultdict(list)
    )

    for item in plan:
        grouped[item["post_url"]].append(item)

    preflight = []
    blocked_urls: set[str] = set()
    resume_state_by_url: dict[
        str,
        dict[str, Any],
    ] = {}

    with httpx.Client(timeout=120.0) as client:
        for post_url, items in grouped.items():
            existing = query_by_url(
                client,
                post_url,
            )
            existing_ids = eagle_id_set(existing)

            resume_flags = {
                bool(item.get("resume_partial"))
                for item in items
            }
            restore_flags = {
                bool(item.get("restore_deleted"))
                for item in items
            }

            blocked = False
            blocked_reason = None
            resume_state = None

            if (
                len(resume_flags) != 1
                or len(restore_flags) != 1
            ):
                blocked = True
                blocked_reason = (
                    "INCONSISTENT_RECOVERY_FLAGS"
                )

            elif (
                True in resume_flags
                and True in restore_flags
            ):
                blocked = True
                blocked_reason = (
                    "RECOVERY_FLAGS_CONFLICT"
                )

            elif True in restore_flags:
                if existing:
                    blocked = True
                    blocked_reason = (
                        "RESTORE_POST_ALREADY_EXISTS_IN_EAGLE"
                    )

            elif True in resume_flags:
                try:
                    resume_state = (
                        registered_resume_state(items)
                    )
                    expected_ids = set(
                        resume_state[
                            "registered_eagle_ids"
                        ]
                    )

                    if existing_ids != expected_ids:
                        blocked = True
                        blocked_reason = (
                            "EAGLE_URL_ITEMS_DO_NOT_MATCH_REGISTRY"
                        )
                    else:
                        resume_state_by_url[
                            post_url
                        ] = resume_state

                except Exception as error:
                    blocked = True
                    blocked_reason = str(error)

            elif existing:
                blocked = True
                blocked_reason = (
                    "POST_URL_ALREADY_EXISTS_IN_EAGLE"
                )

            if blocked:
                blocked_urls.add(post_url)

            preflight.append({
                "post_url": post_url,
                "post_id": items[0]["post_id"],
                "shortcode": items[0]["shortcode"],
                "planned_components": len(items),
                "resume_partial": (
                    True in resume_flags
                ),
                "restore_deleted": (
                    True in restore_flags
                ),
                "existing_eagle_items_with_url": len(
                    existing
                ),
                "blocked": blocked,
                "blocked_reason": blocked_reason,
                "existing_eagle_ids": sorted(
                    existing_ids
                ),
                "registered_eagle_ids": (
                    resume_state.get(
                        "registered_eagle_ids",
                        [],
                    )
                    if resume_state
                    else []
                ),
                "registered_component_numbers": (
                    resume_state.get(
                        "registered_component_numbers",
                        [],
                    )
                    if resume_state
                    else []
                ),
            })

    importable_plan = [
        item
        for item in plan
        if item["post_url"] not in blocked_urls
    ]

    preview = {
        "job_id": job.get("job_id"),
        "staging_job": str(job_path),
        "planned_posts": len(grouped),
        "planned_items": len(plan),
        "blocked_posts": len(blocked_urls),
        "importable_items": len(importable_plan),
        "extensions": dict(
            sorted(
                Counter(
                    item["extension"]
                    for item in plan
                ).items()
            )
        ),
        "target_tag": (
            plan[0]["tags"][0]
            if plan and plan[0]["tags"]
            else None
        ),
        "target_folder_id": (
            plan[0]["folders"][0]
            if plan and plan[0]["folders"]
            else None
        ),
        "target_tags": (
            list(plan[0]["tags"])
            if plan
            else []
        ),
        "target_folder_ids": (
            list(plan[0]["folders"])
            if plan
            else []
        ),
        "target_name_marker": (
            plan[0]["name_marker"]
            if plan
            else None
        ),
        "source_code": (
            plan[0]["source_code"]
            if plan
            else job.get("source_code")
        ),
    }

    if not args.commit:
        print(json.dumps({
            "status": "PREVIEW_ONLY",
            "preview": preview,
            "preflight": preflight,
            "items": [
                {
                    "post_id": item["post_id"],
                    "shortcode": item["shortcode"],
                    "post_number": item[
                        "post_number"
                    ],
                    "component_index": item[
                        "component_index"
                    ],
                    "component_count": item[
                        "component_count"
                    ],
                    "media_id": item["media_id"],
                    "name": item["name"],
                    "extension": item["extension"],
                    "size": item["size"],
                    "post_url": item["post_url"],
                    "source_path": item["source_path"],
                    "source_code": item["source_code"],
                    "tags": list(item["tags"]),
                    "folders": list(item["folders"]),
                    "name_marker": item["name_marker"],
                }
                for item in importable_plan
            ],
            "next_command": (
                "python -m app.eagle_import_staging "
                "--commit"
            ),
            "safety": {
                "eagle_items_created": 0,
                "staging_modified": False,
                "source_files_deleted": 0,
                "database_modified": False,
            },
        }, ensure_ascii=False, indent=2))
        return

    if blocked_urls:
        blocked_details = [
            {
                "post_url": entry.get("post_url"),
                "reason": entry.get("blocked_reason"),
            }
            for entry in preflight
            if entry.get("blocked")
        ]

        raise SystemExit(
            "Import aborted by Eagle preflight: "
            + json.dumps(
                blocked_details,
                ensure_ascii=False,
            )
        )

    if len(importable_plan) != len(plan):
        raise SystemExit(
            "Import aborted: plan changed during preflight"
        )

    import_results = []
    created_ids: list[str] = []
    stop_reason = None

    with httpx.Client(timeout=180.0) as client:
        for item in importable_plan:
            source_path = Path(item["source_path"])

            if not verify_source(item):
                stop_reason = (
                    f"Source verification failed: "
                    f"{source_path}"
                )
                break

            # Recheck immediately before writing.
            existing_now = query_by_url(
                client,
                item["post_url"],
            )

            created_for_same_post = [
                result
                for result in import_results
                if (
                    result.get("post_url")
                    == item["post_url"]
                    and result.get("status")
                    in {
                        "IMPORTED_AND_VERIFIED",
                        "IMPORTED_READBACK_PENDING",
                    }
                )
            ]

            if item.get("resume_partial"):
                resume_state = resume_state_by_url.get(
                    item["post_url"]
                )

                if resume_state is None:
                    stop_reason = (
                        "RESUME_STATE_MISSING_BEFORE_IMPORT"
                    )
                    break

                registered_ids = set(
                    resume_state[
                        "registered_eagle_ids"
                    ]
                )
                created_same_post_ids = {
                    str(
                        result.get("eagle_id") or ""
                    ).strip()
                    for result in created_for_same_post
                    if str(
                        result.get("eagle_id") or ""
                    ).strip()
                }
                expected_ids_now = (
                    registered_ids
                    | created_same_post_ids
                )
                existing_ids_now = eagle_id_set(
                    existing_now
                )

                if existing_ids_now != expected_ids_now:
                    stop_reason = (
                        "Eagle URL state changed before "
                        f"import: {item['post_url']}; "
                        f"expected IDs "
                        f"{sorted(expected_ids_now)}, "
                        f"received "
                        f"{sorted(existing_ids_now)}"
                    )
                    break

            elif existing_now and not created_for_same_post:
                stop_reason = (
                    "An item appeared in Eagle before "
                    f"import: {item['post_url']}"
                )
                break

            payload = {
                "path": str(source_path),
                "name": item["name"],
                "tags": item["tags"],
                "folders": item["folders"],
                "website": item["post_url"],
                "annotation": item["annotation"],
            }

            try:
                response = client.post(
                    f"{API_URL}/api/v2/item/add",
                    json=payload,
                )
                response.raise_for_status()
                add_payload = response.json()

            except Exception as error:
                # Do not automatically retry an ambiguous
                # write request: it may already have succeeded.
                stop_reason = (
                    "Ambiguous Eagle write failure. "
                    "Automatic retry disabled to prevent "
                    f"duplicates: {error}"
                )
                break

            if add_payload.get("status") != "success":
                stop_reason = (
                    f"Eagle rejected item: {add_payload}"
                )
                break

            created_id = find_created_id(add_payload)

            if not created_id:
                stop_reason = (
                    "Eagle reported success but did not "
                    "return a created item ID"
                )
                break

            created_ids.append(created_id)

            created_item = read_created_item(
                client,
                created_id,
            )

            source_unchanged = verify_source(item)

            verification = {
                "created_id_found": True,
                "created_item_read_back": bool(
                    created_item
                ),
                "source_unchanged": source_unchanged,
                "name_correct": bool(
                    created_item
                    and created_item.get("name")
                    == item["name"]
                ),
                "url_correct": bool(
                    created_item
                    and created_item.get("url")
                    == item["post_url"]
                ),
                "tag_correct": bool(
                    created_item
                    and all(
                        tag in (
                            created_item.get("tags") or []
                        )
                        for tag in item["tags"]
                    )
                ),
                "folder_correct": bool(
                    created_item
                    and all(
                        folder_id in (
                            created_item.get("folders")
                            or []
                        )
                        for folder_id in item["folders"]
                    )
                ),
                "extension_correct": bool(
                    created_item
                    and str(
                        created_item.get("ext") or ""
                    ).lower()
                    == item["extension"]
                ),
            }

            fully_verified = all(
                verification.values()
            )

            import_results.append({
                "status": (
                    "IMPORTED_AND_VERIFIED"
                    if fully_verified
                    else "IMPORTED_READBACK_PENDING"
                ),
                "eagle_id": created_id,
                "post_id": item["post_id"],
                "media_id": item["media_id"],
                "shortcode": item["shortcode"],
                "post_url": item["post_url"],
                "name": item["name"],
                "post_number": item[
                    "post_number"
                ],
                "component_index": item[
                    "component_index"
                ],
                "component_count": item[
                    "component_count"
                ],
                "source_path": item["source_path"],
                "source_code": item["source_code"],
                "tags": list(item["tags"]),
                "folders": list(item["folders"]),
                "name_marker": item["name_marker"],
                "verification": verification,
            })

    imported_count = len(created_ids)

    final_status = (
        "BATCH_IMPORTED"
        if (
            imported_count == len(plan)
            and stop_reason is None
        )
        else "BATCH_PARTIAL_OR_REQUIRES_REVIEW"
    )

    result = {
        "status": final_status,
        "job_id": job.get("job_id"),
        "source_code": (
            plan[0]["source_code"]
            if plan
            else job.get("source_code")
        ),
        "start_number": start_number,
        "planned_items": len(plan),
        "imported_items": imported_count,
        "remaining_items": (
            len(plan) - imported_count
        ),
        "created_eagle_ids": created_ids,
        "stop_reason": stop_reason,
        "results": import_results,
        "safety": {
            "staging_modified": False,
            "source_files_deleted": 0,
            "source_files_moved": 0,
            "database_modified": False,
            "eagle_items_created": imported_count,
            "eagle_items_updated": 0,
            "eagle_items_deleted": 0,
        },
    }

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    report_path = (
        REPORTS
        / f"eagle_staging_import_{timestamp}.json"
    )

    report_path.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result["report"] = str(report_path)

    print(json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
