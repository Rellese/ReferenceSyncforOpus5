from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


PROJECT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT / "reports"
API_URL = "http://localhost:41595"
TEST_SHORTCODE = "DRW96TDDSvX"


def latest_manifest() -> Path:
    files = sorted(
        REPORTS.glob("instagram_missing_import_manifest_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise FileNotFoundError("Import manifest not found")
    return files[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def response_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", [])

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        nested = data.get("data", [])
        if isinstance(nested, list):
            return nested

        if data.get("id"):
            return [data]

    return []


def find_created_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        value = payload.get("id")
        if isinstance(value, str) and value:
            return value

        for child in payload.values():
            found = find_created_id(child)
            if found:
                return found

    if isinstance(payload, list):
        for child in payload:
            found = find_created_id(child)
            if found:
                return found

    return None


def get_by_url(
    client: httpx.Client,
    post_url: str,
) -> list[dict[str, Any]]:
    response = client.post(
        f"{API_URL}/api/v2/item/get",
        json={
            "url": post_url,
            "fields": [
                "id",
                "name",
                "ext",
                "size",
                "tags",
                "folders",
                "url",
                "annotation",
                "isDeleted",
            ],
            "limit": 100,
            "offset": 0,
        },
    )
    response.raise_for_status()

    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"Eagle query failed: {payload}")

    return [
        item
        for item in response_items(payload)
        if not item.get("isDeleted")
        and item.get("url") == post_url
    ]


def get_by_id(
    client: httpx.Client,
    item_id: str,
) -> dict[str, Any] | None:
    response = client.post(
        f"{API_URL}/api/v2/item/get",
        json={
            "id": item_id,
            "fields": [
                "id",
                "name",
                "ext",
                "size",
                "tags",
                "folders",
                "url",
                "annotation",
                "isDeleted",
            ],
            "limit": 1,
            "offset": 0,
        },
    )
    response.raise_for_status()

    payload = response.json()
    items = response_items(payload)
    return items[0] if items else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually create one Eagle item",
    )
    args = parser.parse_args()

    manifest_path = latest_manifest()
    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    candidates = [
        item
        for item in manifest
        if item.get("post_shortcode") == TEST_SHORTCODE
    ]

    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected one {TEST_SHORTCODE} candidate, "
            f"found {len(candidates)}"
        )

    item = candidates[0]
    proposed = item["proposed_eagle_item"]
    source_path = Path(item["local_path"])

    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    source_before = {
        "path": str(source_path),
        "size": source_path.stat().st_size,
        "mtime_ns": source_path.stat().st_mtime_ns,
        "sha256": sha256_file(source_path),
    }

    preview = {
        "shortcode": item["post_shortcode"],
        "post_id": item["post_id"],
        "media_id": item["media_id"],
        "component_index": item["component_index"],
        "source": source_before,
        "eagle": {
            "name": proposed["name"],
            "tags": proposed["tags"],
            "folders": proposed["folders"],
            "url": proposed["url"],
            "annotation": proposed["annotation"],
        },
    }

    with httpx.Client(timeout=120.0) as client:
        existing_before = get_by_url(client, proposed["url"])

        if existing_before:
            print(json.dumps({
                "status": "ABORTED_ALREADY_EXISTS",
                "existing_items": existing_before,
                "preview": preview,
                "eagle_items_created": 0,
            }, ensure_ascii=False, indent=2))
            return

        if not args.commit:
            print(json.dumps({
                "status": "PREVIEW_ONLY",
                "preview": preview,
                "existing_eagle_items_with_url": 0,
                "next_command": (
                    "python -m app.eagle_import_one --commit"
                ),
                "safety": {
                    "source_modified": False,
                    "eagle_library_modified": False,
                    "eagle_items_created": 0,
                },
            }, ensure_ascii=False, indent=2))
            return

        payload = {
            "path": str(source_path),
            "name": proposed["name"],
            "tags": proposed["tags"],
            "folders": proposed["folders"],
            "website": proposed["url"],
            "annotation": proposed["annotation"],
        }

        response = client.post(
            f"{API_URL}/api/v2/item/add",
            json=payload,
        )
        response.raise_for_status()
        add_result = response.json()

        if add_result.get("status") != "success":
            raise RuntimeError(
                f"Eagle import failed: {add_result}"
            )

        created_id = find_created_id(add_result)

        if created_id:
            created_item = get_by_id(client, created_id)
        else:
            matching = get_by_url(client, proposed["url"])
            created_item = matching[0] if len(matching) == 1 else None
            if created_item:
                created_id = created_item.get("id")

    source_after = {
        "size": source_path.stat().st_size,
        "mtime_ns": source_path.stat().st_mtime_ns,
        "sha256": sha256_file(source_path),
    }

    source_unchanged = (
        source_before["size"] == source_after["size"]
        and source_before["mtime_ns"] == source_after["mtime_ns"]
        and source_before["sha256"] == source_after["sha256"]
    )

    verification = {
        "created_id_found": bool(created_id),
        "created_item_read_back": bool(created_item),
        "url_correct": bool(
            created_item
            and created_item.get("url") == proposed["url"]
        ),
        "tag_correct": bool(
            created_item
            and "Instagram" in (created_item.get("tags") or [])
        ),
        "folder_correct": bool(
            created_item
            and proposed["folders"][0]
            in (created_item.get("folders") or [])
        ),
        "extension_correct": bool(
            created_item
            and str(created_item.get("ext", "")).lower()
            == item["extension"].lower()
        ),
        "source_unchanged": source_unchanged,
    }

    verification["import_verified"] = all(
        verification.values()
    )

    result = {
        "status": (
            "IMPORTED_AND_VERIFIED"
            if verification["import_verified"]
            else "IMPORTED_REQUIRES_MANUAL_REVIEW"
        ),
        "created_eagle_id": created_id,
        "created_item": created_item,
        "verification": verification,
        "source": {
            "before": source_before,
            "after": source_after,
        },
        "safety": {
            "source_modified": not source_unchanged,
            "physical_files_deleted": 0,
            "physical_files_moved": 0,
            "eagle_items_created": 1,
            "eagle_items_updated": 0,
            "eagle_items_deleted": 0,
        },
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = (
        REPORTS
        / f"eagle_controlled_import_{timestamp}.json"
    )
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result["report"] = str(report_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
