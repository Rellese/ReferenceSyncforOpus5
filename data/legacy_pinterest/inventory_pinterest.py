#!/usr/bin/env python3

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
    ".mp4", ".mov", ".webm", ".m4v", ".heic", ".heif", ".avif",
}

def value_as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def folder_board_fields(root, path):
    try:
        relative = path.relative_to(root)
        folder = relative.parts[0] if len(relative.parts) > 1 else "__ROOT__"
    except ValueError:
        folder = "__UNKNOWN__"

    match = re.match(r"^(\d+)_(.*)$", folder)

    if match:
        return folder, match.group(1), match.group(2)

    return folder, "", folder

def extract_board(data, root, path):
    folder, folder_id, folder_name = folder_board_fields(root, path)

    board = data.get("board")
    if not isinstance(board, dict):
        board = {}

    board_id = (
        board.get("id")
        or data.get("board_id")
        or folder_id
        or folder
    )

    board_name = (
        board.get("name")
        or data.get("board_name")
        or folder_name
        or folder
    )

    pin_count = (
        value_as_int(board.get("pin_count"))
        or value_as_int(board.get("pins_count"))
        or value_as_int(data.get("board_pin_count"))
        or value_as_int(data.get("pin_count"))
    )

    return {
        "key": str(board_id),
        "id": str(board_id),
        "name": str(board_name),
        "folder": folder,
        "expected": pin_count,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default="pinterest_downloaded",
        help="Каталог с загрузками Pinterest",
    )
    parser.add_argument("--expected-boards", type=int, default=115)
    parser.add_argument("--expected-pins", type=int, default=7431)
    args = parser.parse_args()

    root = Path(args.root)

    if not root.exists():
        raise SystemExit(f"Не найден каталог: {root}")

    boards = {}
    pins = {}
    damaged_json = []
    json_without_media = []
    metadata_media = set()
    sidecars = 0

    # Сначала пробуем зарегистрировать доски из info.json.
    for info_path in root.rglob("info.json"):
        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception as exc:
            damaged_json.append({
                "json": str(info_path),
                "error": str(exc),
            })
            continue

        board_info = extract_board(data, root, info_path)
        record = boards.setdefault(
            board_info["key"],
            {
                "id": board_info["id"],
                "name": board_info["name"],
                "folders": set(),
                "expected_values": set(),
            },
        )

        record["folders"].add(board_info["folder"])

        if board_info["expected"] is not None:
            record["expected_values"].add(board_info["expected"])

    # Затем обрабатываем JSON каждого медиафайла.
    for json_path in root.rglob("*.json"):
        if json_path.name == "info.json":
            continue

        media_path = Path(str(json_path)[:-5])

        # Нас интересуют sidecar-файлы вида file.jpg.json/file.mp4.json.
        if media_path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue

        sidecars += 1

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            damaged_json.append({
                "json": str(json_path),
                "error": str(exc),
            })
            continue

        board_info = extract_board(data, root, json_path)

        board_record = boards.setdefault(
            board_info["key"],
            {
                "id": board_info["id"],
                "name": board_info["name"],
                "folders": set(),
                "expected_values": set(),
            },
        )

        board_record["folders"].add(board_info["folder"])

        if board_info["expected"] is not None:
            board_record["expected_values"].add(board_info["expected"])

        pin_id = str(
            data.get("id")
            or data.get("pin_id")
            or data.get("post_id")
            or ""
        ).strip()

        if not pin_id:
            continue

        num = value_as_int(data.get("num")) or 1
        count = value_as_int(data.get("count")) or 1
        media_id = str(data.get("media_id") or media_path.name)

        pin_key = (board_info["key"], pin_id)

        pin = pins.setdefault(
            pin_key,
            {
                "board_id": board_info["id"],
                "board_name": board_info["name"],
                "pin_id": pin_id,
                "expected": 1,
                "parts": defaultdict(list),
            },
        )

        pin["expected"] = max(pin["expected"], count)

        exists = media_path.exists()

        pin["parts"][num].append({
            "media_id": media_id,
            "media": str(media_path),
            "json": str(json_path),
            "exists": exists,
            "extension": media_path.suffix.lower(),
        })

        if exists:
            try:
                metadata_media.add(media_path.resolve())
            except OSError:
                metadata_media.add(media_path)

        else:
            json_without_media.append({
                "board_id": board_info["id"],
                "board_name": board_info["name"],
                "pin_id": pin_id,
                "num": num,
                "json": str(json_path),
                "expected_media": str(media_path),
            })

    board_stats = {}
    partial_rows = []
    complete_pins = 0
    partial_pins = 0
    suspicious_last_only = 0

    for (board_key, pin_id), pin in pins.items():
        expected_positions = set(range(1, pin["expected"] + 1))

        existing_positions = {
            num
            for num, components in pin["parts"].items()
            if any(component["exists"] for component in components)
        }

        missing_positions = sorted(expected_positions - existing_positions)
        extra_positions = sorted(existing_positions - expected_positions)

        complete = not missing_positions

        if complete:
            complete_pins += 1
        else:
            partial_pins += 1

            last_only = (
                missing_positions == [pin["expected"]]
                and existing_positions
                == set(range(1, pin["expected"]))
            )

            if last_only:
                suspicious_last_only += 1

            partial_rows.append({
                "board_id": pin["board_id"],
                "board_name": pin["board_name"],
                "pin_id": pin_id,
                "expected_components": pin["expected"],
                "found_positions": ",".join(
                    map(str, sorted(existing_positions))
                ),
                "missing_positions": ",".join(
                    map(str, missing_positions)
                ),
                "extra_positions": ",".join(
                    map(str, extra_positions)
                ),
                "missing_only_last": "yes" if last_only else "no",
                "pin_url": f"https://www.pinterest.com/pin/{pin_id}/",
            })

        stat = board_stats.setdefault(
            board_key,
            {
                "pins": 0,
                "complete": 0,
                "partial": 0,
                "media": set(),
            },
        )

        stat["pins"] += 1

        if complete:
            stat["complete"] += 1
        else:
            stat["partial"] += 1

        for components in pin["parts"].values():
            for component in components:
                if component["exists"]:
                    stat["media"].add(component["media"])

    all_media = []

    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
            all_media.append(path)

    orphan_media = []

    for media_path in all_media:
        json_path = Path(str(media_path) + ".json")

        if not json_path.exists():
            orphan_media.append({
                "media": str(media_path),
                "expected_json": str(json_path),
            })

    board_rows = []

    for board_key, board in boards.items():
        stat = board_stats.get(
            board_key,
            {
                "pins": 0,
                "complete": 0,
                "partial": 0,
                "media": set(),
            },
        )

        expected = (
            max(board["expected_values"])
            if board["expected_values"]
            else None
        )

        downloaded = stat["pins"]

        remaining = (
            max(0, expected - downloaded)
            if expected is not None
            else None
        )

        percent = (
            min(100.0, downloaded / expected * 100)
            if expected
            else None
        )

        board_rows.append({
            "board_id": board["id"],
            "board_name": board["name"],
            "folders": " | ".join(sorted(board["folders"])),
            "pinterest_pin_count": expected if expected is not None else "",
            "recognized_pins": downloaded,
            "complete_pins": stat["complete"],
            "partial_pins": stat["partial"],
            "media_files": len(stat["media"]),
            "estimated_remaining": remaining if remaining is not None else "",
            "completion_percent": (
                f"{percent:.2f}" if percent is not None else ""
            ),
        })

    board_rows.sort(
        key=lambda row: (
            row["board_name"].casefold(),
            row["board_id"],
        )
    )

    partial_rows.sort(
        key=lambda row: (
            row["board_name"].casefold(),
            row["pin_id"],
        )
    )

    recognized_board_entries = len(pins)
    unique_pin_ids = len({pin_id for _, pin_id in pins})
    boards_with_pins = sum(
        1 for row in board_rows if row["recognized_pins"] > 0
    )

    reported_total = sum(
        int(row["pinterest_pin_count"])
        for row in board_rows
        if row["pinterest_pin_count"] != ""
    )

    estimated_known_remaining = sum(
        int(row["estimated_remaining"])
        for row in board_rows
        if row["estimated_remaining"] != ""
    )

    board_csv = Path("pinterest_board_inventory.csv")
    partial_csv = Path("pinterest_partial_pins.csv")
    missing_media_csv = Path("pinterest_json_without_media.csv")
    orphan_csv = Path("pinterest_media_without_json.csv")
    damaged_csv = Path("pinterest_damaged_json.csv")
    report_path = Path("pinterest_inventory_report.json")

    def write_csv(path, rows, fieldnames):
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)

    write_csv(
        board_csv,
        board_rows,
        [
            "board_id",
            "board_name",
            "folders",
            "pinterest_pin_count",
            "recognized_pins",
            "complete_pins",
            "partial_pins",
            "media_files",
            "estimated_remaining",
            "completion_percent",
        ],
    )

    write_csv(
        partial_csv,
        partial_rows,
        [
            "board_id",
            "board_name",
            "pin_id",
            "expected_components",
            "found_positions",
            "missing_positions",
            "extra_positions",
            "missing_only_last",
            "pin_url",
        ],
    )

    write_csv(
        missing_media_csv,
        json_without_media,
        [
            "board_id",
            "board_name",
            "pin_id",
            "num",
            "json",
            "expected_media",
        ],
    )

    write_csv(
        orphan_csv,
        orphan_media,
        ["media", "expected_json"],
    )

    write_csv(
        damaged_csv,
        damaged_json,
        ["json", "error"],
    )

    report = {
        "root": str(root),
        "expected_boards_manual": args.expected_boards,
        "recognized_boards": len(board_rows),
        "boards_with_pins": boards_with_pins,
        "estimated_unseen_boards": max(
            0, args.expected_boards - len(board_rows)
        ),
        "expected_pin_entries_manual": args.expected_pins,
        "recognized_board_pin_entries": recognized_board_entries,
        "globally_unique_pin_ids": unique_pin_ids,
        "complete_pins": complete_pins,
        "partial_pins": partial_pins,
        "partial_missing_only_last": suspicious_last_only,
        "manual_total_remaining": max(
            0, args.expected_pins - recognized_board_entries
        ),
        "pinterest_reported_total_for_recognized_boards": reported_total,
        "estimated_remaining_in_recognized_boards": estimated_known_remaining,
        "metadata_sidecars": sidecars,
        "physical_media_files": len(all_media),
        "json_without_media": len(json_without_media),
        "media_without_json": len(orphan_media),
        "damaged_json": len(damaged_json),
    }

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    incomplete_boards = [
        row for row in board_rows
        if (
            row["partial_pins"] > 0
            or (
                row["estimated_remaining"] != ""
                and row["estimated_remaining"] > 0
            )
        )
    ]

    print("\n========== PINTEREST INVENTORY ==========")
    print("Ожидалось досок вручную:          ", args.expected_boards)
    print("Распознано досок:                 ", len(board_rows))
    print("Досок с пинами:                   ", boards_with_pins)
    print("Предположительно не найдено досок:", max(
        0, args.expected_boards - len(board_rows)
    ))
    print()
    print("Ожидалось записей пинов вручную:  ", args.expected_pins)
    print("Распознано записей доска+пин:     ", recognized_board_entries)
    print("Уникальных Pin ID глобально:      ", unique_pin_ids)
    print("Полных пинов:                     ", complete_pins)
    print("Частичных пинов:                  ", partial_pins)
    print("Из них отсутствует только последний компонент:",
          suspicious_last_only)
    print("До ручного общего количества:     ", max(
        0, args.expected_pins - recognized_board_entries
    ))
    print()
    print("Физических медиафайлов:           ", len(all_media))
    print("JSON sidecar:                     ", sidecars)
    print("JSON без медиа:                   ", len(json_without_media))
    print("Медиа без JSON:                   ", len(orphan_media))
    print("Повреждённых JSON:                ", len(damaged_json))
    print()
    print("Неполных/незавершённых досок:     ", len(incomplete_boards))

    for row in incomplete_boards[:30]:
        print(
            f"- {row['board_name']}: "
            f"{row['recognized_pins']}/"
            f"{row['pinterest_pin_count'] or '?'}; "
            f"partial={row['partial_pins']}; "
            f"осталось≈{row['estimated_remaining'] or 0}"
        )

    print("\nСозданы файлы:")
    print("-", report_path)
    print("-", board_csv)
    print("-", partial_csv)
    print("-", missing_media_csv)
    print("-", orphan_csv)
    print("-", damaged_csv)

if __name__ == "__main__":
    main()
