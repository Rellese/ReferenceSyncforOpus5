#!/usr/bin/env python3

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("pinterest_downloaded")
BOARD_CSV = Path("pinterest_board_inventory.csv")
PARTIAL_CSV = Path("pinterest_partial_pins.csv")

OUTPUT = Path("pinterest_eagle_manifest.json")
REPORT = Path("pinterest_eagle_manifest_report.json")
SAFE_URLS = Path("pinterest_eagle_safe_pin_urls.txt")
EXCLUDED_URLS = Path("pinterest_eagle_excluded_pin_urls.txt")

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
    ".mp4", ".mov", ".webm", ".m4v",
    ".heic", ".heif", ".avif",
}

def as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def clean(value):
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value).replace("\u00a0", " ")
    ).strip()

def parse_time_value(value):
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)):
        number = float(value)

        if number > 10_000_000_000:
            number /= 1000

        return number

    text = clean(value)

    if not text:
        return None

    if text.isdigit():
        return parse_time_value(int(text))

    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.timestamp()
    except Exception:
        return None

def extract_timestamp(data):
    candidates = [
        data.get("created_at"),
        data.get("date"),
        data.get("timestamp"),
        data.get("created_time"),
        data.get("saved_at"),
        data.get("time"),
    ]

    for value in candidates:
        timestamp = parse_time_value(value)

        if timestamp is not None:
            return timestamp, "metadata"

    pin_id = as_int(data.get("id"))

    if pin_id is not None:
        return float(pin_id), "pin_id"

    return 0.0, "filename"

def extract_board(data, json_path):
    board = data.get("board")

    if not isinstance(board, dict):
        board = {}

    board_id = clean(
        board.get("id")
        or data.get("board_id")
    )

    board_name = clean(
        board.get("name")
        or data.get("board_name")
    )

    try:
        folder = json_path.relative_to(ROOT).parts[0]
    except Exception:
        folder = ""

    match = re.match(r"^(\d+)_(.*)$", folder)

    if not board_id and match:
        board_id = match.group(1)

    if not board_name and match:
        board_name = match.group(2)

    return board_id, board_name

def nested_value(data, *path):
    current = data

    for key in path:
        if not isinstance(current, dict):
            return ""

        current = current.get(key)

    return clean(current)

def extract_description(data):
    candidates = [
        data.get("description"),
        data.get("title"),
        data.get("seo_description"),
        data.get("grid_title"),
        nested_value(data, "rich_metadata", "description"),
        nested_value(data, "rich_summary", "display_description"),
    ]

    for candidate in candidates:
        text = clean(candidate)

        if text:
            return text

    return ""

def extract_external_link(data):
    candidates = [
        data.get("link"),
        data.get("domain"),
        nested_value(data, "rich_metadata", "url"),
        nested_value(data, "rich_summary", "url"),
    ]

    for candidate in candidates:
        value = clean(candidate)

        if value.startswith(("http://", "https://")):
            if "pinterest." not in value.lower():
                return value

    return ""

if not ROOT.exists():
    raise SystemExit(f"Не найден каталог {ROOT}")

if not BOARD_CSV.exists():
    raise SystemExit(f"Не найден {BOARD_CSV}")

if not PARTIAL_CSV.exists():
    raise SystemExit(f"Не найден {PARTIAL_CSV}")

# Окончательные названия досок из инвентаризации.
board_catalog = {}

with BOARD_CSV.open(
    encoding="utf-8-sig",
    newline=""
) as f:
    for row in csv.DictReader(f):
        board_id = clean(row.get("board_id"))
        board_name = clean(row.get("board_name"))

        if board_id:
            board_catalog[board_id] = board_name or board_id

# Все 24 частичных пина исключаем из первого импорта.
excluded_ids = set()
excluded_rows = []

with PARTIAL_CSV.open(
    encoding="utf-8-sig",
    newline=""
) as f:
    for row in csv.DictReader(f):
        pin_id = clean(row.get("pin_id"))

        if pin_id:
            excluded_ids.add(pin_id)
            excluded_rows.append(row)

pins = {}
damaged_json = []
missing_media = []
unknown_board = []
duplicate_components = []
timestamp_sources = defaultdict(int)

