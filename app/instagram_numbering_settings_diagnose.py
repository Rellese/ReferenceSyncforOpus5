from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT / "reports"
DATABASE = PROJECT / "data" / "reference_sync.sqlite3"

TARGETS = [
    PROJECT / "app" / "instagram_sync.py",
    PROJECT / "app" / "instagram_download_staging.py",
    PROJECT / "app" / "eagle_import_staging.py",
    PROJECT / "app" / "instagram_finalize_job.py",
]

KEYWORDS = (
    "ArgumentParser",
    "add_argument",
    "batch_size",
    "batch-size",
    "build_plan",
    "post_number",
    "component_number",
    "component_index",
    "component_count",
    "instpoporder",
    "proposed_name",
    "job.json",
    "json.dump",
    "subprocess",
    "instagram_download_staging",
    "eagle_import_staging",
    "instagram_finalize_job",
)

RELEVANT_FUNCTIONS = {
    "main",
    "build_plan",
    "parse_args",
    "run_command",
    "run_step",
    "latest_ready_job",
    "inspect_downloaded_media",
    "ensure_schema",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_source(path: Path) -> dict:
    result = {
        "path": str(path),
        "exists": path.exists(),
    }

    if not path.exists():
        return result

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    result.update(
        {
            "sha256": sha256_file(path),
            "line_count": len(lines),
            "functions": [],
            "matching_contexts": [],
        }
    )

    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                source = "\n".join(
                    lines[node.lineno - 1 : getattr(node, "end_lineno", node.lineno)]
                )
                if (
                    node.name in RELEVANT_FUNCTIONS
                    or any(keyword in source for keyword in KEYWORDS)
                ):
                    result["functions"].append(
                        {
                            "name": node.name,
                            "start_line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                            "arguments": [arg.arg for arg in node.args.args],
                        }
                    )
    except SyntaxError as exc:
        result["syntax_error"] = str(exc)

    matched_lines = set()

    for index, line in enumerate(lines, start=1):
        if any(keyword in line for keyword in KEYWORDS):
            start = max(1, index - 4)
            end = min(len(lines), index + 4)
            key = (start, end)

            if key in matched_lines:
                continue

            matched_lines.add(key)
            result["matching_contexts"].append(
                {
                    "match_line": index,
                    "start_line": start,
                    "end_line": end,
                    "text": "\n".join(
                        f"{line_number:04d}: {lines[line_number - 1]}"
                        for line_number in range(start, end + 1)
                    ),
                }
            )

    return result


def inspect_latest_job() -> dict:
    incoming = PROJECT / "downloads" / "instagram" / "incoming"
    candidates = sorted(
        incoming.glob("*/job.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not candidates:
        return {
            "found": False,
            "incoming": str(incoming),
        }

    path = candidates[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    posts = data.get("posts") or []

    return {
        "found": True,
        "path": str(path),
        "top_level_keys": sorted(data.keys()),
        "job_id": data.get("job_id"),
        "status": data.get("status"),
        "post_count": len(posts),
        "first_post_keys": (
            sorted(posts[0].keys())
            if posts and isinstance(posts[0], dict)
            else []
        ),
        "existing_numbering_fields": {
            key: value
            for key, value in data.items()
            if any(word in key.lower() for word in ("number", "order", "start"))
        },
    }


def inspect_database() -> dict:
    if not DATABASE.exists():
        return {
            "exists": False,
            "path": str(DATABASE),
        }

    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row

    try:
        table_row = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'instagram_sync_post_order'
            """
        ).fetchone()

        if table_row is None:
            return {
                "exists": True,
                "path": str(DATABASE),
                "order_table_exists": False,
            }

        columns = [
            dict(row)
            for row in connection.execute(
                "PRAGMA table_info(instagram_sync_post_order)"
            ).fetchall()
        ]

        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM instagram_sync_post_order
                ORDER BY job_id, post_number, post_id
                """
            ).fetchall()
        ]

        grouped = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    job_id,
                    COUNT(*) AS post_count,
                    MIN(post_number) AS minimum_post_number,
                    MAX(post_number) AS maximum_post_number
                FROM instagram_sync_post_order
                GROUP BY job_id
                ORDER BY job_id
                """
            ).fetchall()
        ]

        overall = dict(
            connection.execute(
                """
                SELECT
                    COUNT(*) AS row_count,
                    MIN(post_number) AS minimum_post_number,
                    MAX(post_number) AS maximum_post_number
                FROM instagram_sync_post_order
                """
            ).fetchone()
        )

        return {
            "exists": True,
            "path": str(DATABASE),
            "order_table_exists": True,
            "create_sql": table_row["sql"],
            "columns": columns,
            "overall": overall,
            "by_job": grouped,
            "rows": rows,
        }
    finally:
        connection.close()


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    report = {
        "status": "NUMBERING_SETTINGS_DIAGNOSIS_COMPLETE",
        "mode": "READ_ONLY",
        "source_files": [inspect_source(path) for path in TARGETS],
        "latest_job": inspect_latest_job(),
        "order_registry": inspect_database(),
        "planned_feature": {
            "manual_start_number": True,
            "automatic_continue": True,
            "preview_before_download": True,
            "collision_warning": True,
        },
        "safety": {
            "source_code_modified": False,
            "database_modified": False,
            "eagle_items_created": 0,
            "eagle_items_updated": 0,
            "files_downloaded": 0,
        },
    }

    report_path = REPORTS / f"instagram_numbering_settings_diagnose_{timestamp}.json"
    report["report_path"] = str(report_path)

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
