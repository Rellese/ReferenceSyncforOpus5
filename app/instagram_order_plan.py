from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
DATABASE = PROJECT / "data/reference_sync.sqlite3"
REPORTS = PROJECT / "reports"

ORDER_MARKER = "instpoporder"


def normalize_post_id(value: Any) -> str | None:
    if value is None:
        return None

    value = str(value).strip()
    return value if value.isdigit() else None


def walk_post_records(value: Any) -> list[dict[str, Any]]:
    """
    Находит публикации внутри discovery JSON,
    сохраняя порядок их появления.
    """
    found: list[dict[str, Any]] = []

    def visit(current: Any) -> None:
        if isinstance(current, list):
            for item in current:
                visit(item)
            return

        if not isinstance(current, dict):
            return

        post_id = normalize_post_id(
            current.get("post_id")
            or current.get("external_post_id")
            or current.get("instagram_post_id")
        )

        has_metadata = any(
            key in current
            for key in (
                "shortcode",
                "canonical_url",
                "post_url",
                "url",
                "owner_username",
                "username",
                "component_count",
            )
        )

        if post_id and has_metadata:
            found.append(
                {
                    "post_id": post_id,
                    "shortcode": current.get("shortcode"),
                    "url": (
                        current.get("canonical_url")
                        or current.get("post_url")
                        or current.get("url")
                    ),
                    "username": (
                        current.get("owner_username")
                        or current.get("username")
                    ),
                }
            )

        for child in current.values():
            if isinstance(child, (dict, list)):
                visit(child)

    visit(value)

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()

    for record in found:
        post_id = record["post_id"]

        if post_id in seen:
            continue

        seen.add(post_id)
        unique.append(record)

    return unique


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_report_path(value: Any) -> Path | None:
    if not value:
        return None

    path = Path(str(value)).expanduser()

    if not path.is_absolute():
        path = PROJECT / path

    return path.resolve()


def normalize_author(value: Any) -> str:
    author = str(value or "").strip()

    if not author:
        return "@instagram"

    if author.startswith("@"):
        return author

    return f"@{author}"


def load_database() -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row

    try:
        jobs = {
            str(row["job_id"]): dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM instagram_sync_jobs
                ORDER BY created_at ASC, job_id ASC
                """
            ).fetchall()
        }

        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    p.post_id,
                    p.shortcode,
                    p.canonical_url,
                    p.owner_username,
                    p.component_count,
                    p.first_seen_at,

                    m.media_id,
                    m.component_index,
                    m.extension,
                    m.file_size,

                    i.id AS import_registry_id,
                    i.eagle_item_id,
                    i.job_id,
                    i.verification_status,
                    i.imported_at

                FROM instagram_sync_posts AS p

                JOIN instagram_sync_media AS m
                  ON m.post_id = p.post_id

                JOIN instagram_sync_imports AS i
                  ON i.media_id = m.media_id

                ORDER BY
                    i.job_id ASC,
                    p.first_seen_at ASC,
                    p.post_id ASC,
                    m.component_index ASC,
                    i.id ASC
                """
            ).fetchall()
        ]

        return rows, jobs

    finally:
        connection.close()


def load_job_discovery_order(
    job: dict[str, Any],
) -> dict[str, Any]:
    report_path = resolve_report_path(
        job.get("discovery_report")
    )

    if report_path and report_path.exists():
        try:
            records = walk_post_records(load_json(report_path))

            if records:
                return {
                    "status": "ORDER_LOADED",
                    "report_path": str(report_path),
                    "records": records,
                }
        except Exception as error:
            direct_error = (
                f"{type(error).__name__}: {error}"
            )
        else:
            direct_error = "NO_POST_RECORDS_IN_REPORT"
    else:
        direct_error = "DISCOVERY_REPORT_NOT_FOUND"

    # Резервный поиск: проверяем discovery-отчёты,
    # выбирая тот, где встречается больше постов задания.
    candidates: list[dict[str, Any]] = []

    for path in sorted(
        REPORTS.glob("instagram_discovery*.json")
    ):
        try:
            records = walk_post_records(load_json(path))
        except Exception:
            continue

        if records:
            candidates.append(
                {
                    "path": path,
                    "records": records,
                    "modified": path.stat().st_mtime,
                }
            )

    return {
        "status": "DIRECT_REPORT_UNAVAILABLE",
        "report_path": (
            str(report_path) if report_path else None
        ),
        "direct_error": direct_error,
        "fallback_candidates": candidates,
        "records": [],
    }


