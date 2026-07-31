from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .source_models import (
    NormalizedSourceBundle,
    SourceContainer,
    SourceMedia,
    SourcePublication,
)


PLATFORM = "pinterest"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue

        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
            continue

        if isinstance(value, (int, float)):
            return str(value)

    return ""


def _integer(*values: Any) -> int | None:
    for value in values:
        try:
            if value is not None and str(value).strip():
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _number(*values: Any) -> float | None:
    for value in values:
        try:
            if value is not None and str(value).strip():
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _created_at(data: dict[str, Any]) -> str | None:
    for key in (
        "created_at",
        "created_time",
        "timestamp",
        "date",
        "upload_date",
        "board_order_modified_at",
    ):
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def canonical_pin_url(pin_id: str) -> str:
    safe_id = quote(pin_id, safe="_-")
    return f"https://www.pinterest.com/pin/{safe_id}/"


def _publication_type(data: dict[str, Any]) -> str:
    if _dict(data.get("story_pin_data")):
        return "story_pin"

    if _dict(data.get("carousel_data")):
        return "carousel"

    if data.get("is_video") or _dict(data.get("videos")):
        return "video"

    extension = _text(data.get("extension")).lower()

    if extension == "gif":
        return "gif"
    if extension in {"mp4", "m3u8", "mov"}:
        return "video"
    if extension in {"mp3", "m4a", "aac", "wav"}:
        return "audio"

    return "image"


def _media_type(data: dict[str, Any]) -> str:
    extension = _text(data.get("extension")).lower()

    if extension in {"mp4", "m3u8", "mov", "webm"}:
        return "video"
    if extension in {"mp3", "m4a", "aac", "wav", "ogg"}:
        return "audio"
    if extension == "gif":
        return "gif"
    if extension in {"jpg", "jpeg", "png", "webp", "heic", "avif"}:
        return "image"

    if data.get("is_video") or _dict(data.get("videos")):
        return "video"

    return "unknown"


def _media_url(data: dict[str, Any]) -> str | None:
    direct = _text(data.get("url"))

    # In gallery-dl sidecars, url often points to the actual media.
    if direct and (
        "pinimg.com" in direct
        or direct.lower().split("?")[0].endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".webp",
             ".heic", ".mp4", ".m3u8", ".mp3")
        )
    ):
        return direct

    images = _dict(data.get("images"))
    original = _dict(images.get("orig"))
    image_url = _text(original.get("url"))

    if image_url:
        return image_url

    videos = _dict(data.get("videos"))
    video_list = _dict(videos.get("video_list"))

    for key in ("V_720P", "V_EXP7", "V_HLSV4", "V_HLSV3_MOBILE"):
        candidate = _dict(video_list.get(key))
        video_url = _text(candidate.get("url"))
        if video_url:
            return video_url

    return direct or None


def _media_id(data: dict[str, Any], pin_id: str, index: int) -> str:
    value = _text(
        data.get("media_id"),
        data.get("page_id"),
        data.get("story_id"),
        data.get("image_signature"),
    )

    if value:
        return value

    return f"{pin_id}:{index}"


