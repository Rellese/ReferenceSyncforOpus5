from __future__ import annotations

from typing import Any

from app.source_models import (
    NormalizedSourceBundle,
    SourceContainer,
    SourceMedia,
    SourcePublication,
)


IMAGE_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "webp",
    "gif",
    "avif",
    "heic",
}

VIDEO_EXTENSIONS = {
    "mp4",
    "mov",
    "webm",
    "mkv",
    "m4v",
}


class InstagramNormalizationError(ValueError):
    """Raised when Instagram discovery data is incomplete."""


def text_value(*values: Any) -> str:
    for value in values:
        if value is None:
            continue

        result = str(value).strip()

        if result:
            return result

    return ""


def integer_value(
    value: Any,
    default: int,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default

    return number if number > 0 else default


def canonical_instagram_url(
    post_id: str,
    shortcode: str,
    url: str,
) -> str:
    normalized_url = text_value(url)

    if normalized_url:
        return normalized_url.rstrip("/") + "/"

    if shortcode:
        return (
            "https://www.instagram.com/p/"
            f"{shortcode}/"
        )

    return (
        "https://www.instagram.com/p/"
        f"id:{post_id}/"
    )


def component_media_type(
    component: dict[str, Any],
) -> str:
    declared = text_value(
        component.get("media_type"),
        component.get("type"),
    ).lower()

    if "video" in declared:
        return "video"

    if "image" in declared or "photo" in declared:
        return "image"

    extension = text_value(
        component.get("extension")
    ).lower().lstrip(".")

    if extension in VIDEO_EXTENSIONS:
        return "video"

    if extension in IMAGE_EXTENSIONS:
        return "image"

    return "unknown"


def normalize_components(
    post: dict[str, Any],
    post_id: str,
) -> list[SourceMedia]:
    raw_components = post.get("component_items")

    if not isinstance(raw_components, list):
        raw_components = post.get("components")

    if not isinstance(raw_components, list):
        raw_components = []

    media_ids = post.get("media_ids")

    if not isinstance(media_ids, list):
        media_ids = []

    if not raw_components:
        raw_components = [
            {
                "media_id": media_id,
                "component_index": index,
            }
            for index, media_id in enumerate(
                media_ids,
                start=1,
            )
        ]

    normalized: list[SourceMedia] = []
    seen: set[tuple[str, int]] = set()

    for fallback_index, raw_component in enumerate(
        raw_components,
        start=1,
    ):
        if not isinstance(raw_component, dict):
            continue

        component_index = integer_value(
            raw_component.get("component_index"),
            fallback_index,
        )

        media_id = text_value(
            raw_component.get("media_id"),
            (
                media_ids[component_index - 1]
                if component_index <= len(media_ids)
                else None
            ),
            f"{post_id}:{component_index}",
        )

        identity = (media_id, component_index)

        if identity in seen:
            continue

        seen.add(identity)

        extension = text_value(
            raw_component.get("extension")
        ).lower().lstrip(".") or None

        normalized.append(
            SourceMedia(
                source_media_id=media_id,
                index=component_index,
                media_type=component_media_type(
                    raw_component
                ),
                url=text_value(
                    raw_component.get("source_url"),
                    raw_component.get("preview_url"),
                    raw_component.get("url"),
                ) or None,
                extension=extension,
                width=raw_component.get(
                    "preview_width"
                ),
                height=raw_component.get(
                    "preview_height"
                ),
                metadata={
                    key: value
                    for key, value in raw_component.items()
                    if key not in {
                        "source_url",
                        "preview_url",
                        "url",
                    }
                },
            )
        )

    normalized.sort(
        key=lambda item: (
            item.index,
            item.source_media_id,
        )
    )

    return normalized


def normalize_instagram_post(
    post: dict[str, Any],
    *,
    account_username: str | None = None,
    collection_source_id: str = "saved",
    collection_name: str = "Saved",
) -> NormalizedSourceBundle:
    if not isinstance(post, dict):
        raise TypeError("Instagram post must be a dictionary")

    post_id = text_value(
        post.get("post_id"),
        post.get("external_id"),
    )

    if not post_id:
        raise InstagramNormalizationError(
            "Instagram post has no post_id"
        )

    username = text_value(
        account_username,
        post.get("username"),
        post.get("owner_username"),
    ).lstrip("@")

    if not username:
        raise InstagramNormalizationError(
            f"Instagram post {post_id} has no username"
        )

    shortcode = text_value(
        post.get("post_shortcode"),
        post.get("shortcode"),
    )

    canonical_url = canonical_instagram_url(
        post_id,
        shortcode,
        text_value(
            post.get("post_url"),
            post.get("canonical_url"),
        ),
    )

    account_id = f"account:{username.lower()}"

    normalized_collection_id = text_value(
        post.get("collection_id"),
        post.get("saved_collection_id"),
        collection_source_id,
    )

    normalized_collection_name = text_value(
        post.get("collection_name"),
        post.get("saved_collection_name"),
        collection_name,
    )

    collection_id = (
        f"{account_id}:collection:"
        f"{normalized_collection_id}"
        if normalized_collection_id
        else None
    )

    containers = [
        SourceContainer(
            platform="instagram",
            kind="ACCOUNT",
            source_id=account_id,
            name=f"@{username}",
            item_count=None,
            metadata={
                "canonical_url": (
                    "https://www.instagram.com/"
                    f"{username}/"
                ),
                "username": username,
            },
        )
    ]

    container_chain = [account_id]

    if collection_id:
        containers.append(
            SourceContainer(
                platform="instagram",
                kind="COLLECTION",
                source_id=collection_id,
                name=normalized_collection_name,
                parent_source_id=account_id,
                item_count=None,
                metadata={
                    "collection_source_id": (
                        normalized_collection_id
                    ),
                    "virtual": (
                        normalized_collection_id == "saved"
                    ),
                },
            )
        )
        container_chain.append(collection_id)

    media = normalize_components(post, post_id)

    if not media:
        raise InstagramNormalizationError(
            f"Instagram post {post_id} has no media"
        )

    publication_type = (
        "carousel"
        if len(media) > 1
        else media[0].media_type
    )

    publication = SourcePublication(
        platform="instagram",
        source_id=post_id,
        container_id=account_id,
        section_id=None,
        title=text_value(
            post.get("title"),
            post.get("description"),
        ),
        description=text_value(
            post.get("description"),
            post.get("caption"),
        ),
        canonical_url=canonical_url,
        publication_type=publication_type,
        created_at=text_value(
            post.get("post_date"),
            post.get("created_at"),
            post.get("date"),
        ) or None,
        media=media,
        metadata={
            "username": username,
            "shortcode": shortcode or None,
            "discovery_status": post.get(
                "discovery_status"
            ),
            "resume_partial": bool(
                post.get("resume_partial")
            ),
            "existing_post_number": post.get(
                "existing_post_number"
            ),
        },
        container_ids=container_chain,
    )

    return NormalizedSourceBundle(
        platform="instagram",
        containers=containers,
        publications=[publication],
        warnings=[],
    )


def merge_instagram_bundles(
    bundles: list[NormalizedSourceBundle],
) -> NormalizedSourceBundle:
    containers = {}
    publications = {}
    warnings = []

    for bundle in bundles:
        if bundle.platform != "instagram":
            raise InstagramNormalizationError(
                f"Unexpected platform: {bundle.platform}"
            )

        for container in bundle.containers:
            containers[container.source_id] = container

        for publication in bundle.publications:
            publications[publication.source_id] = publication

        warnings.extend(bundle.warnings)

    return NormalizedSourceBundle(
        platform="instagram",
        containers=list(containers.values()),
        publications=list(publications.values()),
        warnings=warnings,
    )


def normalize_instagram_posts(
    posts: list[dict[str, Any]],
    *,
    account_username: str | None = None,
    collection_source_id: str = "saved",
    collection_name: str = "Saved",
) -> NormalizedSourceBundle:
    bundles = [
        normalize_instagram_post(
            post,
            account_username=account_username,
            collection_source_id=collection_source_id,
            collection_name=collection_name,
        )
        for post in posts
    ]

    return merge_instagram_bundles(bundles)
