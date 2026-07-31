#!/usr/bin/env python3

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "http://localhost:41595/api"
MANIFEST_PATH = Path("pinterest_eagle_manifest.json")
STATE_PATH = Path("pinterest_eagle_import_state.json")

def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))

def atomic_write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")

    temporary.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temporary.replace(path)

def file_sha256(path):
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)

    return h.hexdigest()

def request_json(method, endpoint, payload=None, retries=3):
    url = API + endpoint
    body = None
    headers = {}

    if payload is not None:
        body = json.dumps(
            payload,
            ensure_ascii=False
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url,
                data=body,
                headers=headers,
                method=method
            )

            with urllib.request.urlopen(
                request,
                timeout=120
            ) as response:
                text = response.read().decode(
                    "utf-8",
                    errors="replace"
                )

            result = json.loads(text)

            if result.get("status") != "success":
                raise RuntimeError(
                    f"Eagle API вернул ошибку: {result}"
                )

            return result

        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
            RuntimeError
        ) as exc:
            last_error = exc

            if attempt < retries:
                print(
                    f"  Повтор API {attempt}/{retries}: {exc}"
                )
                time.sleep(attempt * 2)

    raise RuntimeError(
        f"Ошибка Eagle API {method} {endpoint}: "
        f"{last_error}"
    )

def application_info():
    return request_json(
        "GET",
        "/application/info"
    )

def folder_list():
    return request_json(
        "GET",
        "/folder/list"
    ).get("data", [])

def create_folder(name, parent=None):
    payload = {
        "folderName": name
    }

    if parent:
        payload["parent"] = parent

    result = request_json(
        "POST",
        "/folder/create",
        payload
    )

    data = result.get("data") or {}
    folder_id = data.get("id")

    if not folder_id:
        raise RuntimeError(
            f"Eagle не вернул ID папки {name!r}"
        )

    return folder_id

def find_root_folder(folders, name):
    for folder in folders:
        if (
            folder.get("name") == name
            and not folder.get("isDeleted")
        ):
            return folder.get("id")

    return None

def validate_manifest(manifest):
    summary = manifest.get("summary") or {}
    boards = manifest.get("boards") or []

    actual_pins = 0
    actual_media = 0
    missing_files = []

    for board in boards:
        for pin in board.get("pins", []):
            actual_pins += 1

            for component in pin.get("components", []):
                actual_media += 1
                path = Path(component["path"])

                if not path.is_file():
                    missing_files.append(str(path))

    expected_boards = int(summary.get("boards", 0))
    expected_pins = int(summary.get("safe_pins", 0))
    expected_media = int(summary.get("media_files", 0))

    if len(boards) != expected_boards:
        raise SystemExit(
            f"Число досок не совпало: "
            f"{len(boards)} != {expected_boards}"
        )

    if actual_pins != expected_pins:
        raise SystemExit(
            f"Число пинов не совпало: "
            f"{actual_pins} != {expected_pins}"
        )

    if actual_media != expected_media:
        raise SystemExit(
            f"Число медиа не совпало: "
            f"{actual_media} != {expected_media}"
        )

    if missing_files:
        Path(
            "pinterest_eagle_missing_files.txt"
        ).write_text(
            "\n".join(missing_files) + "\n",
            encoding="utf-8"
        )

        raise SystemExit(
            f"Не найдено медиафайлов: "
            f"{len(missing_files)}"
        )

    return {
        "boards": len(boards),
        "pins": actual_pins,
        "media": actual_media,
    }

def new_state(manifest_hash):
    return {
        "version": 1,
        "manifest_sha256": manifest_hash,
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "updated_at": None,
        "root_folder_id": None,
        "root_folder_name": None,
        "board_folder_ids": {},
        "imported_pins": {},
        "imported_media_count": 0,
        "failures": [],
    }

def save_state(state):
    state["updated_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    atomic_write_json(
        STATE_PATH,
        state
    )

def ensure_root_folder(manifest, state):
    root_name = (
        manifest.get("root_folder_name")
        or "Pinterest"
    )

    existing_id = state.get("root_folder_id")

    if existing_id:
        return existing_id

    folders = folder_list()
    found = find_root_folder(folders, root_name)

    if found:
        raise SystemExit(
            f"\nВ Eagle уже существует корневая папка "
            f"{root_name!r}, но новый state-файл её не знает.\n"
            "Чтобы избежать дубликатов, импорт остановлен.\n"
            "Переименуй существующую папку либо удали её, "
            "если она пустая, и повтори запуск."
        )

    print(f"Создаю корневую папку: {root_name}")
    root_id = create_folder(root_name)

    state["root_folder_id"] = root_id
    state["root_folder_name"] = root_name
    save_state(state)

    return root_id

def ensure_board_folder(board, root_id, state):
    board_id = str(board["board_id"])
    board_name = board["board_name"]

    existing = state["board_folder_ids"].get(
        board_id
    )

    if existing:
        return existing

    print(
        f"\nСоздаю доску "
        f"#{board['board_number']}: {board_name}"
    )

    folder_id = create_folder(
        board_name,
        parent=root_id
    )

    state["board_folder_ids"][board_id] = {
        "folder_id": folder_id,
        "board_name": board_name,
        "board_number": board["board_number"],
    }

    save_state(state)
    return folder_id