def normalize_pinterest_sidecar(
    data: dict[str, Any],
    *,
    local_filename: str | None = None,
) -> NormalizedSourceBundle:
    """Normalize one gallery-dl Pinterest metadata dictionary."""

    if not isinstance(data, dict):
        raise TypeError("Pinterest metadata must be a dictionary")

    pin_id = _text(data.get("id"), data.get("pin_id"))

    if not pin_id:
        raise ValueError("Pinterest metadata has no Pin ID")

    board = _dict(data.get("board"))
    section = _dict(data.get("section"))

    board_id = _text(board.get("id"), data.get("board_id")) or None
    board_name = _text(
        board.get("name"),
        board.get("title"),
        data.get("board_name"),
        board_id,
    )

    section_id = _text(section.get("id"), data.get("section_id")) or None
    section_name = _text(
        section.get("title"),
        section.get("name"),
        data.get("section_name"),
        section_id,
    )

    containers: list[SourceContainer] = []
    warnings: list[str] = []

    if board_id:
        containers.append(
            SourceContainer(
                platform=PLATFORM,
                kind="board",
                source_id=board_id,
                name=board_name or board_id,
                item_count=_integer(
                    board.get("pin_count"),
                    board.get("pins_count"),
                    data.get("board_pin_count"),
                ),
                metadata={
                    "section_count": _integer(board.get("section_count")),
                    "sectionless_pin_count": _integer(
                        board.get("sectionless_pin_count")
                    ),
                    "privacy": _text(board.get("privacy")) or None,
                    "url": _text(board.get("url")) or None,
                },
            )
        )
    else:
        warnings.append(f"Pin {pin_id} has no Board ID")

    if section_id:
        containers.append(
            SourceContainer(
                platform=PLATFORM,
                kind="section",
                source_id=section_id,
                name=section_name or section_id,
                parent_source_id=board_id,
                item_count=_integer(
                    section.get("pin_count"),
                    section.get("pins_count"),
                ),
                metadata={
                    "slug": _text(section.get("slug")) or None,
                    "url": _text(section.get("url")) or None,
                },
            )
        )

    index = _integer(data.get("num")) or 1
    media_url = _media_url(data)
    extension = _text(data.get("extension")).lower() or None
    filename = local_filename or _text(data.get("filename")) or None

    media = SourceMedia(
        source_media_id=_media_id(data, pin_id, index),
        index=index,
        media_type=_media_type(data),
        url=media_url,
        extension=extension,
        width=_integer(data.get("width")),
        height=_integer(data.get("height")),
        duration=_number(data.get("duration")),
        local_filename=filename,
        metadata={
            "component_count": _integer(data.get("count")),
            "page_id": _text(data.get("page_id")) or None,
            "story_id": _text(data.get("story_id")) or None,
        },
    )

    title = _text(
        data.get("title"),
        data.get("grid_title"),
        data.get("seo_alt_text"),
        data.get("alt_text"),
    )

    description = _text(
        data.get("description"),
        data.get("seo_alt_text"),
        data.get("alt_text"),
    )

    publication = SourcePublication(
        platform=PLATFORM,
        source_id=pin_id,
        container_id=board_id,
        section_id=section_id,
        title=title,
        description=description,
        canonical_url=canonical_pin_url(pin_id),
        publication_type=_publication_type(data),
        created_at=_created_at(data),
        media=[media],
        metadata={
            "pinner_id": _text(_dict(data.get("pinner")).get("id")) or None,
            "pinner_username": _text(
                _dict(data.get("pinner")).get("username")
            ) or None,
            "external_link": _text(data.get("link")) or None,
            "repin_count": _integer(data.get("repin_count")),
            "is_repin": bool(data.get("is_repin")),
            # Missing section metadata does not mean the pin is sectionless.
            "section_membership_known": bool(section_id),
        },
    )

    return NormalizedSourceBundle(
        platform=PLATFORM,
        containers=containers,
        publications=[publication],
        warnings=warnings,
    )



def staging_metadata_from_sidecar(
    data: dict[str, Any],
    *,
    local_filename: str | None = None,
) -> dict[str, Any]:
    """Convert a raw Pinterest sidecar to importer metadata.

    The shared Eagle importer consumes this platform-neutral mapping
    instead of knowing Pinterest field names or URL rules.
    """

    bundle = normalize_pinterest_sidecar(
        data,
        local_filename=local_filename,
    )

    if len(bundle.publications) != 1:
        raise ValueError(
            "Pinterest sidecar must normalize to one publication"
        )

    publication = bundle.publications[0]

    if not publication.media:
        raise ValueError(
            "Pinterest sidecar has no normalized media"
        )

    requested_index = _integer(
        data.get("num")
    ) or 1

    media = next(
        (
            item
            for item in publication.media
            if int(item.index) == requested_index
        ),
        publication.media[0],
    )

    declared_component_count = _integer(
        data.get("count"),
        media.metadata.get("component_count"),
    )

    if (
        declared_component_count is None
        or declared_component_count < 1
    ):
        declared_component_count = len(
            publication.media
        )

    display_name = (
        publication.title.strip()
        if publication.title.strip()
        else f"Pinterest Pin {publication.source_id}"
    )

    return {
        "post_id": publication.source_id,
        "media_id": media.source_media_id,
        "canonical_url": publication.canonical_url,
        "display_name": display_name,
        "description": publication.description,
        "component_index": int(media.index),
        "total_component_count": int(
            declared_component_count
        ),
        "publication_type": (
            publication.publication_type
        ),
        "container_ids": [
            value
            for value in (
                publication.container_id,
                publication.section_id,
                *publication.container_ids,
            )
            if value
        ],
    }

