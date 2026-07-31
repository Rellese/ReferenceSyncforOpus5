from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
DATABASE = PROJECT / "data/reference_sync.sqlite3"
BACKUPS = PROJECT / "data/backups"
REPORTS = PROJECT / "reports"

EAGLE_API = "http://localhost:41595"
ORDER_MARKER = "instpoporder"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


def load_latest_plan() -> tuple[Path, dict[str, Any]]:
    paths = sorted(
        REPORTS.glob("instagram_order_plan_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not paths:
        raise RuntimeError(
            "No instagram_order_plan report found"
        )

    path = paths[0]
    payload = json.loads(
        path.read_text(encoding="utf-8")
    )

    if payload.get("status") != "ORDER_PLAN_READY":
        raise RuntimeError(
            "Latest order plan is not ORDER_PLAN_READY: "
            f"{path}"
        )

    if payload.get("mode") != "PREVIEW_ONLY":
        raise RuntimeError(
            "Unexpected plan mode: "
            f"{payload.get('mode')}"
        )

    return path, payload


def unwrap_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")

    if isinstance(data, list):
        return [
            item
            for item in data
            if isinstance(item, dict)
        ]

    if isinstance(data, dict):
        nested = data.get("data")

        if isinstance(nested, list):
            return [
                item
                for item in nested
                if isinstance(item, dict)
            ]

        if "id" in data:
            return [data]

    return []


def eagle_get_item(
    eagle_item_id: str,
) -> dict[str, Any] | None:
    query = urllib.parse.urlencode(
        {"id": eagle_item_id}
    )

    url = (
        f"{EAGLE_API}/api/v2/item/get?"
        f"{query}"
    )

    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json"},
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:
        payload = json.loads(
            response.read().decode("utf-8")
        )

    items = unwrap_items(payload)
    return items[0] if items else None


def eagle_update_name(
    eagle_item_id: str,
    name: str,
) -> dict[str, Any]:
    url = f"{EAGLE_API}/api/v2/item/update"

    body = json.dumps(
        {
            "id": eagle_item_id,
            "name": name,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:
            response_text = response.read().decode(
                "utf-8"
            )

            if not response_text.strip():
                return {
                    "http_status": response.status,
                    "response": None,
                }

            try:
                response_payload = json.loads(
                    response_text
                )
            except json.JSONDecodeError:
                response_payload = response_text

            return {
                "http_status": response.status,
                "response": response_payload,
            }

    except urllib.error.HTTPError as error:
        error_body = error.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Eagle update failed for "
            f"{eagle_item_id}: HTTP {error.code}: "
            f"{error_body}"
        ) from error


def wait_for_name(
    eagle_item_id: str,
    expected_name: str,
    attempts: int = 15,
    delay: float = 0.5,
) -> tuple[bool, dict[str, Any] | None]:
    last_item: dict[str, Any] | None = None

    for _ in range(attempts):
        last_item = eagle_get_item(eagle_item_id)

        if (
            last_item
            and last_item.get("name") == expected_name
        ):
            return True, last_item

        time.sleep(delay)

    return False, last_item


def canonical_url(item: dict[str, Any]) -> str:
    return str(
        item.get("url")
        or item.get("website")
        or ""
    ).strip()


def validate_plan(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    items = plan.get("plan")

    if not isinstance(items, list):
        return ["Plan does not contain an item list"]

    summary = plan.get("summary") or {}

    if len(items) != summary.get("planned_renames"):
        errors.append(
            "Plan item count does not match summary"
        )

    eagle_ids: set[str] = set()
    target_keys: set[tuple[str, str]] = set()

    for index, item in enumerate(items, start=1):
        eagle_id = str(
            item.get("eagle_item_id") or ""
        ).strip()

        proposed_name = str(
            item.get("proposed_name") or ""
        ).strip()

        job_id = str(
            item.get("job_id") or ""
        ).strip()

        if not eagle_id:
            errors.append(
                f"Item {index}: missing Eagle ID"
            )

        if eagle_id in eagle_ids:
            errors.append(
                f"Duplicate Eagle ID: {eagle_id}"
            )

        eagle_ids.add(eagle_id)

        if not proposed_name:
            errors.append(
                f"Item {index}: missing proposed name"
            )
        elif ORDER_MARKER not in proposed_name:
            errors.append(
                f"Item {index}: marker missing from name"
            )

        key = (job_id, proposed_name)

        if key in target_keys:
            errors.append(
                f"Duplicate target name in job: {key}"
            )

        target_keys.add(key)

    return errors


def preflight(
    plan_items: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    prepared: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for item in plan_items:
        eagle_id = item["eagle_item_id"]

        try:
            current = eagle_get_item(eagle_id)
        except Exception as error:
            errors.append(
                {
                    "eagle_item_id": eagle_id,
                    "error": (
                        f"{type(error).__name__}: "
                        f"{error}"
                    ),
                }
            )
            continue

        if not current:
            errors.append(
                {
                    "eagle_item_id": eagle_id,
                    "error": "EAGLE_ITEM_NOT_FOUND",
                }
            )
            continue

        expected_url = str(
            item.get("url") or ""
        ).rstrip("/")

        actual_url = canonical_url(
            current
        ).rstrip("/")

        if expected_url and actual_url != expected_url:
            errors.append(
                {
                    "eagle_item_id": eagle_id,
                    "error": "URL_MISMATCH",
                    "expected_url": expected_url,
                    "actual_url": actual_url,
                }
            )
            continue

        prepared.append(
            {
                **item,
                "actual_current_name": current.get(
                    "name"
                ),
                "actual_current_url": actual_url,
                "actual_extension": current.get("ext"),
                "actual_tags": current.get("tags"),
                "actual_folders": current.get(
                    "folders"
                ),
            }
        )

    return prepared, errors


def backup_database(timestamp: str) -> Path:
    BACKUPS.mkdir(parents=True, exist_ok=True)

    backup_path = (
        BACKUPS
        / (
            "reference_sync_before_order_apply_"
            f"{timestamp}.sqlite3"
        )
    )

    source = sqlite3.connect(DATABASE)
    destination = sqlite3.connect(backup_path)

    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    return backup_path


def save_order_registry(
    plan_items: list[dict[str, Any]],
) -> None:
    connection = sqlite3.connect(DATABASE)

    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            instagram_sync_post_order (
                post_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                post_number INTEGER NOT NULL,
                order_marker TEXT NOT NULL
                    DEFAULT 'instpoporder',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                UNIQUE(job_id, post_number),

                FOREIGN KEY(post_id)
                    REFERENCES instagram_sync_posts(post_id),

                FOREIGN KEY(job_id)
                    REFERENCES instagram_sync_jobs(job_id)
            )
            """
        )

        timestamp = now_iso()

        unique_posts: dict[
            str,
            dict[str, Any],
        ] = {}

        for item in plan_items:
            unique_posts[str(item["post_id"])] = item

        for item in unique_posts.values():
            connection.execute(
                """
                INSERT INTO instagram_sync_post_order (
                    post_id,
                    job_id,
                    post_number,
                    order_marker,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)

                ON CONFLICT(post_id) DO UPDATE SET
                    job_id = excluded.job_id,
                    post_number = excluded.post_number,
                    order_marker = excluded.order_marker,
                    updated_at = excluded.updated_at
                """,
                (
                    str(item["post_id"]),
                    str(item["job_id"]),
                    int(item["post_number"]),
                    ORDER_MARKER,
                    timestamp,
                    timestamp,
                ),
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def rollback_names(
    changed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for item in reversed(changed):
        eagle_id = item["eagle_item_id"]
        old_name = item["actual_current_name"]

        try:
            eagle_update_name(eagle_id, old_name)

            verified, readback = wait_for_name(
                eagle_id,
                old_name,
            )

            results.append(
                {
                    "eagle_item_id": eagle_id,
                    "restored_name": old_name,
                    "verified": verified,
                    "readback_name": (
                        readback.get("name")
                        if readback
                        else None
                    ),
                }
            )
        except Exception as error:
            results.append(
                {
                    "eagle_item_id": eagle_id,
                    "restored_name": old_name,
                    "verified": False,
                    "error": (
                        f"{type(error).__name__}: "
                        f"{error}"
                    ),
                }
            )

    return results


def write_report(
    timestamp: str,
    report: dict[str, Any],
) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)

    path = (
        REPORTS
        / f"instagram_order_apply_{timestamp}.json"
    )

    report["report_path"] = str(path)

    path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return path


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--commit",
        action="store_true",
        help="Apply names in Eagle and store order in SQLite",
    )

    args = parser.parse_args()
    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    plan_path, plan = load_latest_plan()
    validation_errors = validate_plan(plan)
    plan_items = plan["plan"]

    if validation_errors:
        report = {
            "status": "PLAN_VALIDATION_FAILED",
            "mode": (
                "COMMIT"
                if args.commit
                else "PREFLIGHT_ONLY"
            ),
            "plan_path": str(plan_path),
            "errors": validation_errors,
            "safety": {
                "database_modified": False,
                "eagle_items_updated": 0,
            },
        }

        report_path = write_report(
            timestamp,
            report,
        )

        print(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)

    prepared, preflight_errors = preflight(
        plan_items
    )

    if preflight_errors:
        report = {
            "status": "PREFLIGHT_FAILED",
            "mode": (
                "COMMIT"
                if args.commit
                else "PREFLIGHT_ONLY"
            ),
            "plan_path": str(plan_path),
            "planned_items": len(plan_items),
            "preflight_ready": len(prepared),
            "errors": preflight_errors,
            "safety": {
                "database_modified": False,
                "eagle_items_updated": 0,
            },
        }

        write_report(timestamp, report)

        print(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)

    if not args.commit:
        preview = {
            "status": "PREFLIGHT_READY",
            "mode": "PREFLIGHT_ONLY",
            "plan_path": str(plan_path),
            "planned_items": len(plan_items),
            "preflight_ready": len(prepared),
            "already_correct": sum(
                1
                for item in prepared
                if (
                    item["actual_current_name"]
                    == item["proposed_name"]
                )
            ),
            "names_to_change": sum(
                1
                for item in prepared
                if (
                    item["actual_current_name"]
                    != item["proposed_name"]
                )
            ),
            "changes": [
                {
                    "eagle_item_id": item[
                        "eagle_item_id"
                    ],
                    "old_name": item[
                        "actual_current_name"
                    ],
                    "new_name": item[
                        "proposed_name"
                    ],
                }
                for item in prepared
            ],
            "safety": {
                "database_modified": False,
                "eagle_items_updated": 0,
            },
        }

        write_report(timestamp, preview)

        print(
            json.dumps(
                preview,
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    backup_path = backup_database(timestamp)
    changed: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    try:
        for item in prepared:
            eagle_id = item["eagle_item_id"]
            old_name = item["actual_current_name"]
            new_name = item["proposed_name"]

            if old_name == new_name:
                results.append(
                    {
                        "eagle_item_id": eagle_id,
                        "status": "ALREADY_CORRECT",
                        "old_name": old_name,
                        "new_name": new_name,
                        "verified": True,
                    }
                )
                continue

            update_response = eagle_update_name(
                eagle_id,
                new_name,
            )

            changed.append(item)

            verified, readback = wait_for_name(
                eagle_id,
                new_name,
            )

            result = {
                "eagle_item_id": eagle_id,
                "status": (
                    "RENAMED_AND_VERIFIED"
                    if verified
                    else "RENAME_READBACK_FAILED"
                ),
                "old_name": old_name,
                "new_name": new_name,
                "verified": verified,
                "readback_name": (
                    readback.get("name")
                    if readback
                    else None
                ),
                "update_response": update_response,
            }

            results.append(result)

            if not verified:
                raise RuntimeError(
                    "Read-back verification failed for "
                    f"{eagle_id}"
                )

        save_order_registry(plan_items)

    except Exception as error:
        rollback = rollback_names(changed)

        report = {
            "status": "APPLY_FAILED_ROLLBACK_ATTEMPTED",
            "mode": "COMMIT",
            "plan_path": str(plan_path),
            "backup_path": str(backup_path),
            "error": (
                f"{type(error).__name__}: {error}"
            ),
            "results_before_failure": results,
            "rollback": rollback,
            "summary": {
                "planned_items": len(plan_items),
                "changed_before_failure": len(changed),
                "rollback_verified": sum(
                    1
                    for item in rollback
                    if item.get("verified")
                ),
                "rollback_failed": sum(
                    1
                    for item in rollback
                    if not item.get("verified")
                ),
            },
        }

        write_report(timestamp, report)

        print(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)

    report = {
        "status": "ORDER_APPLIED_AND_VERIFIED",
        "mode": "COMMIT",
        "plan_path": str(plan_path),
        "backup_path": str(backup_path),
        "summary": {
            "registered_jobs": plan["summary"][
                "registered_jobs"
            ],
            "registered_posts": plan["summary"][
                "registered_posts"
            ],
            "planned_items": len(plan_items),
            "renamed_items": sum(
                1
                for result in results
                if result["status"]
                == "RENAMED_AND_VERIFIED"
            ),
            "already_correct": sum(
                1
                for result in results
                if result["status"]
                == "ALREADY_CORRECT"
            ),
            "verified_items": sum(
                1
                for result in results
                if result["verified"]
            ),
            "order_rows_registered": len(
                {
                    item["post_id"]
                    for item in plan_items
                }
            ),
        },
        "results": results,
        "safety": {
            "eagle_items_created": 0,
            "eagle_items_deleted": 0,
            "source_files_deleted": 0,
            "database_backup_created": True,
        },
    }

    write_report(timestamp, report)

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
