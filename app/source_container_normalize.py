from __future__ import annotations

from typing import Any

from app.source_models import (
    NormalizedSourceBundle,
    SourceContainer,
)


class SourceContainerNormalizeError(ValueError):
    pass


def text_value(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()

        if text:
            return text

    return None


def integer_value(*values: Any) -> int | None:
    for value in values:
        if value is None or value == "":
            continue

        try:
            return int(value)
        except (TypeError, ValueError):
            continue

    return None


def normalize_account_key(value: str) -> str:
    normalized = value.strip().lstrip("@").lower()

    if not normalized:
        raise SourceContainerNormalizeError(
            "Instagram account name is required"
        )

    return normalized


def normalize_instagram_collections(
    payload: dict[str, Any],
    *,
    account_name: str,
) -> NormalizedSourceBundle:
    account_key = normalize_account_key(account_name)
    account_source_id = f"account:{account_key}"

    containers = [
        SourceContainer(
            platform="instagram",
            kind="account",
            source_id=account_source_id,
            name=account_name.strip().lstrip("@"),
            metadata={
                "username": account_key,
                "virtual": True,
            },
        )
    ]

    raw_collections = payload.get("collections", [])

    if not isinstance(raw_collections, list):
        raise SourceContainerNormalizeError(
            "Instagram collections are not a list"
        )

    for position, item in enumerate(
        raw_collections,
        start=1,
    ):
        if not isinstance(item, dict):
            continue

        raw_id = text_value(
            item.get("collection_id"),
            item.get("id"),
        )
        name = text_value(
            item.get("name"),
            item.get("collection_name"),
            raw_id,
        )

        if not raw_id or not name:
            continue

        source_id = (
            f"{account_source_id}:collection:{raw_id}"
        )

        containers.append(
            SourceContainer(
                platform="instagram",
                kind="collection",
                source_id=source_id,
                name=name,
                parent_source_id=account_source_id,
                item_count=integer_value(
                    item.get("media_count"),
                    item.get("item_count"),
                ),
                metadata={
                    "raw_collection_id": raw_id,
                    "collection_type": text_value(
                        item.get("collection_type"),
                        item.get("type"),
                    ),
                    "position": integer_value(
                        item.get("position"),
                        position,
                    ),
                    "system": (
                        raw_id
                        == "ALL_MEDIA_AUTO_COLLECTION"
                    ),
                },
            )
        )

    return NormalizedSourceBundle(
        platform="instagram",
        containers=containers,
    )


def normalize_pinterest_boards(
    payload: dict[str, Any],
) -> NormalizedSourceBundle:
    raw_boards = payload.get("boards", [])

    if not isinstance(raw_boards, list):
        raise SourceContainerNormalizeError(
            "Pinterest boards are not a list"
        )

    containers: list[SourceContainer] = []

    for item in raw_boards:
        if not isinstance(item, dict):
            continue

        board_id = text_value(item.get("id"))
        name = text_value(
            item.get("name"),
            item.get("title"),
            board_id,
        )

        if not board_id or not name:
            continue

        containers.append(
            SourceContainer(
                platform="pinterest",
                kind="board",
                source_id=board_id,
                name=name,
                item_count=integer_value(
                    item.get("pin_count"),
                    item.get("pins_count"),
                ),
                metadata={
                    "url": text_value(item.get("url")),
                    "privacy": text_value(
                        item.get("privacy")
                    ),
                    "section_count": integer_value(
                        item.get("section_count")
                    ),
                    "sectionless_pin_count": integer_value(
                        item.get("sectionless_pin_count")
                    ),
                },
            )
        )

    return NormalizedSourceBundle(
        platform="pinterest",
        containers=containers,
    )


def normalize_pinterest_sections(
    payload: dict[str, Any],
) -> NormalizedSourceBundle:
    board = payload.get("board", {})

    if not isinstance(board, dict):
        raise SourceContainerNormalizeError(
            "Pinterest board is not an object"
        )

    board_id = text_value(board.get("id"))
    board_name = text_value(
        board.get("name"),
        board.get("title"),
        board_id,
    )

    if not board_id or not board_name:
        raise SourceContainerNormalizeError(
            "Pinterest board identity is missing"
        )

    containers = [
        SourceContainer(
            platform="pinterest",
            kind="board",
            source_id=board_id,
            name=board_name,
            item_count=integer_value(
                board.get("pin_count")
            ),
            metadata={
                "url": text_value(board.get("url")),
                "section_count": integer_value(
                    board.get("section_count")
                ),
                "sectionless_pin_count": integer_value(
                    board.get("sectionless_pin_count")
                ),
            },
        )
    ]

    raw_sections = payload.get("sections", [])

    if not isinstance(raw_sections, list):
        raise SourceContainerNormalizeError(
            "Pinterest sections are not a list"
        )

    for position, item in enumerate(
        raw_sections,
        start=1,
    ):
        if not isinstance(item, dict):
            continue

        section_id = text_value(item.get("id"))
        name = text_value(
            item.get("name"),
            item.get("title"),
            section_id,
        )

        if not section_id or not name:
            continue

        containers.append(
            SourceContainer(
                platform="pinterest",
                kind="section",
                source_id=section_id,
                name=name,
                parent_source_id=board_id,
                item_count=integer_value(
                    item.get("pin_count"),
                    item.get("pins_count"),
                ),
                metadata={
                    "url": text_value(item.get("url")),
                    "slug": text_value(item.get("slug")),
                    "position": integer_value(
                        item.get("position"),
                        position,
                    ),
                },
            )
        )

    return NormalizedSourceBundle(
        platform="pinterest",
        containers=containers,
    )


def normalize_container_payload(
    platform: str,
    payload: dict[str, Any],
    *,
    account_name: str | None = None,
) -> NormalizedSourceBundle:
    normalized_platform = platform.strip().lower()
    operation = text_value(payload.get("operation"))

    if normalized_platform == "instagram":
        if not account_name:
            raise SourceContainerNormalizeError(
                "Instagram account name is required"
            )

        return normalize_instagram_collections(
            payload,
            account_name=account_name,
        )

    if normalized_platform == "pinterest":
        if operation == "list-boards":
            return normalize_pinterest_boards(payload)

        if operation == "list-sections":
            return normalize_pinterest_sections(payload)

        raise SourceContainerNormalizeError(
            f"Unsupported Pinterest operation: {operation}"
        )

    raise SourceContainerNormalizeError(
        f"Unsupported platform: {platform}"
    )


def run_self_test() -> dict[str, int]:
    instagram = normalize_instagram_collections(
        {
            "operation": "list_collections",
            "collections": [
                {
                    "collection_id": (
                        "ALL_MEDIA_AUTO_COLLECTION"
                    ),
                    "name": "All posts",
                    "collection_type": (
                        "ALL_MEDIA_AUTO_COLLECTION"
                    ),
                },
                {
                    "collection_id": "collection-1",
                    "name": "motion",
                    "collection_type": "MEDIA",
                },
            ],
        },
        account_name="maximus",
    )

    pinterest = normalize_pinterest_sections({
        "operation": "list-sections",
        "board": {
            "id": "board-1",
            "name": "References",
            "section_count": 1,
        },
        "sections": [
            {
                "id": "section-1",
                "name": "Motion",
                "pin_count": 10,
            }
        ],
    })

    if len(instagram.containers) != 3:
        raise AssertionError(
            "Instagram hierarchy is incomplete"
        )

    if (
        instagram.containers[1].parent_source_id
        != instagram.containers[0].source_id
    ):
        raise AssertionError(
            "Instagram collection parent is invalid"
        )

    if len(pinterest.containers) != 2:
        raise AssertionError(
            "Pinterest hierarchy is incomplete"
        )

    if (
        pinterest.containers[1].parent_source_id
        != pinterest.containers[0].source_id
    ):
        raise AssertionError(
            "Pinterest section parent is invalid"
        )

    return {
        "instagram_containers": len(
            instagram.containers
        ),
        "pinterest_containers": len(
            pinterest.containers
        ),
    }


if __name__ == "__main__":
    print(run_self_test())