def select_fallback_order(
    discovery: dict[str, Any],
    job_post_ids: set[str],
) -> dict[str, Any]:
    candidates = discovery.get(
        "fallback_candidates",
        [],
    )

    scored: list[dict[str, Any]] = []

    for candidate in candidates:
        record_ids = {
            record["post_id"]
            for record in candidate["records"]
        }

        overlap = len(record_ids & job_post_ids)

        if overlap:
            scored.append(
                {
                    **candidate,
                    "overlap": overlap,
                }
            )

    if not scored:
        return discovery

    scored.sort(
        key=lambda item: (
            item["overlap"],
            item["modified"],
        ),
        reverse=True,
    )

    best = scored[0]

    return {
        "status": "ORDER_LOADED_FROM_FALLBACK",
        "report_path": str(best["path"]),
        "records": best["records"],
        "overlap": best["overlap"],
        "direct_error": discovery.get("direct_error"),
    }


def component_number_map(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    parsed: list[tuple[dict[str, Any], int | None]] = []

    for row in rows:
        try:
            index = int(row["component_index"])
        except (TypeError, ValueError):
            index = None

        parsed.append((row, index))

    valid = [
        index
        for _, index in parsed
        if index is not None
    ]

    zero_based = bool(valid) and min(valid) == 0

    result: dict[str, int] = {}

    for fallback, (row, index) in enumerate(
        parsed,
        start=1,
    ):
        if index is None:
            number = fallback
        elif zero_based:
            number = index + 1
        else:
            number = index

        result[str(row["media_id"])] = number

    return result


def main() -> None:
    if not DATABASE.exists():
        raise SystemExit(
            f"Database not found: {DATABASE}"
        )

    REPORTS.mkdir(parents=True, exist_ok=True)

    registry_rows, jobs = load_database()

    rows_by_job: dict[str, list[dict[str, Any]]] = {}

    for row in registry_rows:
        job_id = str(row["job_id"])
        rows_by_job.setdefault(job_id, []).append(row)

    plan: list[dict[str, Any]] = []
    job_diagnostics: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for job_id, job_rows in rows_by_job.items():
        job = jobs.get(job_id)

        if not job:
            unresolved.append(
                {
                    "job_id": job_id,
                    "reason": "JOB_NOT_FOUND_IN_REGISTRY",
                }
            )
            continue

        job_post_ids = {
            str(row["post_id"])
            for row in job_rows
        }

        discovery = load_job_discovery_order(job)

        if not discovery.get("records"):
            discovery = select_fallback_order(
                discovery,
                job_post_ids,
            )

        records = discovery.get("records", [])

        # Оставляем только публикации, действительно вошедшие
        # в текущее задание. Уже известные посты из discovery-отчёта
        # не должны занимать номера внутри новой пачки.
        job_records = [
            record
            for record in records
            if record["post_id"] in job_post_ids
        ]

        position_by_post_id = {
            record["post_id"]: position
            for position, record in enumerate(
                job_records,
                start=1,
            )
        }

        job_diagnostics.append(
            {
                "job_id": job_id,
                "discovery_status": discovery.get(
                    "status"
                ),
                "discovery_report": discovery.get(
                    "report_path"
                ),
                "posts_in_job": len(job_post_ids),
                "posts_in_discovery_report": len(records),
                "resolved_job_posts": len(
                    job_post_ids
                    & set(position_by_post_id)
                ),
            }
        )

        rows_by_post: dict[
            str,
            list[dict[str, Any]],
        ] = {}

        for row in job_rows:
            post_id = str(row["post_id"])
            rows_by_post.setdefault(
                post_id,
                [],
            ).append(row)

        for post_id, post_rows in rows_by_post.items():
            post_number = position_by_post_id.get(
                post_id
            )

            if post_number is None:
                unresolved.append(
                    {
                        "job_id": job_id,
                        "post_id": post_id,
                        "shortcode": post_rows[0].get(
                            "shortcode"
                        ),
                        "reason": (
                            "POST_NOT_FOUND_IN_JOB_"
                            "DISCOVERY_ORDER"
                        ),
                    }
                )
                continue

            numbers = component_number_map(post_rows)

            try:
                component_count = int(
                    post_rows[0].get(
                        "component_count"
                    ) or len(post_rows)
                )
            except (TypeError, ValueError):
                component_count = len(post_rows)

            # Фактическое количество зарегистрированных файлов
            # надёжнее ошибочного или отсутствующего metadata.
            component_count = max(
                component_count,
                len(post_rows),
            )

            for row in post_rows:
                component_number = numbers[
                    str(row["media_id"])
                ]

                author = normalize_author(
                    row.get("owner_username")
                )

                if component_count <= 1:
                    proposed_name = (
                        f"{author} "
                        f"{ORDER_MARKER}-{post_number}"
                    )
                else:
                    proposed_name = (
                        f"{author} "
                        f"{ORDER_MARKER}-"
                        f"{post_number}-"
                        f"{component_number}"
                    )

                plan.append(
                    {
                        "job_id": job_id,
                        "post_number": post_number,
                        "component_number": (
                            component_number
                            if component_count > 1
                            else None
                        ),
                        "component_count": component_count,
                        "post_id": post_id,
                        "shortcode": row.get("shortcode"),
                        "url": row.get("canonical_url"),
                        "author": author,
                        "media_id": row.get("media_id"),
                        "component_index_in_database": (
                            row.get("component_index")
                        ),
                        "extension": row.get(
                            "extension"
                        ),
                        "file_size": row.get(
                            "file_size"
                        ),
                        "eagle_item_id": row.get(
                            "eagle_item_id"
                        ),
                        "current_name": author,
                        "proposed_name": proposed_name,
                        "verification_status": row.get(
                            "verification_status"
                        ),
                    }
                )

    plan.sort(
        key=lambda item: (
            item["job_id"],
            item["post_number"],
            item["component_number"] or 0,
        )
    )

    registered_posts = {
        str(row["post_id"])
        for row in registry_rows
    }

    planned_posts = {
        item["post_id"]
        for item in plan
    }

    duplicate_targets: list[dict[str, Any]] = []
    seen_targets: dict[
        tuple[str, str],
        str,
    ] = {}

    for item in plan:
        key = (
            item["job_id"],
            item["proposed_name"],
        )

        if key in seen_targets:
            duplicate_targets.append(
                {
                    "job_id": item["job_id"],
                    "proposed_name": (
                        item["proposed_name"]
                    ),
                    "first_eagle_id": (
                        seen_targets[key]
                    ),
                    "second_eagle_id": (
                        item["eagle_item_id"]
                    ),
                }
            )
        else:
            seen_targets[key] = item[
                "eagle_item_id"
            ]

    ready = (
        len(plan) == len(registry_rows)
        and planned_posts == registered_posts
        and not unresolved
        and not duplicate_targets
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    report_path = (
        REPORTS
        / f"instagram_order_plan_{timestamp}.json"
    )

    report = {
        "status": (
            "ORDER_PLAN_READY"
            if ready
            else "ORDER_PLAN_REQUIRES_REVIEW"
        ),
        "mode": "PREVIEW_ONLY",
        "order_marker": ORDER_MARKER,
        "name_templates": {
            "single_post": (
                "{author} "
                "instpoporder-{post_number}"
            ),
            "carousel": (
                "{author} "
                "instpoporder-"
                "{post_number}-"
                "{component_number}"
            ),
        },
        "summary": {
            "registered_jobs": len(rows_by_job),
            "registered_posts": len(
                registered_posts
            ),
            "registered_media": len(
                registry_rows
            ),
            "planned_posts": len(planned_posts),
            "planned_renames": len(plan),
            "unresolved_entries": len(
                unresolved
            ),
            "duplicate_target_names": len(
                duplicate_targets
            ),
        },
        "job_diagnostics": job_diagnostics,
        "plan": plan,
        "unresolved": unresolved,
        "duplicate_targets": duplicate_targets,
        "safety": {
            "database_modified": False,
            "eagle_items_created": 0,
            "eagle_items_updated": 0,
            "eagle_items_deleted": 0,
            "files_downloaded": 0,
        },
        "report_path": str(report_path),
    }

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
