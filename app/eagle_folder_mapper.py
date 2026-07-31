"""Universal Eagle folder planning for ReferenceSync.

Preview mode is deliberately read-only:
- reads the containers hierarchy from SQLite;
- does not contact Eagle;
- does not create or update folders;
- does not modify SQLite.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


class FolderMappingError(RuntimeError):
    """Raised when the container hierarchy cannot be mapped safely."""


def normalized_name(value: Any, fallback: str) -> str:
    """Return a safe, non-empty Eagle folder name."""

    text = str(value or "").replace("\x00", "").strip()
    text = re.sub(r"\s+", " ", text)
    return text or fallback


def open_read_only(database: Path) -> sqlite3.Connection:
    """Open an existing SQLite database without permitting writes."""

    database = database.expanduser().resolve()

    if not database.is_file():
        raise FolderMappingError(
            f"SQLite database does not exist: {database}"
        )

    connection = sqlite3.connect(
        database.as_uri() + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def load_containers(
    database: Path,
    source_codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Load source/container rows needed for an Eagle folder plan."""

    requested = {
        str(value).strip().lower()
        for value in (source_codes or [])
        if str(value).strip()
    }

    with open_read_only(database) as connection:
        available_tables = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }

        required_tables = {"sources", "containers"}
        missing = sorted(required_tables - available_tables)

        if missing:
            raise FolderMappingError(
                "Required database tables are missing: "
                + ", ".join(missing)
            )

        rows = connection.execute(
            """
            SELECT
                c.id,
                c.source_id,
                s.code AS source_code,
                s.name AS source_name,
                c.external_id,
                c.parent_container_id,
                c.container_type,
                c.original_name,
                c.display_name,
                c.canonical_url,
                c.eagle_folder_id,
                c.status
            FROM containers AS c
            JOIN sources AS s
              ON s.id = c.source_id
            ORDER BY
                s.code COLLATE NOCASE,
                c.id
            """
        ).fetchall()

    result = [dict(row) for row in rows]

    if requested:
        result = [
            row
            for row in result
            if str(row["source_code"]).lower() in requested
        ]

        found = {
            str(row["source_code"]).lower()
            for row in result
        }
        unknown = sorted(requested - found)

        if unknown:
            raise FolderMappingError(
                "No containers found for source(s): "
                + ", ".join(unknown)
            )

    return result


