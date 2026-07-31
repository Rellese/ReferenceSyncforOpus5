from __future__ import annotations

# STALE_DISCOVERY_SELECTED_POST_RECOVERY_V1

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


STATE_KEYS = (
    "resume_partial",
    "restore_deleted",
    "reimport_deleted_as_new",
    "discovery_status",
    "existing_post_number",
    "imported_component_numbers",
    "imported_media_ids",
    "selected_components",
    "total_component_count",
)


def _valid_instagram_candidate(
    post_id: str,
    candidate: object,
) -> bool:
    if not isinstance(candidate, dict):
        return False

    candidate_id = str(
        candidate.get("post_id") or ""
    ).strip()

    if candidate_id != post_id:
        return False

    raw_url = str(
        candidate.get("post_url")
        or candidate.get("canonical_url")
        or ""
    ).strip()

    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return False

    host = parsed.netloc.lower().split(":", 1)[0]

    if host not in {
        "instagram.com",
        "www.instagram.com",
    }:
        return False

    if not parsed.path.startswith(("/p/", "/reel/", "/tv/")):
        return False

    components = candidate.get("components")

    if not isinstance(components, list) or not components:
        return False

    seen_indexes = set()

    for fallback_index, component in enumerate(
        components,
        start=1,
    ):
        if not isinstance(component, dict):
            return False

        try:
            component_index = int(
                component.get("component_index")
                or fallback_index
            )
        except (TypeError, ValueError):
            return False

        media_id = str(
            component.get("media_id") or ""
        ).strip()

        if (
            component_index < 1
            or component_index in seen_indexes
            or not media_id
        ):
            return False

        seen_indexes.add(component_index)

    return True


def _manifest_state(
    selection_posts: dict[str, dict[str, Any]],
    post_id: str,
) -> dict[str, Any]:
    raw = selection_posts.get(post_id)

    if not isinstance(raw, dict):
        return {}

    return {
        key: raw[key]
        for key in STATE_KEYS
        if key in raw
    }


def _apply_manifest_state(
    candidate: dict[str, Any],
    selection_state: dict[str, Any],
) -> dict[str, Any]:
    result = dict(candidate)
    result.update(selection_state)

    components = result.get("components", [])
    component_count = len(components)

    try:
        declared_count = int(
            result.get("total_component_count")
            or result.get("component_count_returned")
            or component_count
        )
    except (TypeError, ValueError):
        declared_count = component_count

    if (
        component_count < 1
        or declared_count != component_count
    ):
        raise RuntimeError(
            "RECOVERED_COMPONENT_COUNT_MISMATCH: "
            + str(result.get("post_id") or "")
        )

    selected = result.get("selected_components", [])

    if selected:
        normalized_selected = []

        for raw_index in selected:
            try:
                component_index = int(raw_index)
            except (TypeError, ValueError):
                raise RuntimeError(
                    "RECOVERED_COMPONENT_INDEX_INVALID"
                )

            if (
                component_index < 1
                or component_index > component_count
            ):
                raise RuntimeError(
                    "RECOVERED_COMPONENT_OUT_OF_RANGE: "
                    + str(component_index)
                )

            if component_index not in normalized_selected:
                normalized_selected.append(component_index)

        result["selected_components"] = sorted(
            normalized_selected
        )

    result["total_component_count"] = component_count
    result["component_count_returned"] = component_count

    if result.get("reimport_deleted_as_new"):
        result["resume_partial"] = False
        result["restore_deleted"] = False
        result["existing_post_number"] = None
        result["imported_component_numbers"] = []
        result["imported_media_ids"] = []
        result["available_component_numbers"] = list(
            range(1, component_count + 1)
        )
        result["selected_components"] = list(
            range(1, component_count + 1)
        )
        result["discovery_status"] = (
            "NEW_POST_CANDIDATE"
        )
    elif result.get("restore_deleted"):
        result["resume_partial"] = False
        result["imported_component_numbers"] = []
        result["imported_media_ids"] = []
        result["available_component_numbers"] = list(
            range(1, component_count + 1)
        )
        result["selected_components"] = list(
            range(1, component_count + 1)
        )
        result["discovery_status"] = (
            "RESTORE_DELETED_POST"
        )
    elif result.get("resume_partial"):
        result["discovery_status"] = (
            "NEW_POST_CANDIDATE"
        )
    else:
        result["discovery_status"] = (
            result.get("discovery_status")
            or "NEW_POST_CANDIDATE"
        )

    return result


def recover_selected_posts(
    current_posts: list[dict[str, Any]],
    requested_post_ids: list[str],
    reports_directory: Path,
    selection_posts: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Resolve explicit GUI selections from the current discovery
    snapshot and, when absent there, from previous reports.

    The GUI manifest is authoritative only for mutable local state.
    URL and component identities must come from a validated
    discovery report.
    """
    selection_posts = selection_posts or {}

    requested = []
    seen = set()

    for raw_post_id in requested_post_ids:
        post_id = str(raw_post_id).strip()

        if not post_id or not post_id.isdigit():
            raise RuntimeError(
                "INVALID_SELECTED_POST_ID: " + post_id
            )

        if post_id not in seen:
            requested.append(post_id)
            seen.add(post_id)

    resolved: dict[str, dict[str, Any]] = {}
    recovered_ids = []

    for candidate in current_posts:
        if not isinstance(candidate, dict):
            continue

        post_id = str(
            candidate.get("post_id") or ""
        ).strip()

        if (
            post_id in seen
            and _valid_instagram_candidate(
                post_id,
                candidate,
            )
        ):
            resolved[post_id] = _apply_manifest_state(
                candidate,
                _manifest_state(
                    selection_posts,
                    post_id,
                ),
            )

    missing = [
        post_id
        for post_id in requested
        if post_id not in resolved
    ]

    report_paths = sorted(
        reports_directory.glob(
            "instagram_discovery_posts_*.json"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for report_path in report_paths:
        if not missing:
            break

        try:
            payload = json.loads(
                report_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            continue

        # DISCOVERY_REPORT_LIST_PAYLOAD_HOTFIX_V1
        if isinstance(payload, dict):
            raw_posts = payload.get("posts", [])
        elif isinstance(payload, list):
            raw_posts = payload
        else:
            continue

        if not isinstance(raw_posts, list):
            continue

        for candidate in raw_posts:
            if not isinstance(candidate, dict):
                continue

            post_id = str(
                candidate.get("post_id") or ""
            ).strip()

            if post_id not in missing:
                continue

            if not _valid_instagram_candidate(
                post_id,
                candidate,
            ):
                continue

            recovered = _apply_manifest_state(
                candidate,
                _manifest_state(
                    selection_posts,
                    post_id,
                ),
            )
            recovered[
                "_recovered_discovery_report"
            ] = str(report_path)

            resolved[post_id] = recovered
            recovered_ids.append(post_id)
            missing.remove(post_id)

    if missing:
        raise RuntimeError(
            "SELECTED_POSTS_NOT_FOUND_IN_DISCOVERY_HISTORY: "
            + ", ".join(missing)
        )

    return (
        [resolved[post_id] for post_id in requested],
        recovered_ids,
    )