def import_pin(pin, folder_id):
    items = []

    for component in pin["components"]:
        path = Path(component["path"])

        items.append({
            "path": str(path),
            "name": pin["name"],
            "website": pin["pin_url"],
            "annotation": pin["annotation"],
            "tags": pin["tags"],
        })

    request_json(
        "POST",
        "/item/addFromPaths",
        {
            "items": items,
            "folderId": folder_id,
        },
        retries=3
    )

    return len(items)

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Безопасный импорт Pinterest в Eagle"
        )
    )

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--test",
        type=int,
        metavar="POSTS",
        help="Импортировать только первые N новых пинов"
    )

    mode.add_argument(
        "--all",
        action="store_true",
        help="Импортировать весь манифест"
    )

    args = parser.parse_args()

    if args.test is not None and args.test < 1:
        raise SystemExit(
            "--test должен быть больше нуля"
        )

    if not MANIFEST_PATH.exists():
        raise SystemExit(
            f"Не найден {MANIFEST_PATH}"
        )

    manifest_hash = file_sha256(MANIFEST_PATH)
    manifest = load_json(MANIFEST_PATH)
    totals = validate_manifest(manifest)

    print("========== PINTEREST → EAGLE ==========")
    print("Досок:", totals["boards"])
    print("Пинов:", totals["pins"])
    print("Медиа:", totals["media"])
    print("Манифест SHA256:", manifest_hash)
    print()

    info = application_info()
    app = info.get("data") or {}

    print(
        "Eagle:",
        app.get("version", "версия неизвестна")
    )

    if STATE_PATH.exists():
        state = load_json(STATE_PATH)

        if state.get("manifest_sha256") != manifest_hash:
            raise SystemExit(
                "\nМанифест изменился после начала импорта.\n"
                "Импорт остановлен, чтобы не создать "
                "дубликаты."
            )

        print(
            "Продолжаю существующий импорт:",
            len(state.get("imported_pins", {})),
            "пинов уже зарегистрировано."
        )

    else:
        state = new_state(manifest_hash)

    root_id = ensure_root_folder(
        manifest,
        state
    )

    limit = (
        args.test
        if args.test is not None
        else None
    )

    newly_imported_pins = 0
    newly_imported_media = 0
    stop = False

    for board in manifest["boards"]:
        if stop:
            break

        unimported = [
            pin for pin in board["pins"]
            if (
                f"{board['board_id']}:{pin['pin_id']}"
                not in state["imported_pins"]
            )
        ]

        if not unimported:
            continue

        folder_id = ensure_board_folder(
            board,
            root_id,
            state
        )

        for pin in unimported:
            if (
                limit is not None
                and newly_imported_pins >= limit
            ):
                stop = True
                break

            key = (
                f"{board['board_id']}:"
                f"{pin['pin_id']}"
            )

            print(
                f"[{pin['global_number']}/"
                f"{totals['pins']}] "
                f"{board['board_name']} | "
                f"Pin {pin['pin_id']} | "
                f"файлов {len(pin['components'])}"
            )

            try:
                media_count = import_pin(
                    pin,
                    folder_id
                )

            except Exception as exc:
                failure = {
                    "time": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "key": key,
                    "board_id": board["board_id"],
                    "board_name": board[
                        "board_name"
                    ],
                    "pin_id": pin["pin_id"],
                    "error": str(exc),
                }

                state["failures"].append(failure)
                save_state(state)

                print(
                    f"\n❌ Ошибка импорта {key}: {exc}"
                )
                print(
                    "Импорт остановлен. После исправления "
                    "повтори ту же команду."
                )
                return 1

            state["imported_pins"][key] = {
                "board_id": board["board_id"],
                "board_name": board[
                    "board_name"
                ],
                "pin_id": pin["pin_id"],
                "global_number": pin[
                    "global_number"
                ],
                "board_pin_number": pin[
                    "board_pin_number"
                ],
                "media_count": media_count,
                "pin_url": pin["pin_url"],
            }

            state["imported_media_count"] += (
                media_count
            )

            newly_imported_pins += 1
            newly_imported_media += media_count

            save_state(state)
            time.sleep(0.05)

    total_imported_pins = len(
        state["imported_pins"]
    )
    total_imported_media = int(
        state.get("imported_media_count", 0)
    )

    remaining = (
        totals["pins"] - total_imported_pins
    )

    print("\n========== РЕЗУЛЬТАТ ==========")
    print(
        "Добавлено сейчас пинов:",
        newly_imported_pins
    )
    print(
        "Добавлено сейчас медиа:",
        newly_imported_media
    )
    print(
        "Всего зарегистрировано пинов:",
        total_imported_pins
    )
    print(
        "Всего зарегистрировано медиа:",
        total_imported_media
    )
    print("Осталось пинов:", remaining)
    print("State:", STATE_PATH)

    if remaining == 0:
        print(
            "\n✅ ИМПОРТ PINTEREST ПОЛНОСТЬЮ "
            "ЗАВЕРШЁН"
        )
    elif args.test is not None:
        print(
            "\n✅ Тест завершён. Проверь элементы "
            "в Eagle, затем используй --all."
        )

    return 0

if __name__ == "__main__":
    sys.exit(main())
