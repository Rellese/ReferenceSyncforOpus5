import json
from collections import defaultdict
from pathlib import Path

ROOT = Path("pinterest_downloaded")

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
    ".avif", ".bmp", ".tif", ".tiff",
}
VIDEO_EXTS = {
    ".mp4", ".mov", ".webm", ".m4v",
}

def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def first(*values):
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None

pins = {}
boards = {}
damaged_json = []
json_without_file = []
media_files = set()

for path in ROOT.rglob("*"):
    if path.is_file() and path.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS:
        media_files.add(path.resolve())

for json_path in ROOT.rglob("*.json"):
    if json_path.name == "info.json":
        continue

    media_path = Path(str(json_path)[:-5])

    if not media_path.is_file():
        json_without_file.append(str(json_path))
        continue

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        damaged_json.append((str(json_path), str(exc)))
        continue

    pin_id = first(
        data.get("id"),
        data.get("pin_id"),
    )

    if pin_id is None:
        continue

    pin_id = str(pin_id)

    board = data.get("board")
    if not isinstance(board, dict):
        board = {}

    board_id = first(
        board.get("id"),
        data.get("board_id"),
        json_path.parent.name,
    )
    board_id = str(board_id)

    board_name = first(
        board.get("name"),
        board.get("title"),
        data.get("board_name"),
        json_path.parent.name,
    )
    board_name = str(board_name)

    board_expected = to_int(first(
        board.get("pin_count"),
        board.get("pins_count"),
        data.get("pin_count"),
    ))

    boards.setdefault(board_id, {
        "id": board_id,
        "name": board_name,
        "expected": 0,
        "pins": set(),
    })

    boards[board_id]["name"] = board_name
    boards[board_id]["expected"] = max(
        boards[board_id]["expected"],
        board_expected,
    )
    boards[board_id]["pins"].add(pin_id)

    num = to_int(data.get("num"), 1)
    count = max(to_int(data.get("count"), 1), 1)

    record = pins.setdefault(pin_id, {
        "board_id": board_id,
        "board_name": board_name,
        "expected_components": 1,
        "positions": set(),
        "files": set(),
        "images": 0,
        "videos": 0,
    })

    record["expected_components"] = max(
        record["expected_components"],
        count,
    )
    record["positions"].add(num)
    record["files"].add(str(media_path.resolve()))

    ext = media_path.suffix.lower()
    if ext in IMAGE_EXTS:
        record["images"] += 1
    elif ext in VIDEO_EXTS:
        record["videos"] += 1

complete = []
partial = []

for pin_id, record in pins.items():
    expected = record["expected_components"]
    positions = record["positions"]

    if all(position in positions for position in range(1, expected + 1)):
        complete.append(pin_id)
    else:
        partial.append(pin_id)

print()
print("=== ОБЩИЙ РЕЗУЛЬТАТ ===")
print(f"Распознано досок:                  {len(boards)}")
print(f"Распознано пинов:                  {len(pins)}")
print(f"Полных пинов:                      {len(complete)}")
print(f"Частичных пинов:                   {len(partial)}")
print(f"Фотофайлов:                        {sum(p['images'] for p in pins.values())}")
print(f"Видеофайлов:                       {sum(p['videos'] for p in pins.values())}")
print(f"Всего фото/видео на диске:         {len(media_files)}")
print(f"JSON без соответствующего файла:   {len(json_without_file)}")
print(f"Повреждённых JSON:                 {len(damaged_json)}")

print()
print("=== ПО ДОСКАМ ===")

estimated_total = 0
estimated_remaining = 0

for board_id, board in sorted(
    boards.items(),
    key=lambda item: item[1]["name"].casefold(),
):
    recognized = len(board["pins"])
    expected = board["expected"]
    remaining = max(expected - recognized, 0) if expected else None

    if expected:
        estimated_total += expected
        estimated_remaining += remaining

    remaining_text = str(remaining) if remaining is not None else "неизвестно"

    print(
        f"- {board['name']}\n"
        f"  ID: {board_id}\n"
        f"  указано Pinterest: {expected or 'неизвестно'}; "
        f"скачано пинов: {recognized}; "
        f"примерно осталось: {remaining_text}"
    )

if estimated_total:
    print()
    print("=== ПРИБЛИЗИТЕЛЬНЫЙ ИТОГ ПО НАЙДЕННЫМ ДОСКАМ ===")
    print(f"Заявлено Pinterest:                {estimated_total}")
    print(f"Распознано локально:               {len(pins)}")
    print(f"Примерно осталось:                 {estimated_remaining}")
    print("Внимание: это оценка только по доскам, которые уже удалось открыть.")

if partial:
    Path("pinterest_partial_pins.txt").write_text(
        "\n".join(
            f"https://www.pinterest.com/pin/{pin_id}/"
            for pin_id in sorted(partial)
        ) + "\n",
        encoding="utf-8",
    )
    print()
    print(
        "Частичные пины записаны в: "
        "pinterest_partial_pins.txt"
    )

if json_without_file:
    Path("pinterest_json_without_media.txt").write_text(
        "\n".join(json_without_file) + "\n",
        encoding="utf-8",
    )

if damaged_json:
    Path("pinterest_damaged_json.txt").write_text(
        "\n".join(f"{p} | {e}" for p, e in damaged_json) + "\n",
        encoding="utf-8",
    )