def build_folder_plan(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build and validate the desired Eagle folder hierarchy."""

    by_id = {int(row["id"]): row for row in rows}
    state: dict[int, int] = {}
    calculated: dict[int, dict[str, Any]] = {}
    warnings: list[str] = []
    cycle_ids: set[int] = set()
    orphan_non_roots = 0

    def calculate(container_id: int) -> dict[str, Any]:
        nonlocal orphan_non_roots

        if container_id in calculated:
            return calculated[container_id]

        visit_state = state.get(container_id, 0)

        if visit_state == 1:
            cycle_ids.add(container_id)
            raise FolderMappingError(
                f"Container hierarchy cycle detected at id={container_id}"
            )

        if visit_state == 2:
            return calculated[container_id]

        row = by_id[container_id]
        state[container_id] = 1

        external_id = str(row.get("external_id") or container_id)
        container_type = str(
            row.get("container_type") or "UNKNOWN"
        ).upper()

        fallback_name = (
            str(row.get("source_name") or row.get("source_code") or "Source")
            if container_type == "ROOT"
            else external_id
        )

        desired_name = normalized_name(
            row.get("display_name") or row.get("original_name"),
            fallback_name,
        )

        parent_id = row.get("parent_container_id")
        parent = None

        if parent_id is not None:
            parent = by_id.get(int(parent_id))

        if parent is None:
            if parent_id is not None:
                warnings.append(
                    f"Container {external_id!r} references missing "
                    f"parent id={parent_id}"
                )

            if container_type != "ROOT":
                orphan_non_roots += 1
                warnings.append(
                    f"Non-ROOT container {external_id!r} has no "
                    "available parent"
                )

            depth = 0
            chain_ids = [external_id]
            chain_names = [desired_name]
            parent_external_id = None
        else:
            parent_plan = calculate(int(parent["id"]))
            depth = int(parent_plan["depth"]) + 1
            chain_ids = [
                *parent_plan["chain_external_ids"],
                external_id,
            ]
            chain_names = [
                *parent_plan["chain_names"],
                desired_name,
            ]
            parent_external_id = str(parent["external_id"])

        item = {
            "container_id": container_id,
            "source_code": str(row["source_code"]),
            "external_id": external_id,
            "container_type": container_type,
            "desired_name": desired_name,
            "parent_container_id": (
                int(parent_id) if parent_id is not None else None
            ),
            "parent_external_id": parent_external_id,
            "depth": depth,
            "chain_external_ids": chain_ids,
            "chain_names": chain_names,
            "display_path": " \u203a ".join(chain_names),
            "canonical_url": row.get("canonical_url"),
            "existing_eagle_folder_id": (
                str(row["eagle_folder_id"])
                if row.get("eagle_folder_id")
                else None
            ),
            "status": row.get("status"),
        }

        state[container_id] = 2
        calculated[container_id] = item
        return item

    for current_id in sorted(by_id):
        calculate(current_id)

    folders = sorted(
        calculated.values(),
        key=lambda item: (
            item["source_code"].lower(),
            item["depth"],
            [name.lower() for name in item["chain_names"]],
            item["container_id"],
        ),
    )

    root_count = sum(
        item["container_type"] == "ROOT"
        for item in folders
    )
    mapped_count = sum(
        bool(item["existing_eagle_folder_id"])
        for item in folders
    )

    source_counts: dict[str, int] = {}

    for item in folders:
        source_code = item["source_code"]
        source_counts[source_code] = (
            source_counts.get(source_code, 0) + 1
        )

    summary = {
        "folders": len(folders),
        "roots": root_count,
        "mapped_folders": mapped_count,
        "unmapped_folders": len(folders) - mapped_count,
        "orphan_non_roots": orphan_non_roots,
        "cycles": len(cycle_ids),
        "sources": source_counts,
        "warnings": len(warnings),
        "eagle_api_requests": 0,
        "database_modified": False,
    }

    return {
        "schema_version": 1,
        "operation": "preview-eagle-folders",
        "summary": summary,
        "folders": folders,
        "warnings": warnings,
    }



def load_job_scope(job_json: Path) -> dict[str, Any]:
    """Load and validate source/container scope from a staging job."""

    job_json = job_json.expanduser().resolve()

    if not job_json.is_file():
        raise FolderMappingError(
            f"Staging job does not exist: {job_json}"
        )

    try:
        payload = json.loads(
            job_json.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise FolderMappingError(
            f"Cannot read staging job {job_json}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise FolderMappingError(
            "Staging job must contain a JSON object"
        )

    source_code = str(
        payload.get("source_code") or ""
    ).strip().lower()

    if not source_code:
        raise FolderMappingError(
            "Staging job has no source_code"
        )

    raw_chain = payload.get("container_chain")

    if not isinstance(raw_chain, list):
        raise FolderMappingError(
            "Staging job container_chain must be a list"
        )

    container_chain: list[str] = []
    seen: set[str] = set()

    for raw_value in raw_chain:
        value = str(raw_value or "").strip()

        if not value or value in seen:
            continue

        seen.add(value)
        container_chain.append(value)

    if not container_chain:
        raise FolderMappingError(
            "Staging job container_chain is empty"
        )

    eagle = payload.get("eagle")

    if not isinstance(eagle, dict):
        eagle = {}

    return {
        "job_json": str(job_json),
        "job_id": str(
            payload.get("job_id")
            or payload.get("id")
            or ""
        ).strip() or None,
        "source_code": source_code,
        "container_chain": container_chain,
        "eagle_folder_ids": [
            str(value).strip()
            for value in (eagle.get("folder_ids") or [])
            if str(value).strip()
        ],
    }


def select_container_scope(
    rows: list[dict[str, Any]],
    requested_external_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Select requested containers and all of their ancestors."""

    by_id = {
        int(row["id"]): row
        for row in rows
    }

    by_external_id: dict[str, dict[str, Any]] = {}

    for row in rows:
        external_id = str(row.get("external_id") or "")

        if not external_id:
            continue

        if external_id in by_external_id:
            raise FolderMappingError(
                "Duplicate container external_id in source: "
                f"{external_id}"
            )

        by_external_id[external_id] = row

    missing = [
        external_id
        for external_id in requested_external_ids
        if external_id not in by_external_id
    ]

    if missing:
        raise FolderMappingError(
            "Staging job references unknown container(s): "
            + ", ".join(missing)
        )

    selected_ids: set[int] = set()

    for external_id in requested_external_ids:
        current = by_external_id[external_id]
        visited_in_chain: set[int] = set()

        while current is not None:
            current_id = int(current["id"])

            if current_id in visited_in_chain:
                raise FolderMappingError(
                    "Container parent cycle detected while selecting "
                    f"scope for {external_id!r}"
                )

            visited_in_chain.add(current_id)
            selected_ids.add(current_id)

            parent_id = current.get("parent_container_id")

            if parent_id is None:
                current = None
                continue

            current = by_id.get(int(parent_id))

            if current is None:
                raise FolderMappingError(
                    f"Container {external_id!r} references missing "
                    f"parent id={parent_id}"
                )

    selected = [
        row
        for row in rows
        if int(row["id"]) in selected_ids
    ]

    selected_external_ids = {
        str(row["external_id"])
        for row in selected
    }

    ancestors = [
        external_id
        for external_id in selected_external_ids
        if external_id not in requested_external_ids
    ]

    return selected, sorted(ancestors)

def preview_folder_plan(
    database: Path,
    source_codes: list[str] | None = None,
    job_json: Path | None = None,
) -> dict[str, Any]:
    """Load containers and return a read-only Eagle folder plan."""

    scope = None

    if job_json is not None:
        scope = load_job_scope(job_json)
        job_source = scope["source_code"]

        requested_sources = {
            str(value).strip().lower()
            for value in (source_codes or [])
            if str(value).strip()
        }

        if requested_sources and requested_sources != {job_source}:
            raise FolderMappingError(
                "--source-code conflicts with job source_code"
            )

        rows = load_containers(
            database,
            [job_source],
        )
        rows, ancestors = select_container_scope(
            rows,
            scope["container_chain"],
        )
        scope["ancestor_container_ids"] = ancestors
        scope["selected_containers"] = len(rows)

    else:
        rows = load_containers(database, source_codes)

    result = build_folder_plan(rows)

    if scope is not None:
        result["scope"] = scope

    return result



def fetch_eagle_folder_tree(
    api_url: str,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Read the current Eagle folder tree without modifying it."""

    import urllib.error
    import urllib.request

    endpoint = api_url.rstrip("/") + "/folder/list"
    request = urllib.request.Request(
        endpoint,
        method="GET",
        headers={"Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            payload = json.loads(
                response.read().decode("utf-8")
            )
    except urllib.error.URLError as exc:
        raise FolderMappingError(
            f"Cannot connect to Eagle API: {exc}"
        ) from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise FolderMappingError(
            f"Invalid Eagle API response: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise FolderMappingError(
            "Eagle folder/list returned a non-object response"
        )

    if payload.get("status") != "success":
        raise FolderMappingError(
            "Eagle folder/list returned non-success status"
        )

    folders = payload.get("data")

    if not isinstance(folders, list):
        raise FolderMappingError(
            "Eagle folder/list response has no folder list"
        )

    return folders


def flatten_eagle_folders(
    folders: list[dict[str, Any]],
    parent_id: str | None = None,
    parent_path: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Flatten Eagle's nested folder response while preserving parents."""

    result: list[dict[str, Any]] = []
    current_parent_path = list(parent_path or [])

    for raw_folder in folders:
        if not isinstance(raw_folder, dict):
            continue

        folder_id = str(raw_folder.get("id") or "").strip()
        folder_name = str(raw_folder.get("name") or "").strip()

        if not folder_id:
            continue

        current_path = [
            *current_parent_path,
            folder_name,
        ]

        result.append({
            "id": folder_id,
            "name": folder_name,
            "parent_id": parent_id,
            "path": current_path,
            "display_path": " \u203a ".join(current_path),
        })

        children = raw_folder.get("children") or []

        if isinstance(children, list):
            result.extend(
                flatten_eagle_folders(
                    children,
                    parent_id=folder_id,
                    parent_path=current_path,
                )
            )

    return result


def compare_folder_plan(
    database: Path,
    source_codes: list[str] | None = None,
    api_url: str = "http://localhost:41595/api",
    job_json: Path | None = None,
) -> dict[str, Any]:
    """Compare the desired hierarchy with Eagle without writing anything."""

    preview = preview_folder_plan(
        database,
        source_codes,
        job_json=job_json,
    )

    eagle_tree = fetch_eagle_folder_tree(api_url)
    existing = flatten_eagle_folders(eagle_tree)

    by_eagle_id = {
        folder["id"]: folder
        for folder in existing
    }

    children_by_parent: dict[
        str | None,
        list[dict[str, Any]],
    ] = {}

    for folder in existing:
        children_by_parent.setdefault(
            folder["parent_id"],
            [],
        ).append(folder)

    planned = sorted(
        preview["folders"],
        key=lambda item: (
            int(item.get("depth") or 0),
            str(item.get("source_code") or "").lower(),
            str(item.get("display_path") or "").lower(),
            int(item.get("container_id") or 0),
        ),
    )

    resolved: dict[
        tuple[str, str],
        dict[str, Any],
    ] = {}

    comparison: list[dict[str, Any]] = []
    warnings: list[str] = []

    status_counts = {
        "MAPPED_BY_ID": 0,
        "MATCHED_BY_NAME": 0,
        "MISSING": 0,
        "BLOCKED_BY_PARENT": 0,
        "AMBIGUOUS_DUPLICATE": 0,
        "MAPPED_ID_NOT_FOUND": 0,
        "MAPPED_ID_PARENT_MISMATCH": 0,
    }

    for item in planned:
        source_code = str(item["source_code"])
        external_id = str(item["external_id"])
        parent_external_id = item.get("parent_external_id")
        mapped_id = item.get("existing_eagle_folder_id")
        desired_name = str(item["desired_name"])

        key = (source_code, external_id)
        parent_key = (
            (source_code, str(parent_external_id))
            if parent_external_id is not None
            else None
        )

        resolved_parent = (
            resolved.get(parent_key)
            if parent_key is not None
            else None
        )

        expected_parent_id = (
            resolved_parent["id"]
            if resolved_parent is not None
            else None
        )

        result = {
            "source_code": source_code,
            "external_id": external_id,
            "container_type": item["container_type"],
            "desired_name": desired_name,
            "desired_path": item["display_path"],
            "parent_external_id": parent_external_id,
            "expected_parent_eagle_id": expected_parent_id,
            "stored_eagle_folder_id": mapped_id,
            "eagle_folder_id": None,
            "actual_name": None,
            "actual_path": None,
            "status": None,
            "name_differs": False,
            "candidate_ids": [],
            "candidate_paths": [],
        }

        # A stable stored Eagle ID always takes precedence over names.
        if mapped_id:
            existing_by_id = by_eagle_id.get(str(mapped_id))

            if existing_by_id is None:
                result["status"] = "MAPPED_ID_NOT_FOUND"
                status_counts["MAPPED_ID_NOT_FOUND"] += 1
                warnings.append(
                    f"Stored Eagle folder ID {mapped_id!r} for "
                    f"{external_id!r} does not exist"
                )
                comparison.append(result)
                continue

            actual_parent_id = existing_by_id["parent_id"]

            parent_mismatch = False

            if parent_key is None:
                parent_mismatch = actual_parent_id is not None
            elif resolved_parent is None:
                parent_mismatch = True
            else:
                parent_mismatch = (
                    actual_parent_id != expected_parent_id
                )

            result.update({
                "eagle_folder_id": existing_by_id["id"],
                "actual_name": existing_by_id["name"],
                "actual_path": existing_by_id["display_path"],
                "name_differs": (
                    normalized_name(
                        existing_by_id["name"],
                        existing_by_id["id"],
                    ).casefold()
                    != normalized_name(
                        desired_name,
                        external_id,
                    ).casefold()
                ),
            })

            if parent_mismatch:
                result["status"] = (
                    "MAPPED_ID_PARENT_MISMATCH"
                )
                status_counts[
                    "MAPPED_ID_PARENT_MISMATCH"
                ] += 1
                warnings.append(
                    f"Stored Eagle folder ID {mapped_id!r} for "
                    f"{external_id!r} has an unexpected parent"
                )
            else:
                result["status"] = "MAPPED_BY_ID"
                status_counts["MAPPED_BY_ID"] += 1
                resolved[key] = existing_by_id

            comparison.append(result)
            continue

        # An unresolved planned parent blocks safe child matching.
        if parent_key is not None and resolved_parent is None:
            result["status"] = "BLOCKED_BY_PARENT"
            status_counts["BLOCKED_BY_PARENT"] += 1
            comparison.append(result)
            continue

        candidates = children_by_parent.get(
            expected_parent_id,
            [],
        )

        desired_key = normalized_name(
            desired_name,
            external_id,
        ).casefold()

        exact_candidates = [
            folder
            for folder in candidates
            if normalized_name(
                folder["name"],
                folder["id"],
            ).casefold() == desired_key
        ]

        result["candidate_ids"] = [
            folder["id"]
            for folder in exact_candidates
        ]
        result["candidate_paths"] = [
            folder["display_path"]
            for folder in exact_candidates
        ]

        if len(exact_candidates) == 1:
            selected = exact_candidates[0]
            result.update({
                "status": "MATCHED_BY_NAME",
                "eagle_folder_id": selected["id"],
                "actual_name": selected["name"],
                "actual_path": selected["display_path"],
            })
            status_counts["MATCHED_BY_NAME"] += 1
            resolved[key] = selected

        elif len(exact_candidates) > 1:
            result["status"] = "AMBIGUOUS_DUPLICATE"
            status_counts["AMBIGUOUS_DUPLICATE"] += 1
            warnings.append(
                f"Multiple Eagle folders match "
                f"{item['display_path']!r}"
            )

        else:
            result["status"] = "MISSING"
            status_counts["MISSING"] += 1

        comparison.append(result)

    unsafe_statuses = (
        "AMBIGUOUS_DUPLICATE",
        "MAPPED_ID_NOT_FOUND",
        "MAPPED_ID_PARENT_MISMATCH",
    )

    unsafe_conflicts = sum(
        status_counts[name]
        for name in unsafe_statuses
    )

    matched_existing = (
        status_counts["MAPPED_BY_ID"]
        + status_counts["MATCHED_BY_NAME"]
    )

    summary = {
        "planned_folders": len(planned),
        "eagle_folders_total": len(existing),
        "mapped_by_id": status_counts["MAPPED_BY_ID"],
        "matched_by_name": status_counts["MATCHED_BY_NAME"],
        "matched_existing": matched_existing,
        "missing": status_counts["MISSING"],
        "blocked_by_parent": (
            status_counts["BLOCKED_BY_PARENT"]
        ),
        "ambiguous_duplicates": (
            status_counts["AMBIGUOUS_DUPLICATE"]
        ),
        "mapped_id_not_found": (
            status_counts["MAPPED_ID_NOT_FOUND"]
        ),
        "mapped_id_parent_mismatch": (
            status_counts["MAPPED_ID_PARENT_MISMATCH"]
        ),
        "unsafe_conflicts": unsafe_conflicts,
        "status_counts": status_counts,
        "warnings": len(warnings),
        "eagle_api_get_requests": 1,
        "eagle_api_write_requests": 0,
        "database_modified": False,
    }

    result = {
        "schema_version": 1,
        "operation": "compare-eagle-folders",
        "summary": summary,
        "comparison": comparison,
        "warnings": warnings,
    }

    if preview.get("scope") is not None:
        result["scope"] = preview["scope"]

    return result


def create_eagle_folder(
    api_url: str,
    folder_name: str,
    parent_id: str | None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Create one Eagle folder and return its normalized identity."""

    import urllib.error
    import urllib.request

    endpoint = api_url.rstrip("/") + "/folder/create"
    body: dict[str, Any] = {
        "folderName": folder_name,
    }

    if parent_id:
        body["parent"] = parent_id

    encoded = json.dumps(
        body,
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        endpoint,
        data=encoded,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            payload = json.loads(
                response.read().decode("utf-8")
            )
    except urllib.error.URLError as exc:
        raise FolderMappingError(
            f"Cannot create Eagle folder {folder_name!r}: {exc}"
        ) from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise FolderMappingError(
            f"Invalid Eagle create response for "
            f"{folder_name!r}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise FolderMappingError(
            "Eagle folder/create returned a non-object response"
        )

    if payload.get("status") != "success":
        raise FolderMappingError(
            f"Eagle rejected folder creation for {folder_name!r}"
        )

    data = payload.get("data")

    if not isinstance(data, dict):
        raise FolderMappingError(
            "Eagle folder/create response has no data object"
        )

    folder_id = str(data.get("id") or "").strip()

    if not folder_id:
        raise FolderMappingError(
            "Eagle folder/create response has no folder ID"
        )

    return {
        "id": folder_id,
        "name": str(data.get("name") or folder_name).strip(),
        "parent_id": parent_id,
    }


def save_eagle_folder_mapping(
    database: Path,
    container_id: int,
    eagle_folder_id: str,
) -> bool:
    """Persist one verified container-to-Eagle mapping."""

    from datetime import datetime, timezone

    database = database.expanduser().resolve()

    if not database.is_file():
        raise FolderMappingError(
            f"SQLite database does not exist: {database}"
        )

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")

    try:
        container = connection.execute(
            """
            SELECT
                id,
                eagle_folder_id
            FROM containers
            WHERE id = ?
            """,
            (container_id,),
        ).fetchone()

        if container is None:
            raise FolderMappingError(
                f"Container id={container_id} no longer exists"
            )

        conflicts = connection.execute(
            """
            SELECT id
            FROM containers
            WHERE eagle_folder_id = ?
              AND id != ?
            """,
            (eagle_folder_id, container_id),
        ).fetchall()

        if conflicts:
            raise FolderMappingError(
                f"Eagle folder ID {eagle_folder_id!r} is already "
                "assigned to another container"
            )

        old_value = (
            str(container["eagle_folder_id"])
            if container["eagle_folder_id"]
            else None
        )

        if old_value == eagle_folder_id:
            return False

        connection.execute(
            """
            UPDATE containers
            SET
                eagle_folder_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                eagle_folder_id,
                datetime.now(timezone.utc).isoformat(),
                container_id,
            ),
        )

        connection.commit()
        return True

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def apply_folder_plan(
    database: Path,
    source_codes: list[str] | None = None,
    api_url: str = "http://localhost:41595/api",
    job_json: Path | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    """Create only missing folders for one exact staging job."""

    if not commit:
        raise FolderMappingError(
            "Folder creation requires the explicit --commit flag"
        )

    if job_json is None:
        raise FolderMappingError(
            "Folder creation requires --job-json; "
            "unscoped creation is prohibited"
        )

    preview = preview_folder_plan(
        database,
        source_codes,
        job_json=job_json,
    )

    eagle_tree = fetch_eagle_folder_tree(api_url)
    existing = flatten_eagle_folders(eagle_tree)

    by_eagle_id = {
        folder["id"]: folder
        for folder in existing
    }

    children_by_parent: dict[
        str | None,
        list[dict[str, Any]],
    ] = {}

    for folder in existing:
        children_by_parent.setdefault(
            folder["parent_id"],
            [],
        ).append(folder)

    planned = sorted(
        preview["folders"],
        key=lambda item: (
            int(item.get("depth") or 0),
            str(item.get("source_code") or "").lower(),
            int(item.get("container_id") or 0),
        ),
    )

    resolved: dict[
        tuple[str, str],
        dict[str, Any],
    ] = {}

    results: list[dict[str, Any]] = []
    created = 0
    reused_by_id = 0
    reused_by_name = 0
    database_mappings_written = 0

    for item in planned:
        source_code = str(item["source_code"])
        external_id = str(item["external_id"])
        desired_name = str(item["desired_name"])
        stored_id = item.get("existing_eagle_folder_id")
        parent_external_id = item.get("parent_external_id")

        key = (source_code, external_id)
        parent_key = (
            (source_code, str(parent_external_id))
            if parent_external_id is not None
            else None
        )

        if parent_key is None:
            parent_id = None
        else:
            parent = resolved.get(parent_key)

            if parent is None:
                raise FolderMappingError(
                    f"Cannot process {external_id!r}: "
                    "its parent has not been resolved"
                )

            parent_id = parent["id"]

        selected = None
        status = None

        # Stable database ID has the highest priority.
        if stored_id:
            selected = by_eagle_id.get(str(stored_id))

            if selected is None:
                raise FolderMappingError(
                    f"Stored Eagle folder ID {stored_id!r} for "
                    f"{external_id!r} does not exist"
                )

            if selected["parent_id"] != parent_id:
                raise FolderMappingError(
                    f"Stored Eagle folder {stored_id!r} for "
                    f"{external_id!r} has an unexpected parent"
                )

            status = "REUSED_BY_ID"
            reused_by_id += 1

        else:
            desired_key = normalized_name(
                desired_name,
                external_id,
            ).casefold()

            matches = [
                folder
                for folder in children_by_parent.get(
                    parent_id,
                    [],
                )
                if normalized_name(
                    folder["name"],
                    folder["id"],
                ).casefold() == desired_key
            ]

            if len(matches) > 1:
                raise FolderMappingError(
                    f"Multiple Eagle folders match "
                    f"{item['display_path']!r}"
                )

            if len(matches) == 1:
                selected = matches[0]
                status = "REUSED_BY_NAME"
                reused_by_name += 1

            else:
                created_folder = create_eagle_folder(
                    api_url,
                    desired_name,
                    parent_id,
                )

                selected = {
                    "id": created_folder["id"],
                    "name": created_folder["name"],
                    "parent_id": parent_id,
                    "path": [
                        *(
                            resolved[parent_key]["path"]
                            if parent_key is not None
                            else []
                        ),
                        created_folder["name"],
                    ],
                }
                selected["display_path"] = " \u203a ".join(
                    selected["path"]
                )

                by_eagle_id[selected["id"]] = selected
                children_by_parent.setdefault(
                    parent_id,
                    [],
                ).append(selected)

                status = "CREATED"
                created += 1

        if selected is None or status is None:
            raise FolderMappingError(
                f"Folder resolution failed for {external_id!r}"
            )

        mapping_written = save_eagle_folder_mapping(
            database,
            int(item["container_id"]),
            selected["id"],
        )

        if mapping_written:
            database_mappings_written += 1

        resolved[key] = selected

        results.append({
            "source_code": source_code,
            "container_id": int(item["container_id"]),
            "external_id": external_id,
            "container_type": item["container_type"],
            "desired_name": desired_name,
            "desired_path": item["display_path"],
            "status": status,
            "eagle_folder_id": selected["id"],
            "actual_name": selected["name"],
            "actual_path": selected["display_path"],
            "parent_eagle_folder_id": parent_id,
            "database_mapping_written": mapping_written,
        })

    summary = {
        "planned_folders": len(planned),
        "reused_by_id": reused_by_id,
        "reused_by_name": reused_by_name,
        "created": created,
        "database_mappings_written": database_mappings_written,
        "eagle_api_get_requests": 1,
        "eagle_api_write_requests": created,
        "database_modified": database_mappings_written > 0,
        "unsafe_conflicts": 0,
    }

    return {
        "schema_version": 1,
        "operation": "apply-eagle-folders",
        "scope": preview.get("scope"),
        "summary": summary,
        "results": results,
        "warnings": [],
    }

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only Eagle folder plan from ReferenceSync "
            "container records."
        )
    )

    parser.add_argument(
        "operation",
        choices=("preview", "compare", "apply"),
    )
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="Path to the ReferenceSync SQLite database",
    )
    parser.add_argument(
        "--source-code",
        action="append",
        dest="source_codes",
        help=(
            "Limit the plan to a source code. May be supplied more "
            "than once."
        ),
    )
    parser.add_argument(
        "--job-json",
        type=Path,
        help=(
            "Limit processing to source/container_chain from "
            "a staging job.json"
        ),
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Allow apply mode to create Eagle folders and save "
            "verified folder IDs"
        ),
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:41595/api",
        help="Eagle local API base URL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output file",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only the summary to stdout",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        if args.operation == "preview":
            plan = preview_folder_plan(
                args.database,
                args.source_codes,
                job_json=args.job_json,
            )
        elif args.operation == "compare":
            plan = compare_folder_plan(
                args.database,
                args.source_codes,
                api_url=args.api_url,
                job_json=args.job_json,
            )
        else:
            plan = apply_folder_plan(
                args.database,
                args.source_codes,
                api_url=args.api_url,
                job_json=args.job_json,
                commit=args.commit,
            )
    except (
        FolderMappingError,
        OSError,
        sqlite3.Error,
        ValueError,
    ) as exc:
        print(
            f"EAGLE_FOLDER_MAPPING_ERROR="
            f"{type(exc).__name__}: {exc}"
        )
        return 1

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                plan,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )

    payload = plan["summary"] if args.summary_only else plan

    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
