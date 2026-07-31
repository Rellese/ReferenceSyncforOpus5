from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin


class SourceContainerPreviewError(RuntimeError):
    """Read-only source-container discovery failed."""


def _text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue

        text = str(value).strip()

        if text:
            return text

    return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception as error:
        raise SourceContainerPreviewError(
            f"Unable to read normalized JSON: {type(error).__name__}"
        ) from error

    if not isinstance(payload, dict):
        raise SourceContainerPreviewError(
            "Normalized source result is not a JSON object"
        )

    return payload


def _run(
    arguments: list[str],
    *,
    timeout: int,
    output: Path | None = None,
) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )

    if completed.returncode != 0:
        message = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "Source discovery command failed"
        )

        raise SourceContainerPreviewError(
            message[-1000:]
        )

    if output is not None:
        return _read_json(output)

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SourceContainerPreviewError(
            "Source discovery returned invalid JSON"
        ) from error

    if not isinstance(payload, dict):
        raise SourceContainerPreviewError(
            "Source discovery result is not a JSON object"
        )

    return payload


def _container(
    *,
    source: str,
    container_id: str,
    parent_id: str | None,
    name: str,
    container_type: str,
    url: str | None = None,
    selectable: bool = True,
    children: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": source,
        "id": container_id,
        "parent_id": parent_id,
        "name": name,
        "type": container_type,
        "selectable": selectable,
        "children": list(children or []),
    }

    if url:
        result["url"] = url

    if metadata:
        result["metadata"] = {
            key: value
            for key, value in metadata.items()
            if value is not None
        }

    return result


def instagram_tree(
    payload: dict[str, Any],
    *,
    username: str,
) -> dict[str, Any]:
    if payload.get("status") != "SUCCESS":
        raise SourceContainerPreviewError(
            _text(
                payload.get("error"),
                "Instagram collection discovery failed",
            )
        )

    account_id = (
        "instagram-account:"
        + username.lower().lstrip("@")
    )

    children = []

    for position, item in enumerate(
        payload.get("collections") or [],
        1,
    ):
        if not isinstance(item, dict):
            continue

        collection_id = _text(
            item.get("collection_id"),
            item.get("id"),
        )

        if not collection_id:
            continue

        children.append(
            _container(
                source="instagram",
                container_id=collection_id,
                parent_id=account_id,
                name=_text(
                    item.get("name"),
                    f"Коллекция {position}",
                ),
                container_type="collection",
                metadata={
                    "collection_type": item.get(
                        "collection_type"
                    ),
                    "media_count": item.get("media_count"),
                    "position": item.get(
                        "position",
                        position,
                    ),
                },
            )
        )

    root = _container(
        source="instagram",
        container_id=account_id,
        parent_id=None,
        name="@" + username.lstrip("@"),
        container_type="account",
        selectable=False,
        children=children,
    )

    return {
        "status": "SUCCESS",
        "schema_version": 1,
        "operation": "list_containers",
        "read_only": True,
        "source": "instagram",
        "root": root,
        "container_count": len(children),
    }


def pinterest_tree(
    boards_payload: dict[str, Any],
    section_payloads: dict[str, dict[str, Any]],
    *,
    username: str,
) -> dict[str, Any]:
    account_id = (
        "pinterest-account:"
        + username.lower().lstrip("@")
    )
    profile_url = (
        "https://www.pinterest.com/"
        + username.lstrip("@")
        + "/"
    )

    board_nodes = []

    for position, board in enumerate(
        boards_payload.get("boards") or [],
        1,
    ):
        if not isinstance(board, dict):
            continue

        board_id = _text(
            board.get("id"),
            board.get("url"),
            board.get("name"),
        )

        if not board_id:
            continue

        board_url = _text(board.get("url"))

        if board_url:
            board_url = urljoin(profile_url, board_url)

        sections_payload = section_payloads.get(
            board_id,
            {},
        )
        section_nodes = []

        for section_position, section in enumerate(
            sections_payload.get("sections") or [],
            1,
        ):
            if not isinstance(section, dict):
                continue

            section_id = _text(
                section.get("id"),
                section.get("url"),
                section.get("name"),
            )

            if not section_id:
                continue

            section_url = _text(section.get("url"))

            if section_url:
                section_url = urljoin(
                    board_url or profile_url,
                    section_url,
                )

            section_nodes.append(
                _container(
                    source="pinterest",
                    container_id=section_id,
                    parent_id=board_id,
                    name=_text(
                        section.get("name"),
                        f"Раздел {section_position}",
                    ),
                    container_type="section",
                    url=section_url or None,
                    metadata={
                        "pin_count": section.get(
                            "pin_count"
                        ),
                        "position": section.get(
                            "position",
                            section_position,
                        ),
                    },
                )
            )

        board_nodes.append(
            _container(
                source="pinterest",
                container_id=board_id,
                parent_id=account_id,
                name=_text(
                    board.get("name"),
                    f"Доска {position}",
                ),
                container_type="board",
                url=board_url or None,
                children=section_nodes,
                metadata={
                    "privacy": board.get("privacy"),
                    "pin_count": board.get("pin_count"),
                    "section_count": board.get(
                        "section_count"
                    ),
                    "sectionless_pin_count": board.get(
                        "sectionless_pin_count"
                    ),
                    "position": position,
                },
            )
        )

    root = _container(
        source="pinterest",
        container_id=account_id,
        parent_id=None,
        name="@" + username.lstrip("@"),
        container_type="account",
        url=profile_url,
        selectable=False,
        children=board_nodes,
    )

    total_sections = sum(
        len(board.get("children") or [])
        for board in board_nodes
    )

    return {
        "status": "SUCCESS",
        "schema_version": 1,
        "operation": "list_containers",
        "read_only": True,
        "source": "pinterest",
        "root": root,
        "container_count": (
            len(board_nodes) + total_sections
        ),
        "board_count": len(board_nodes),
        "section_count": total_sections,
    }


