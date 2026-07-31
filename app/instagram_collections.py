from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from gallery_dl import cookies
from gallery_dl.extractor.instagram import (
    InstagramCollectionExtractor,
)

from app.browser_cookie_source import (
    gallery_dl_browser_spec,
    public_browser_details,
)


PROJECT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT / "config" / "local.json"
COLLECTIONS_ENDPOINT = (
    "https://www.instagram.com/api/v1/collections/list/"
)
COLLECTION_TYPES = (
    '["ALL_MEDIA_AUTO_COLLECTION",'
    '"MEDIA","AUDIO_AUTO_COLLECTION"]'
)


class InstagramCollectionsError(RuntimeError):
    pass


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}

    try:
        value = json.loads(
            CONFIG_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}

    return value if isinstance(value, dict) else {}


def parse_browser_specification(
    browser: str,
) -> tuple[str, str | None, str | None, None, str]:
    specification = gallery_dl_browser_spec(browser)
    browser_part, separator, keyring = specification.partition("+")
    browser_name, profile_separator, profile = (
        browser_part.partition(":")
    )

    return (
        browser_name,
        profile if profile_separator and profile else None,
        keyring if separator and keyring else None,
        None,
        ".instagram.com",
    )


def cookie_value(cookie_jar: Any, name: str) -> str:
    for cookie in cookie_jar:
        if cookie.name == name:
            return str(cookie.value or "")

    return ""


def normalize_collection(
    item: dict[str, Any],
    position: int,
) -> dict[str, Any]:
    collection_id = (
        item.get("collection_id")
        or item.get("id")
        or item.get("pk")
    )
    name = (
        item.get("collection_name")
        or item.get("name")
        or item.get("title")
    )
    collection_type = (
        item.get("collection_type")
        or item.get("type")
    )
    media_count = (
        item.get("media_count")
        or item.get("items_count")
        or item.get("count")
    )

    return {
        "position": position,
        "collection_id": (
            str(collection_id)
            if collection_id is not None
            else None
        ),
        "name": str(name or "").strip() or None,
        "collection_type": (
            str(collection_type)
            if collection_type is not None
            else None
        ),
        "media_count": media_count,
    }


def list_collections(
    browser: str,
    *,
    timeout: int = 30,
    maximum_pages: int = 100,
) -> dict[str, Any]:
    cookie_specification = parse_browser_specification(browser)

    try:
        browser_cookies = cookies.load_cookies(
            cookie_specification
        )
    except Exception as error:
        raise InstagramCollectionsError(
            f"Unable to read {browser} cookies: "
            f"{type(error).__name__}: {error}"
        ) from error

    csrf_token = cookie_value(
        browser_cookies,
        "csrftoken",
    )
    session_id_present = bool(
        cookie_value(browser_cookies, "sessionid")
    )

    if not csrf_token or not session_id_present:
        raise InstagramCollectionsError(
            "Instagram authorization cookies are missing"
        )

    extractor_url = (
        "https://www.instagram.com/"
        "reference_sync/saved/reference-sync/0"
    )
    extractor = InstagramCollectionExtractor.from_url(
        extractor_url
    )

    if extractor is None:
        raise InstagramCollectionsError(
            "Unable to initialize Instagram extractor"
        )

    try:
        extractor.initialize()

        for cookie in browser_cookies:
            extractor.session.cookies.set_cookie(cookie)

        extractor.csrf_token = csrf_token

        if not getattr(extractor, "www_claim", None):
            extractor.www_claim = "0"

        collections: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        next_max_id = ""
        pages = 0

        while pages < maximum_pages:
            params = {
                "collection_types": COLLECTION_TYPES,
                "get_cover_media_lists": "true",
                "include_public_only": "0",
                "max_id": next_max_id,
            }

            try:
                payload = extractor.api._call(
                    "/v1/collections/list/",
                    params=params,
                    timeout=timeout,
                )
            except Exception as error:
                raise InstagramCollectionsError(
                    "Instagram collection request failed: "
                    f"{type(error).__name__}"
                ) from error

            if not isinstance(payload, dict):
                raise InstagramCollectionsError(
                    "Instagram returned an unexpected response"
                )

            raw_items = payload.get("items", [])

            if not isinstance(raw_items, list):
                raise InstagramCollectionsError(
                    "Instagram collections are not a list"
                )

            for item in raw_items:
                if not isinstance(item, dict):
                    continue

                collections.append(
                    normalize_collection(
                        item,
                        len(collections) + 1,
                    )
                )

            pages += 1

            if not payload.get("more_available"):
                break

            cursor = str(
                payload.get("next_max_id")
                or payload.get("max_id")
                or ""
            ).strip()

            if not cursor or cursor in seen_cursors:
                break

            seen_cursors.add(cursor)
            next_max_id = cursor

        return {
            "source": "instagram",
            "operation": "list_collections",
            "read_only": True,
            "browser": public_browser_details(browser),
            "pages": pages,
            "collection_count": len(collections),
            "collections": collections,
        }
    finally:
        session = getattr(extractor, "session", None)

        if session is not None:
            session.close()

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read Instagram saved collections"
    )
    parser.add_argument(
        "--browser",
        help="Browser containing Instagram cookies",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config()
    browser = (
        args.browser
        or config.get("browser")
        or "firefox"
    )

    try:
        result = list_collections(
            str(browser),
            timeout=max(1, args.timeout),
        )
    except InstagramCollectionsError as error:
        print(json.dumps(
            {
                "status": "ERROR",
                "read_only": True,
                "error": str(error),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 1

    print(json.dumps(
        {
            "status": "SUCCESS",
            **result,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