for json_path in ROOT.rglob("*.json"):
    if json_path.name == "info.json":
        continue

    media_path = Path(str(json_path)[:-5])

    if media_path.suffix.lower() not in MEDIA_EXTENSIONS:
        continue

    try:
        data = json.loads(
            json_path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        damaged_json.append({
            "json": str(json_path),
            "error": str(exc),
        })
        continue

    pin_id = clean(
        data.get("id")
        or data.get("pin_id")
        or data.get("post_id")
    )

    if not pin_id:
        continue

    if pin_id in excluded_ids:
        continue

    if not media_path.exists():
        missing_media.append(str(media_path))
        continue

    board_id, metadata_board_name = extract_board(
        data,
        json_path
    )

    board_name = (
        board_catalog.get(board_id)
        or metadata_board_name
        or board_id
        or "Без доски"
    )

    if not board_id:
        unknown_board.append({
            "pin_id": pin_id,
            "json": str(json_path),
        })
        continue

    num = as_int(data.get("num"), 1) or 1
    count = as_int(data.get("count"), 1) or 1

    timestamp, timestamp_source = extract_timestamp(data)
    timestamp_sources[timestamp_source] += 1

    key = (board_id, pin_id)

    pin = pins.setdefault(
        key,
        {
            "board_id": board_id,
            "board_name": board_name,
            "pin_id": pin_id,
            "pin_url": (
                f"https://www.pinterest.com/pin/{pin_id}/"
            ),
            "expected_components": count,
            "timestamp": timestamp,
            "timestamp_source": timestamp_source,
            "description": extract_description(data),
            "external_link": extract_external_link(data),
            "author": "@nikitadoctor26",
            "components": {},
        },
    )

    pin["expected_components"] = max(
        pin["expected_components"],
        count
    )

    if timestamp < pin["timestamp"]:
        pin["timestamp"] = timestamp
        pin["timestamp_source"] = timestamp_source

    if not pin["description"]:
        pin["description"] = extract_description(data)

    if not pin["external_link"]:
        pin["external_link"] = extract_external_link(data)

    if num in pin["components"]:
        duplicate_components.append({
            "board_id": board_id,
            "pin_id": pin_id,
            "num": num,
            "first": pin["components"][num]["path"],
            "second": str(media_path.resolve()),
        })
        continue

    pin["components"][num] = {
        "num": num,
        "path": str(media_path.resolve()),
        "extension": media_path.suffix.lower().lstrip("."),
        "media_id": clean(
            data.get("media_id")
            or media_path.stem
        ),
    }

# Дополнительная проверка полноты безопасных пинов.
unexpected_partial = []

for pin in pins.values():
    expected = set(
        range(1, pin["expected_components"] + 1)
    )
    found = set(pin["components"])
    missing = sorted(expected - found)

    if missing:
        unexpected_partial.append({
            "board_id": pin["board_id"],
            "board_name": pin["board_name"],
            "pin_id": pin["pin_id"],
            "expected": pin["expected_components"],
            "found": sorted(found),
            "missing": missing,
        })

if damaged_json:
    raise SystemExit(
        f"Обнаружено повреждённых JSON: {len(damaged_json)}"
    )

if missing_media:
    raise SystemExit(
        f"JSON без медиафайла: {len(missing_media)}"
    )

if unknown_board:
    raise SystemExit(
        f"Не удалось определить доску: {len(unknown_board)}"
    )

if duplicate_components:
    Path("pinterest_duplicate_components.json").write_text(
        json.dumps(
            duplicate_components,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8",
    )

    raise SystemExit(
        "Обнаружены дубликаты позиций каруселей: "
        f"{len(duplicate_components)}. "
        "Подробности: pinterest_duplicate_components.json"
    )

if unexpected_partial:
    Path(
        "pinterest_unexpected_partial.json"
    ).write_text(
        json.dumps(
            unexpected_partial,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8",
    )

    raise SystemExit(
        "После исключения известных 24 partial "
        f"осталось неожиданных partial: "
        f"{len(unexpected_partial)}"
    )

boards = defaultdict(list)

for pin in pins.values():
    boards[pin["board_id"]].append(pin)

# Стабильный порядок досок по названию.
sorted_boards = sorted(
    boards.items(),
    key=lambda item: (
        item[1][0]["board_name"].casefold(),
        item[0],
    ),
)

manifest_boards = []
global_number = 0
total_media = 0

for board_number, (board_id, board_pins) in enumerate(
    sorted_boards,
    start=1,
):
    # От старых к новым.
    board_pins.sort(
        key=lambda pin: (
            pin["timestamp"],
            as_int(pin["pin_id"], 0),
            pin["pin_id"],
        )
    )

    output_pins = []

    for board_pin_number, pin in enumerate(
        board_pins,
        start=1,
    ):
        global_number += 1

        components = [
            pin["components"][num]
            for num in sorted(pin["components"])
        ]

        total_media += len(components)

        annotation_parts = []

        if pin["description"]:
            annotation_parts.append(pin["description"])

        if pin["external_link"]:
            annotation_parts.append(pin["external_link"])

        annotation_parts.append(
            "пинтерестпопорядку-"
            f"{board_number}-{board_pin_number}"
        )
        annotation_parts.append(
            f"пинпопорядку-{global_number}"
        )

        output_pins.append({
            "board_number": board_number,
            "board_pin_number": board_pin_number,
            "global_number": global_number,
            "pin_id": pin["pin_id"],
            "pin_url": pin["pin_url"],
            "name": pin["author"],
            "tags": ["Pinterest"],
            "annotation": "\n".join(annotation_parts),
            "timestamp": pin["timestamp"],
            "timestamp_source": pin["timestamp_source"],
            "expected_components": pin[
                "expected_components"
            ],
            "components": components,
        })

    manifest_boards.append({
        "board_number": board_number,
        "board_id": board_id,
        "board_name": board_pins[0]["board_name"],
        "safe_pin_count": len(output_pins),
        "media_count": sum(
            len(pin["components"])
            for pin in output_pins
        ),
        "pins": output_pins,
    })

manifest = {
    "version": 1,
    "created_at": datetime.now(
        timezone.utc
    ).isoformat(),
    "source_root": str(ROOT.resolve()),
    "root_folder_name": "Pinterest",
    "excluded_partial_pin_ids": sorted(excluded_ids),
    "summary": {
        "boards": len(manifest_boards),
        "safe_pins": global_number,
        "media_files": total_media,
        "excluded_partial_pins": len(excluded_ids),
    },
    "boards": manifest_boards,
}

OUTPUT.write_text(
    json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8",
)

safe_urls = [
    pin["pin_url"]
    for board in manifest_boards
    for pin in board["pins"]
]

SAFE_URLS.write_text(
    "\n".join(safe_urls) + "\n",
    encoding="utf-8",
)

excluded_urls = [
    f"https://www.pinterest.com/pin/{pin_id}/"
    for pin_id in sorted(excluded_ids)
]

EXCLUDED_URLS.write_text(
    "\n".join(excluded_urls) + "\n",
    encoding="utf-8",
)

report = {
    "manifest": str(OUTPUT),
    "boards": len(manifest_boards),
    "safe_pins": global_number,
    "media_files": total_media,
    "excluded_partial_pins": len(excluded_ids),
    "damaged_json": len(damaged_json),
    "missing_media": len(missing_media),
    "unknown_board": len(unknown_board),
    "duplicate_components": len(duplicate_components),
    "unexpected_partial": len(unexpected_partial),
    "timestamp_sources": dict(timestamp_sources),
    "first_board": (
        manifest_boards[0]["board_name"]
        if manifest_boards else None
    ),
    "last_board": (
        manifest_boards[-1]["board_name"]
        if manifest_boards else None
    ),
}

REPORT.write_text(
    json.dumps(
        report,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8",
)

print("========== EAGLE MANIFEST ==========")
print("Досок:", len(manifest_boards))
print("Безопасных пинов:", global_number)
print("Медиафайлов:", total_media)
print("Исключено partial:", len(excluded_ids))
print("Повреждённых JSON:", len(damaged_json))
print("JSON без медиа:", len(missing_media))
print("Неизвестных досок:", len(unknown_board))
print("Дубликатов компонентов:", len(
    duplicate_components
))
print("Неожиданных partial:", len(
    unexpected_partial
))
print("Источники порядка:", dict(timestamp_sources))
print()
print("Созданы:")
print("-", OUTPUT)
print("-", REPORT)
print("-", SAFE_URLS)
print("-", EXCLUDED_URLS)
