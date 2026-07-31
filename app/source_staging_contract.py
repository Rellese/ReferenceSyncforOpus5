from __future__ import annotations

from app.source_adapter import (
    LEGACY_DEFAULT_SOURCE_CODE,
    SourceAdapterError,
    get_source_adapter,
)

import re
from typing import Any


CONTRACT_VERSION = 1
SOURCE_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,49}$")
MARKER_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class StagingContractError(ValueError):
    """Raised when a staging job violates the shared contract."""


def normalized_strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []

    result = []

    for item in value:
        normalized = str(item or "").strip()

        if normalized and normalized not in result:
            result.append(normalized)

    return result


def instagram_container_chain(
    post: dict[str, Any],
) -> list[str]:
    username = str(
        post.get("username")
        or post.get("owner_username")
        or ""
    ).strip().lstrip("@").lower()

    if not username:
        return []

    account_id = f"account:{username}"
    collection_id = f"{account_id}:collection:saved"

    return [account_id, collection_id]


def ensure_staging_contract(
    job: dict[str, Any],
    *,
    default_source_code: str,
    default_tags: list[str] | tuple[str, ...] = (),
    default_folder_ids: list[str] | tuple[str, ...] = (),
    default_name_marker: str,
) -> dict[str, Any]:
    if not isinstance(job, dict):
        raise TypeError("Staging job must be a dictionary")

    source_code = str(
        job.get("source_code")
        or default_source_code
    ).strip().lower()

    if not SOURCE_CODE_RE.fullmatch(source_code):
        raise StagingContractError(
            f"Invalid source_code: {source_code!r}"
        )

    name_marker_default = str(
        default_name_marker
    ).strip()

    if not MARKER_RE.fullmatch(name_marker_default):
        raise StagingContractError(
            f"Invalid default name marker: "
            f"{name_marker_default!r}"
        )

    eagle_raw = job.get("eagle")

    if eagle_raw is None:
        eagle_raw = {}

    if not isinstance(eagle_raw, dict):
        raise StagingContractError(
            "job.eagle must be an object"
        )

    name_marker = str(
        eagle_raw.get("name_marker")
        or name_marker_default
    ).strip()

    if not MARKER_RE.fullmatch(name_marker):
        raise StagingContractError(
            f"Invalid Eagle name marker: {name_marker!r}"
        )

    tags = normalized_strings(
        eagle_raw.get("tags")
    )

    if not tags:
        tags = normalized_strings(default_tags)

    folder_ids = normalized_strings(
        eagle_raw.get("folder_ids")
    )

    if not folder_ids:
        folder_ids = normalized_strings(
            default_folder_ids
        )

    posts = job.get("posts")

    if posts is None:
        posts = []
        job["posts"] = posts

    if not isinstance(posts, list):
        raise StagingContractError(
            "job.posts must be a list"
        )

    job_container_chain = normalized_strings(
        job.get("container_chain")
    )

    for post in posts:
        if not isinstance(post, dict):
            raise StagingContractError(
                "Every job post must be an object"
            )

        container_ids = normalized_strings(
            post.get("container_ids")
        )

        if not container_ids and source_code == "instagram":
            container_ids = instagram_container_chain(
                post
            )

        post["container_ids"] = container_ids

        if (
            not job_container_chain
            and container_ids
        ):
            job_container_chain = list(container_ids)

    job["staging_contract_version"] = CONTRACT_VERSION
    job["source_code"] = source_code
    job["container_chain"] = job_container_chain
    job["eagle"] = {
        "tags": tags,
        "folder_ids": folder_ids,
        "name_marker": name_marker,
    }

    return job


def ensure_registered_staging_contract(
    job: dict,
    *,
    source_code: str | None = None,
) -> dict:
    """Apply defaults from the registered source adapter.

    Jobs created before the universal contract may not contain
    source_code. Such jobs use the centrally declared legacy source.
    """

    requested_source = str(
        source_code
        or job.get("source_code")
        or LEGACY_DEFAULT_SOURCE_CODE
    ).strip().lower()

    try:
        adapter = get_source_adapter(requested_source)
    except SourceAdapterError as exc:
        raise ValueError(str(exc)) from exc

    return ensure_staging_contract(
        job,
        default_source_code=adapter.source_code,
        default_tags=list(adapter.default_eagle_tags),
        default_folder_ids=list(
            adapter.default_eagle_folder_ids
        ),
        default_name_marker=adapter.default_name_marker,
    )

