from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]

TARGETS = [
    PROJECT / "app/eagle_import_staging.py",
    PROJECT / "app/instagram_download_staging.py",
    PROJECT / "app/instagram_finalize_job.py",
    PROJECT / "app/instagram_sync.py",
]

RELEVANT_TEXT = (
    "/api/v2/item/add",
    "item/add",
    '"name"',
    "'name'",
    "proposed_name",
    "owner_username",
    "component_index",
    "component_count",
    "job.json",
    "instagram_finalize_job",
    "eagle_import_staging",
)

RELEVANT_FUNCTION_NAMES = (
    "name",
    "plan",
    "payload",
    "import",
    "item",
    "media",
    "job",
    "final",
    "main",
    "run",
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def function_is_relevant(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source: str,
) -> bool:
    name = node.name.lower()

    if any(
        marker in name
        for marker in RELEVANT_FUNCTION_NAMES
    ):
        return True

    segment = ast.get_source_segment(
        source,
        node,
    ) or ""

    return any(
        marker in segment
        for marker in RELEVANT_TEXT
    )


def inspect_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
        }

    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()

    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return {
            "path": str(path),
            "exists": True,
            "syntax_error": str(error),
            "line_count": len(lines),
            "sha256": sha256_text(source),
        }

    all_functions: list[dict[str, Any]] = []
    relevant_functions: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue

        function_info = {
            "name": node.name,
            "start_line": node.lineno,
            "end_line": node.end_lineno,
        }

        all_functions.append(function_info)

        if not function_is_relevant(node, source):
            continue

        segment = ast.get_source_segment(
            source,
            node,
        )

        relevant_functions.append(
            {
                **function_info,
                "source": segment,
            }
        )

    matching_lines: list[dict[str, Any]] = []

    for number, line in enumerate(lines, start=1):
        if any(
            marker in line
            for marker in RELEVANT_TEXT
        ):
            matching_lines.append(
                {
                    "line": number,
                    "text": line,
                }
            )

    return {
        "path": str(path),
        "exists": True,
        "line_count": len(lines),
        "sha256": sha256_text(source),
        "all_functions": sorted(
            all_functions,
            key=lambda item: item["start_line"],
        ),
        "relevant_functions": sorted(
            relevant_functions,
            key=lambda item: item["start_line"],
        ),
        "matching_lines": matching_lines,
    }


def inspect_order_registry() -> dict[str, Any]:
    import sqlite3

    database = (
        PROJECT
        / "data/reference_sync.sqlite3"
    )

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row

    try:
        table = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'instagram_sync_post_order'
            """
        ).fetchone()

        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM instagram_sync_post_order
                ORDER BY job_id, post_number
                """
            ).fetchall()
        ]

        return {
            "table_exists": table is not None,
            "create_sql": (
                table["sql"] if table else None
            ),
            "row_count": len(rows),
            "rows": rows,
        }

    finally:
        connection.close()


def main() -> None:
    report = {
        "status": "NAMING_INTEGRATION_DIAGNOSIS_COMPLETE",
        "files": [
            inspect_file(path)
            for path in TARGETS
        ],
        "order_registry": inspect_order_registry(),
        "safety": {
            "source_code_modified": False,
            "database_modified": False,
            "eagle_items_updated": 0,
            "files_downloaded": 0,
        },
    }

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