def list_instagram(
    *,
    username: str,
    browser: str,
    timeout: int,
) -> dict[str, Any]:
    payload = _run(
        [
            "-m",
            "app.instagram_collections",
            "--browser",
            browser,
            "--timeout",
            str(timeout),
        ],
        timeout=timeout + 30,
    )

    return instagram_tree(
        payload,
        username=username,
    )


def list_pinterest(
    *,
    username: str,
    browser: str,
    timeout: int,
    limit: int,
) -> dict[str, Any]:
    normalized_username = username.strip().lstrip("@")

    if not normalized_username:
        raise SourceContainerPreviewError(
            "Pinterest username is required"
        )

    profile_url = (
        "https://www.pinterest.com/"
        + normalized_username
        + "/"
    )

    with tempfile.TemporaryDirectory(
        prefix="reference_sync_containers_"
    ) as temporary_directory:
        temporary = Path(temporary_directory)
        boards_output = temporary / "boards.json"

        boards_payload = _run(
            [
                "-m",
                "app.pinterest_discover",
                "list-boards",
                "--url",
                profile_url,
                "--limit",
                str(limit),
                "--cookies-browser",
                browser,
                "--timeout",
                str(timeout),
                "--output",
                str(boards_output),
            ],
            timeout=timeout + 30,
            output=boards_output,
        )

        section_payloads: dict[
            str,
            dict[str, Any],
        ] = {}

        for index, board in enumerate(
            boards_payload.get("boards") or [],
            1,
        ):
            if not isinstance(board, dict):
                continue

            board_id = _text(
                board.get("id"),
                board.get("url"),
                board.get("name"),
            )
            board_url = _text(board.get("url"))

            if not board_id or not board_url:
                continue

            board_url = urljoin(
                profile_url,
                board_url,
            )
            sections_output = (
                temporary / f"sections_{index}.json"
            )

            section_payloads[board_id] = _run(
                [
                    "-m",
                    "app.pinterest_discover",
                    "list-sections",
                    "--url",
                    board_url,
                    "--limit",
                    str(limit),
                    "--cookies-browser",
                    browser,
                    "--output",
                    str(sections_output),
                ],
                timeout=timeout + 30,
                output=sections_output,
            )

    return pinterest_tree(
        boards_payload,
        section_payloads,
        username=normalized_username,
    )


def self_test() -> dict[str, Any]:
    instagram = instagram_tree(
        {
            "status": "SUCCESS",
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
                    "collection_id": "2102701284013394",
                    "name": "motion",
                    "collection_type": "MEDIA",
                },
            ],
        },
        username="example",
    )

    pinterest = pinterest_tree(
        {
            "boards": [
                {
                    "id": "board-1",
                    "name": "Board",
                    "url": "/example/board/",
                }
            ]
        },
        {
            "board-1": {
                "sections": [
                    {
                        "id": "section-1",
                        "name": "Section",
                        "url": (
                            "/example/board/section/"
                        ),
                    }
                ]
            }
        },
        username="example",
    )

    if instagram["container_count"] != 2:
        raise AssertionError(
            "Instagram self-test failed"
        )

    if pinterest["container_count"] != 2:
        raise AssertionError(
            "Pinterest self-test failed"
        )

    return {
        "status": "SUCCESS",
        "instagram_containers": (
            instagram["container_count"]
        ),
        "pinterest_containers": (
            pinterest["container_count"]
        ),
        "read_only": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.source_container_preview",
        description=(
            "Read-only Instagram/Pinterest container tree"
        ),
    )

    parser.add_argument(
        "--source",
        choices=("instagram", "pinterest"),
    )
    parser.add_argument("--username")
    parser.add_argument(
        "--browser",
        default="chrome",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
    )
    parser.add_argument(
        "--output",
        type=Path,
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    return parser


def main() -> int:
    arguments = build_parser().parse_args()

    try:
        if arguments.self_test:
            result = self_test()
        else:
            if not arguments.source:
                raise SourceContainerPreviewError(
                    "--source is required"
                )

            username = (
                arguments.username or ""
            ).strip().lstrip("@")

            if not username:
                raise SourceContainerPreviewError(
                    "--username is required"
                )

            if arguments.source == "instagram":
                result = list_instagram(
                    username=username,
                    browser=arguments.browser,
                    timeout=arguments.timeout,
                )
            else:
                result = list_pinterest(
                    username=username,
                    browser=arguments.browser,
                    timeout=arguments.timeout,
                    limit=arguments.limit,
                )

        rendered = json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )

        if arguments.output:
            arguments.output.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            arguments.output.write_text(
                rendered + "\n",
                encoding="utf-8",
            )

        print(rendered)
        return 0

    except Exception as error:
        result = {
            "status": "ERROR",
            "operation": "list_containers",
            "read_only": True,
            "error": (
                f"{type(error).__name__}: {error}"
            ),
        }
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
