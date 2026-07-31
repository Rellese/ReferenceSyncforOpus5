from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse


PINTEREST_HOST_RE = re.compile(
    r"(?:^|\.)pinterest\.[a-z.]+$",
    re.IGNORECASE,
)

GALLERY_DL_DIRECTORY_MESSAGE = 2
GALLERY_DL_URL_MESSAGE = 3
GALLERY_DL_QUEUE_MESSAGE = 6


class PinterestDiscoveryError(RuntimeError):
    """Raised when Pinterest metadata discovery cannot be completed."""


def validate_pinterest_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)

    if parsed.scheme not in {"http", "https"}:
        raise PinterestDiscoveryError(
            "Pinterest URL must start with http:// or https://"
        )

    hostname = (parsed.hostname or "").lower()

    if not PINTEREST_HOST_RE.search(hostname):
        raise PinterestDiscoveryError(
            f"Unsupported Pinterest hostname: {hostname or '<empty>'}"
        )

    return value


def load_gallery_dump(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PinterestDiscoveryError(
            f"Input JSON does not exist: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise PinterestDiscoveryError(
            f"Invalid gallery-dl JSON in {path}: {exc}"
        ) from exc


def run_gallery_dl(
    url: str,
    *,
    limit: int,
    cookies_browser: str,
    timeout: int,
) -> tuple[Any, str]:
    url = validate_pinterest_url(url)

    command = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--cookies-from-browser",
        cookies_browser,
        "--dump-json",
        "--range",
        f"1-{limit}",
        url,
    ]

    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PinterestDiscoveryError(
            f"gallery-dl timed out after {timeout} seconds"
        ) from exc
    except OSError as exc:
        raise PinterestDiscoveryError(
            f"Unable to start gallery-dl: {exc}"
        ) from exc

    if completed.returncode != 0:
        diagnostic = completed.stderr.strip()
        if len(diagnostic) > 1500:
            diagnostic = diagnostic[-1500:]

        raise PinterestDiscoveryError(
            "gallery-dl failed with exit status "
            f"{completed.returncode}: {diagnostic or 'no diagnostic'}"
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PinterestDiscoveryError(
            f"gallery-dl returned invalid JSON: {exc}"
        ) from exc

    return payload, completed.stderr.strip()


def text_value(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, (str, int)):
        result = str(value).strip()
        return result or None

    return None


def first_text(*values: Any) -> str | None:
    for value in values:
        result = text_value(value)
        if result:
            return result
    return None


def dictionary(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def media_component(metadata: dict[str, Any]) -> dict[str, Any]:
    component = {
        "media_id": first_text(
            metadata.get("media_id"),
            metadata.get("page_id"),
        ),
        "extension": text_value(metadata.get("extension")),
        "filename": text_value(metadata.get("filename")),
    }

    return {
        key: value
        for key, value in component.items()
        if value is not None
    }


def discover_sections(
    url: str,
    *,
    limit: int,
    cookies_browser: str,
) -> dict[str, Any]:
    url = validate_pinterest_url(url)

    if not re.fullmatch(r"[A-Za-z0-9_-]+", cookies_browser):
        raise PinterestDiscoveryError(
            "Browser name contains unsupported characters"
        )

    try:
        from gallery_dl import cookies
        from gallery_dl.extractor.pinterest import (
            PinterestBoardExtractor,
        )
    except ImportError as exc:
        raise PinterestDiscoveryError(
            f"Unable to import gallery-dl Pinterest API: {exc}"
        ) from exc

    extractor = PinterestBoardExtractor.from_url(url)

    if extractor is None:
        raise PinterestDiscoveryError(
            "The URL is not recognized as a Pinterest board URL"
        )

    try:
        extractor.initialize()

        browser_cookies = cookies.load_cookies(
            (
                cookies_browser,
                None,
                None,
                None,
                ".pinterest.com",
            )
        )

        cookie_count = 0

        for cookie in browser_cookies:
            extractor.session.cookies.set_cookie(cookie)
            cookie_count += 1

        board = extractor.api.board(
            extractor.user,
            extractor.board_name,
        )

        board_id = first_text(board.get("id"))

        if not board_id:
            raise PinterestDiscoveryError(
                "Pinterest returned a board without an ID"
            )

        raw_sections = list(
            extractor.api.board_sections(board_id)
        )

    except PinterestDiscoveryError:
        raise
    except Exception as exc:
        raise PinterestDiscoveryError(
            "Unable to retrieve Pinterest sections: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    sections: OrderedDict[str, dict[str, Any]] = OrderedDict()
    skipped_sections = 0

    for raw_section in raw_sections:
        if not isinstance(raw_section, dict):
            skipped_sections += 1
            continue

        section_id = first_text(raw_section.get("id"))

        if not section_id:
            skipped_sections += 1
            continue

        raw_url = first_text(raw_section.get("url"))

        if raw_url:
            section_url = urljoin(url, raw_url)
        else:
            section_url = (
                f"{url.rstrip('/')}/id:{section_id}/"
            )

        section = {
            "id": section_id,
            "name": first_text(
                raw_section.get("title"),
                raw_section.get("name"),
            ),
            "slug": first_text(raw_section.get("slug")),
            "url": section_url,
            "pin_count": raw_section.get("pin_count"),
            "position": raw_section.get("position"),
        }

        section = {
            key: value
            for key, value in section.items()
            if value is not None
        }

        sections[section_id] = section

    selected_sections = list(sections.values())[:limit]

    reported_section_count = board.get("section_count")
    warnings = []

    if skipped_sections:
        warnings.append(
            "Skipped section records without usable metadata: "
            f"{skipped_sections}"
        )

    try:
        expected_count = int(reported_section_count or 0)
    except (TypeError, ValueError):
        expected_count = None

    if (
        expected_count is not None
        and expected_count != len(sections)
    ):
        warnings.append(
            "Board section count differs from API result: "
            f"reported={expected_count}, returned={len(sections)}"
        )

    normalized_board = {
        "id": board_id,
        "name": first_text(board.get("name")),
        "url": first_text(board.get("url")),
        "pin_count": board.get("pin_count"),
        "section_count": reported_section_count,
        "sectionless_pin_count": board.get(
            "sectionless_pin_count"
        ),
    }

    normalized_board = {
        key: value
        for key, value in normalized_board.items()
        if value is not None
    }

    return {
        "schema_version": 1,
        "platform": "pinterest",
        "operation": "list-sections",
        "source_url": url,
        "board": normalized_board,
        "sections": selected_sections,
        "summary": {
            "sections": len(selected_sections),
            "sections_available": len(sections),
            "reported_section_count": reported_section_count,
            "skipped_sections": skipped_sections,
            "cookies_loaded": cookie_count,
            "warnings": len(warnings),
        },
        "warnings": warnings,
    }


def parse_board_messages(
    payload: Any,
    *,
    limit: int,
    source_url: str | None,
) -> dict[str, Any]:
    if not isinstance(payload, list):
        raise PinterestDiscoveryError(
            "Expected the gallery-dl dump to be a JSON list"
        )

    boards: OrderedDict[str, dict[str, Any]] = OrderedDict()
    queue_messages = 0
    skipped_queue_messages = 0

    for entry in payload:
        if not isinstance(entry, list) or not entry:
            continue

        if entry[0] != GALLERY_DL_QUEUE_MESSAGE:
            continue

        queue_messages += 1

        if len(entry) < 3 or not isinstance(entry[2], dict):
            skipped_queue_messages += 1
            continue

        metadata = entry[2]
        owner = dictionary(metadata.get("owner"))

        board_id = first_text(metadata.get("id"))
        board_url = first_text(
            metadata.get("url"),
            entry[1] if len(entry) > 1 else None,
        )

        identity = first_text(
            board_id,
            board_url,
            metadata.get("name"),
        )

        if not identity:
            skipped_queue_messages += 1
            continue

        if identity in boards:
            continue

        board = {
            "id": board_id,
            "name": first_text(metadata.get("name")),
            "url": board_url,
            "privacy": first_text(metadata.get("privacy")),
            "pin_count": metadata.get("pin_count"),
            "section_count": metadata.get("section_count"),
            "sectionless_pin_count": metadata.get(
                "sectionless_pin_count"
            ),
            "owner": {
                "id": first_text(owner.get("id")),
                "username": first_text(owner.get("username")),
                "full_name": first_text(owner.get("full_name")),
            },
        }

        board["owner"] = {
            key: value
            for key, value in board["owner"].items()
            if value is not None
        }

        board = {
            key: value
            for key, value in board.items()
            if value is not None
        }

        boards[identity] = board

    selected_boards = list(boards.values())[:limit]
    warnings = []

    if skipped_queue_messages:
        warnings.append(
            "Skipped board queue messages without usable metadata: "
            f"{skipped_queue_messages}"
        )

    if not selected_boards:
        warnings.append(
            "No Pinterest boards were found in gallery-dl output"
        )

    return {
        "schema_version": 1,
        "platform": "pinterest",
        "operation": "list-boards",
        "source_url": source_url,
        "boards": selected_boards,
        "summary": {
            "boards": len(selected_boards),
            "boards_available": len(boards),
            "queue_messages": queue_messages,
            "skipped_queue_messages": skipped_queue_messages,
            "warnings": len(warnings),
        },
        "warnings": warnings,
    }


def parse_pin_messages(
    payload: Any,
    *,
    limit: int,
    source_url: str | None,
) -> dict[str, Any]:
    if not isinstance(payload, list):
        raise PinterestDiscoveryError(
            "Expected the gallery-dl dump to be a JSON list"
        )

    publications: OrderedDict[str, dict[str, Any]] = OrderedDict()
    directory_messages = 0
    url_messages = 0
    skipped_url_messages = 0

    for entry in payload:
        if not isinstance(entry, list) or not entry:
            continue

        message_code = entry[0]

        if message_code == GALLERY_DL_DIRECTORY_MESSAGE:
            directory_messages += 1
            continue

        if message_code != GALLERY_DL_URL_MESSAGE:
            continue

        url_messages += 1

        if len(entry) < 3 or not isinstance(entry[2], dict):
            skipped_url_messages += 1
            continue

        metadata = entry[2]

        pin_id = first_text(
            metadata.get("id"),
            metadata.get("pin_id"),
        )

        if not pin_id:
            skipped_url_messages += 1
            continue

        board = dictionary(metadata.get("board"))
        section = dictionary(metadata.get("section"))

        publication = publications.get(pin_id)

        if publication is None:
            publication = {
                "pin_id": pin_id,
                "canonical_url": f"https://www.pinterest.com/pin/{pin_id}/",
                "title": first_text(
                    metadata.get("title"),
                    metadata.get("grid_title"),
                ),
                "description": first_text(
                    metadata.get("description"),
                    metadata.get("alt_text"),
                ),
                "board": {
                    "id": first_text(board.get("id")),
                    "name": first_text(board.get("name")),
                    "url": first_text(board.get("url")),
                    "pin_count": board.get("pin_count"),
                    "section_count": board.get("section_count"),
                    "sectionless_pin_count": board.get(
                        "sectionless_pin_count"
                    ),
                },
                "section": {
                    "id": first_text(section.get("id")),
                    "name": first_text(
                        section.get("title"),
                        section.get("name"),
                    ),
                    "url": first_text(section.get("url")),
                } if section else None,
                "media_components": [],
                "has_video_metadata": bool(metadata.get("videos")),
                "has_story_pin_metadata": bool(
                    metadata.get("story_pin_data")
                ),
                "has_carousel_metadata": bool(
                    metadata.get("carousel_data")
                ),
            }

            publication["board"] = {
                key: value
                for key, value in publication["board"].items()
                if value is not None
            }

            publications[pin_id] = publication

        component = media_component(metadata)

        if component and component not in publication["media_components"]:
            publication["media_components"].append(component)

    pins = list(publications.values())[:limit]

    for pin in pins:
        pin["media_component_count"] = len(pin["media_components"])

    warnings = []

    if skipped_url_messages:
        warnings.append(
            f"Skipped URL messages without usable metadata: "
            f"{skipped_url_messages}"
        )

    if not pins:
        warnings.append("No Pinterest pins were found in gallery-dl output")

    return {
        "schema_version": 1,
        "platform": "pinterest",
        "operation": "list-pins",
        "source_url": source_url,
        "pins": pins,
        "summary": {
            "pins": len(pins),
            "directory_messages": directory_messages,
            "url_messages": url_messages,
            "skipped_url_messages": skipped_url_messages,
            "warnings": len(warnings),
        },
        "warnings": warnings,
    }


def write_result(
    result: dict[str, Any],
    *,
    output: Path | None,
    summary_only: bool,
) -> None:
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )

    if summary_only:
        print(
            json.dumps(
                result["summary"],
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    elif output is not None:
        print(f"OUTPUT={output}")
        print(
            json.dumps(
                result["summary"],
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )


def positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected an integer"
        ) from exc

    if number < 1:
        raise argparse.ArgumentTypeError(
            "value must be greater than zero"
        )

    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.pinterest_discover",
        description=(
            "Read-only Pinterest metadata discovery through gallery-dl"
        ),
    )

    subparsers = parser.add_subparsers(
        dest="operation",
        required=True,
    )

    list_sections = subparsers.add_parser(
        "list-sections",
        help="List Pinterest board sections without downloading pins",
    )
    list_sections.add_argument(
        "--url",
        required=True,
        help="Pinterest board URL",
    )
    list_sections.add_argument(
        "--limit",
        type=positive_integer,
        default=200,
        help="Maximum sections returned (default: 200)",
    )
    list_sections.add_argument(
        "--cookies-browser",
        default="chrome",
        help="Browser used for Pinterest cookies (default: chrome)",
    )
    list_sections.add_argument(
        "--output",
        type=Path,
        help="Optional normalized JSON output file",
    )
    list_sections.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only non-sensitive summary counters",
    )

    list_boards = subparsers.add_parser(
        "list-boards",
        help="List Pinterest boards without downloading their pins",
    )

    board_source_group = list_boards.add_mutually_exclusive_group(
        required=True
    )
    board_source_group.add_argument(
        "--url",
        help="Pinterest user profile URL",
    )
    board_source_group.add_argument(
        "--input-json",
        type=Path,
        help="Parse an existing gallery-dl profile JSON file",
    )

    list_boards.add_argument(
        "--limit",
        type=positive_integer,
        default=200,
        help="Maximum boards returned by ReferenceSync (default: 200)",
    )
    list_boards.add_argument(
        "--cookies-browser",
        default="chrome",
        help="Browser used by gallery-dl for cookies (default: chrome)",
    )
    list_boards.add_argument(
        "--timeout",
        type=positive_integer,
        default=180,
        help="gallery-dl timeout in seconds (default: 180)",
    )
    list_boards.add_argument(
        "--output",
        type=Path,
        help="Optional normalized JSON output file",
    )
    list_boards.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only non-sensitive summary counters",
    )

    list_pins = subparsers.add_parser(
        "list-pins",
        help="List Pinterest pins without downloading media",
    )

    source_group = list_pins.add_mutually_exclusive_group(
        required=True
    )
    source_group.add_argument(
        "--url",
        help="Pinterest board, section, profile or all-pins URL",
    )
    source_group.add_argument(
        "--input-json",
        type=Path,
        help="Parse an existing gallery-dl --dump-json file",
    )

    list_pins.add_argument(
        "--limit",
        type=positive_integer,
        default=20,
        help="Maximum number of gallery-dl results (default: 20)",
    )
    list_pins.add_argument(
        "--cookies-browser",
        default="chrome",
        help="Browser used by gallery-dl for cookies (default: chrome)",
    )
    list_pins.add_argument(
        "--timeout",
        type=positive_integer,
        default=180,
        help="gallery-dl timeout in seconds (default: 180)",
    )
    list_pins.add_argument(
        "--output",
        type=Path,
        help="Optional normalized JSON output file",
    )
    list_pins.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only non-sensitive summary counters",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.operation == "list-sections":
            result = discover_sections(
                args.url,
                limit=args.limit,
                cookies_browser=args.cookies_browser,
            )

            write_result(
                result,
                output=args.output,
                summary_only=args.summary_only,
            )
            return 0

        if args.operation == "list-boards":
            diagnostic = ""

            if args.input_json is not None:
                payload = load_gallery_dump(args.input_json)
                source_url = None
            else:
                payload, diagnostic = run_gallery_dl(
                    args.url,
                    limit=args.limit,
                    cookies_browser=args.cookies_browser,
                    timeout=args.timeout,
                )
                source_url = args.url

            result = parse_board_messages(
                payload,
                limit=args.limit,
                source_url=source_url,
            )

            if diagnostic:
                result["gallery_dl_diagnostic_present"] = True

            write_result(
                result,
                output=args.output,
                summary_only=args.summary_only,
            )
            return 0

        if args.operation == "list-pins":
            diagnostic = ""

            if args.input_json is not None:
                payload = load_gallery_dump(args.input_json)
                source_url = None
            else:
                payload, diagnostic = run_gallery_dl(
                    args.url,
                    limit=args.limit,
                    cookies_browser=args.cookies_browser,
                    timeout=args.timeout,
                )
                source_url = args.url

            result = parse_pin_messages(
                payload,
                limit=args.limit,
                source_url=source_url,
            )

            if diagnostic:
                result["gallery_dl_diagnostic_present"] = True

            write_result(
                result,
                output=args.output,
                summary_only=args.summary_only,
            )
            return 0

        parser.error(f"Unsupported operation: {args.operation}")
        return 2

    except PinterestDiscoveryError as exc:
        print(
            f"PINTEREST_DISCOVERY_ERROR={exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