def merge_pinterest_bundles(
    bundles: list[NormalizedSourceBundle],
) -> NormalizedSourceBundle:
    """Merge sidecars and deduplicate boards, sections, pins and media."""

    container_map: dict[tuple[str, str], SourceContainer] = {}
    publication_map: dict[str, SourcePublication] = {}
    warnings: list[str] = []

    for bundle in bundles:
        warnings.extend(bundle.warnings)

        for container in bundle.containers:
            key = (container.kind, container.source_id)
            existing = container_map.get(key)

            if existing is None:
                container_map[key] = container
            else:
                if not existing.name and container.name:
                    existing.name = container.name
                if existing.item_count is None:
                    existing.item_count = container.item_count
                existing.metadata.update({
                    key: value
                    for key, value in container.metadata.items()
                    if value is not None
                })

        for publication in bundle.publications:
            existing = publication_map.get(publication.source_id)

            if existing is None:
                publication_map[publication.source_id] = publication
                continue

            if existing.section_id is None and publication.section_id:
                existing.section_id = publication.section_id
                existing.metadata["section_membership_known"] = True

            if existing.container_id is None and publication.container_id:
                existing.container_id = publication.container_id

            if not existing.title and publication.title:
                existing.title = publication.title

            if not existing.description and publication.description:
                existing.description = publication.description

            known_media = {item.identity() for item in existing.media}

            for media in publication.media:
                if media.identity() not in known_media:
                    existing.media.append(media)
                    known_media.add(media.identity())

            existing.media.sort(key=lambda item: (
                item.index,
                item.source_media_id,
                item.url or "",
            ))

    containers = sorted(
        container_map.values(),
        key=lambda item: (
            0 if item.kind == "board" else 1,
            (item.name or "").casefold(),
            item.source_id,
        ),
    )

    publications = sorted(
        publication_map.values(),
        key=lambda item: (
            item.container_id or "",
            item.section_id or "",
            item.source_id,
        ),
    )

    return NormalizedSourceBundle(
        platform=PLATFORM,
        containers=containers,
        publications=publications,
        warnings=sorted(set(warnings)),
    )


def normalize_json_file(path: Path) -> NormalizedSourceBundle:
    data = json.loads(path.read_text(encoding="utf-8"))
    local_name = path.name[:-5] if path.name.endswith(".json") else None
    return normalize_pinterest_sidecar(
        data,
        local_filename=local_name,
    )


def run_self_test(sample_directory: Path) -> dict[str, int]:
    paths = sorted(sample_directory.glob("*.json"))

    if not paths:
        raise RuntimeError(f"No Pinterest samples in {sample_directory}")

    bundles = [normalize_json_file(path) for path in paths]
    merged = merge_pinterest_bundles(bundles)

    if not merged.publications:
        raise RuntimeError("No Pinterest publications normalized")

    if any(not item.source_id for item in merged.publications):
        raise RuntimeError("Normalized publication without Pin ID")

    synthetic = {
        "id": "123456789",
        "title": "Synthetic section test",
        "extension": "jpg",
        "url": "https://i.pinimg.com/originals/test.jpg",
        "board": {
            "id": "board-1",
            "name": "Board One",
            "pin_count": 10,
            "section_count": 1,
        },
        "section": {
            "id": "section-1",
            "title": "Section One",
            "pin_count": 4,
        },
    }

    synthetic_bundle = normalize_pinterest_sidecar(synthetic)
    synthetic_pin = synthetic_bundle.publications[0]

    if synthetic_pin.container_id != "board-1":
        raise RuntimeError("Synthetic Board ID normalization failed")

    if synthetic_pin.section_id != "section-1":
        raise RuntimeError("Synthetic Section ID normalization failed")

    return {
        "sample_files": len(paths),
        "unique_pins": len(merged.publications),
        "containers": len(merged.containers),
        "media_components": sum(
            len(item.media) for item in merged.publications
        ),
        "warnings": len(merged.warnings),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run normalization test on preserved Pinterest JSON",
    )
    parser.add_argument(
        "--samples",
        type=Path,
        default=(
            Path(__file__).resolve().parents[1]
            / "data"
            / "legacy_pinterest"
            / "sample_json"
        ),
    )
    args = parser.parse_args()

    if args.self_test:
        result = run_self_test(args.samples)

        for key, value in result.items():
            print(f"{key.upper()}={value}")

        print("PINTEREST_NORMALIZER_SELF_TEST=SUCCESS")
        return 0

    parser.error("Use --self-test")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
