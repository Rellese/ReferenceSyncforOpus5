"""
External gallery-dl extractor for ReferenceSync.

SMALLEST_INSTAGRAM_PREVIEW_METADATA_V1

This module does not download preview images. It only preserves the
smallest image candidate already present in Instagram REST metadata.

The installed gallery-dl package is not modified.
"""

from __future__ import annotations

import os

from gallery_dl.extractor.instagram import (
    InstagramSavedExtractor as _InstagramSavedExtractor,
)


def _positive_int(value) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return 0

    return result if result > 0 else 0


def _smallest_candidate(item: dict) -> dict | None:
    image_versions = item.get("image_versions2")

    if not isinstance(image_versions, dict):
        return None

    candidates = image_versions.get("candidates")

    if not isinstance(candidates, list):
        return None

    valid = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        url = str(candidate.get("url") or "").strip()
        width = _positive_int(candidate.get("width"))
        height = _positive_int(candidate.get("height"))

        if not url or not width or not height:
            continue

        valid.append({
            "url": url,
            "width": width,
            "height": height,
        })

    if not valid:
        return None

    preview = min(
        valid,
        key=lambda candidate: (
            candidate["width"] * candidate["height"],
            candidate["width"],
            candidate["height"],
        ),
    )

    return {
        "preview_url": preview["url"],
        "preview_width": preview["width"],
        "preview_height": preview["height"],
        "preview_candidate_count": len(valid),
        "preview_source": "instagram_image_versions2_smallest",
    }


REFERENCE_SYNC_DISCOVERY_PROFILES = {
    # Original gallery-dl safety interval.
    "safe": (6.0, 12.0),

    # Roughly halves artificial pagination waiting while
    # retaining randomized delays between Instagram requests.
    "balanced": (3.0, 6.0),
}


class InstagramSavedPreviewExtractor(
    _InstagramSavedExtractor
):
    """
    Saved-post extractor with lightweight preview metadata.

    It keeps the original gallery-dl behavior and only appends
    preview_* fields to each generated media dictionary.
    """

    category = _InstagramSavedExtractor.category
    subcategory = _InstagramSavedExtractor.subcategory

    def _init(self):
        super()._init()

        profile_name = str(
            os.environ.get(
                "REFERENCE_SYNC_DISCOVERY_SPEED",
                "safe",
            )
        ).strip().lower()

        interval = REFERENCE_SYNC_DISCOVERY_PROFILES.get(
            profile_name,
            REFERENCE_SYNC_DISCOVERY_PROFILES["safe"],
        )

        # Extractor.request() reads this attribute before every
        # Instagram request.
        self.request_interval = interval

        self.log.info(
            "ReferenceSync discovery speed profile: %s; "
            "request interval: %.1f-%.1f seconds",
            profile_name,
            interval[0],
            interval[1],
        )

    def _parse_post_rest(self, post):
        preview_by_media_id: dict[str, dict] = {}

        if isinstance(post, dict):
            if isinstance(post.get("items"), list):
                source_items = post["items"]
            elif isinstance(post.get("carousel_media"), list):
                source_items = post["carousel_media"]
            else:
                source_items = [post]

            for item in source_items:
                if not isinstance(item, dict):
                    continue

                media_id = str(item.get("pk") or "").strip()
                preview = _smallest_candidate(item)

                if media_id and preview:
                    preview_by_media_id[media_id] = preview

        data = super()._parse_post_rest(post)

        if not isinstance(data, dict):
            return data

        files = data.get("_files")

        if not isinstance(files, list):
            return data

        for media in files:
            if not isinstance(media, dict):
                continue

            media_id = str(media.get("media_id") or "").strip()
            preview = preview_by_media_id.get(media_id)

            if preview:
                media.update(preview)

        return data
