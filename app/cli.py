from __future__ import annotations

import argparse
import json

from app.database import Database
from app.health import build_health_report
from app.settings import Settings


def print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_init(settings: Settings) -> None:
    database = Database(settings.database_path)
    database.initialize()

    print_json(
        {
            "status": "ok",
            "message": "ReferenceSync database initialized",
            "database": str(settings.database_path),
            "summary": database.summary(),
            "safety": {
                "eagle_library_modified": False,
                "existing_instagram_modified": False,
            },
        }
    )


def command_status(settings: Settings) -> None:
    database = Database(settings.database_path)

    if not settings.database_path.exists():
        raise SystemExit(
            "Database does not exist. Run: python -m app.cli init"
        )

    print_json(
        {
            "status": "ok",
            "database": database.summary(),
            "health": build_health_report(settings),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ReferenceSync",
        description="ReferenceSync local download and Eagle import engine",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Initialize the SQLite registry")
    subparsers.add_parser("status", help="Show system status")

    args = parser.parse_args()
    settings = Settings.load()

    if args.command == "init":
        command_init(settings)
    elif args.command == "status":
        command_status(settings)


if __name__ == "__main__":
    main()
