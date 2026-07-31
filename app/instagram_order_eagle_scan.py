from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


EAGLE_API = "http://localhost:41595"
PAGE_LIMIT = 1000

ORDER_PATTERN = re.compile(
    r"instpoporder-(\d+)(?:-(\d+))?",
    re.IGNORECASE,
)


def response_items(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], int | None]:
    data = payload.get("data")

    if isinstance(data, list):
        return data, None

    if isinstance(data, dict):
        items = data.get("data")

        if not isinstance(items, list):
            items = data.get("items")

        if not isinstance(items, list):
            items = []

        total = data.get("total")

        try:
            parsed_total = int(total)
        except (TypeError, ValueError):
            parsed_total = None

        return items, parsed_total

    return [], None


def request_page(
    parameters: dict[str, Any],
) -> dict[str, Any]:
    query = urllib.parse.urlencode(parameters)
    url = f"{EAGLE_API}/api/v2/item/get?{query}"

    try:
        with urllib.request.urlopen(
            url,
            timeout=60,
        ) as response:
            return json.loads(
                response.read().decode("utf-8")
            )
    except urllib.error.HTTPError as error:
        body = error.read().decode(
            "utf-8",
            errors="replace",
        )
        raise RuntimeError(
            f"Eagle HTTP {error.code}: {body}"
        ) from error


def collect_items(
    base_parameters: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    offset = 0
    pages = 0
    collected = []

    while True:
        parameters = dict(base_parameters)
        parameters.update({
            "offset": offset,
            "limit": PAGE_LIMIT,
        })

        payload = request_page(parameters)
        items, total = response_items(payload)

        if not items:
            break

        collected.extend(items)
        pages += 1
        offset += len(items)

        if total is not None and offset >= total:
            break

        if len(items) < PAGE_LIMIT:
            break

    return collected, pages


def scan_order_names(
    target_numbers: set[int] | None = None,
) -> dict[str, Any]:
    target_numbers = target_numbers or set()
    fallback_errors = []

    try:
        items, pages = collect_items({
            "keywords": "instpoporder",
        })
        strategy = "KEYWORD_QUERY"
    except Exception as error:
        fallback_errors.append({
            "strategy": "KEYWORD_QUERY",
            "error": str(error),
        })
        items = []
        pages = 0
        strategy = "ALL_LIBRARY_ITEMS"

    keyword_matches = [
        item
        for item in items
        if ORDER_PATTERN.search(
            str(item.get("name") or "")
        )
    ]

    if not keyword_matches:
        items, pages = collect_items({})
        strategy = "ALL_LIBRARY_ITEMS"

    matched = []
    post_numbers = set()
    collisions = []

    for item in items:
        name = str(item.get("name") or "")
        match = ORDER_PATTERN.search(name)

        if not match:
            continue

        post_number = int(match.group(1))
        component_number = (
            int(match.group(2))
            if match.group(2)
            else None
        )

        post_numbers.add(post_number)

        row = {
            "id": item.get("id"),
            "name": name,
            "post_number": post_number,
            "component_number": component_number,
            "url": item.get("url"),
            "folders": item.get("folders"),
        }

        matched.append(row)

        if post_number in target_numbers:
            collisions.append(row)

    maximum = (
        max(post_numbers)
        if post_numbers
        else None
    )

    return {
        "strategy": strategy,
        "pages": pages,
        "items_received": len(items),
        "numbered_items_found": len(matched),
        "distinct_post_numbers": len(post_numbers),
        "minimum_post_number": (
            min(post_numbers)
            if post_numbers
            else None
        ),
        "maximum_post_number": maximum,
        "automatic_next_number": (
            maximum + 1
            if maximum is not None
            else 1
        ),
        "target_numbers": sorted(target_numbers),
        "collisions": collisions,
        # V6.2: discovery uses these read-only identities to
        # recognise publications that already exist in Eagle,
        # even when they predate instagram_sync_posts.
        "matched": matched,
        "fallback_errors": fallback_errors,
    }
