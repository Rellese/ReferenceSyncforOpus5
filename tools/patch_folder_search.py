"""One-off patch: wire folder selection through sync and GUI."""

from pathlib import Path

DISCOVER = Path("app/instagram_discover.py")
SYNC = Path("app/instagram_sync.py")
GUI = Path("app/reference_sync_gui.py")

PATCHES = {
    DISCOVER: [
        (
            '''        collection_targets.append((
            collection_id,
            raw_name.strip() or collection_id,
            (
                f"https://www.instagram.com/"
                f"{username}/saved/collection/"
                f"{collection_id}/"
            ),
        ))
''',
            '''        collection_targets.append((
            collection_id,
            raw_name.strip() or collection_id,
            (
                saved_url
                if collection_id
                == "ALL_MEDIA_AUTO_COLLECTION"
                else (
                    f"https://www.instagram.com/"
                    f"{username}/saved/collection/"
                    f"{collection_id}/"
                )
            ),
        ))
''',
        ),
    ],
    SYNC: [
        (
            '''    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
''',
            '''    parser.add_argument(
        "--collection",
        action="append",
        default=[],
        metavar="ID:NAME",
        help=(
            "Limit discovery to a saved collection. "
            "Can be repeated."
        ),
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
''',
        ),
        (
            '''            discovery = run_module(
                "app.instagram_discover",
                [
                    "--username",
                    args.username,
                    "--browser",
                    args.browser,
                    "--scan-speed",
                    args.speed_profile,
                    "--search-mode",
                    args.search_mode,
                    "--limit",
                    str(args.limit),
                ],
                log_lines,
            )
''',
            '''            discovery_arguments = [
                "--username",
                args.username,
                "--browser",
                args.browser,
                "--scan-speed",
                args.speed_profile,
                "--search-mode",
                args.search_mode,
                "--limit",
                str(args.limit),
            ]

            for entry in args.collection or []:
                discovery_arguments.extend([
                    "--collection",
                    str(entry),
                ])

            discovery = run_module(
                "app.instagram_discover",
                discovery_arguments,
                log_lines,
            )
''',
        ),
        (
            '''            next_command = (
                "python -m app.instagram_sync "
''',
            '''            collection_argument = "".join(
                f'--collection "{entry}" '
                for entry in (args.collection or [])
            )

            next_command = (
                "python -m app.instagram_sync "
''',
        ),
        (
            '''                f"--limit {args.limit} "
''',
            '''                f"--limit {args.limit} "
                f"{collection_argument}"
''',
        ),
    ],
    GUI: [
        (
            '''        self.selected_container_records = []
        self._folder_search_ready = False
''',
            '''        self.selected_container_records = []
        self._folder_search_ready = False
        self._folder_search_confirmed = False
''',
        ),
        (
            '''        if self.selected_folders_toggle.isChecked():
            if not self._folder_search_ready:
                self.start_container_preview()
                return

            self.status.setText(
                "Выберите папки и нажмите «Продолжить»"
            )
            return
''',
            '''        if self.selected_folders_toggle.isChecked():
            if not self._folder_search_ready:
                self.start_container_preview()
                return

            if not self._folder_search_confirmed:
                self.status.setText(
                    "Выберите папки и нажмите «Продолжить»"
                )
                return

            if self.active_source == "pinterest":
                self._folder_search_confirmed = False
                self.status.setText(
                    "Поиск по доскам Pinterest "
                    "пока не подключён"
                )
                return
''',
        ),
        (
            '''            "50",
            "--continue-numbering",
        ]
''',
            '''            "50",
            "--continue-numbering",
        ]

        if self._folder_search_confirmed:
            for record in self.selected_container_records:
                container_id = str(
                    record.get("id") or ""
                ).strip()

                if not container_id:
                    continue

                container_name = str(
                    record.get("name") or container_id
                ).replace(":", " ")

                arguments.extend([
                    "--collection",
                    f"{container_id}:{container_name}",
                ])

        self._folder_search_confirmed = False
''',
        ),
        (
            r'''        self.status.setText(
            "Папки выбраны — подготовка поиска публикаций"
        )
        self.summary.setText(
            f"Выбрано папок: {len(selected_names)}"
        )
        self.log.setPlainText(
            "Выбранные папки:\n"
            + "\n".join(
                f"• {name}"
                for name in selected_names
            )
            + "\n\n"
            + "Поиск публикаций не запущен: "
            + "ожидается подключение folder-aware backend."
        )

        QMessageBox.information(
            self,
            "Папки выбраны",
            "Выбор сохранён. На этом безопасном этапе "
            "публикации ещё не загружаются.",
        )
''',
            r'''        self.log.setPlainText(
            "Выбранные папки:\n"
            + "\n".join(
                f"• {name}"
                for name in selected_names
            )
        )
        self.summary.setText(
            f"Выбрано папок: {len(selected_names)}"
        )
        self.container_tree.setVisible(False)
        self.continue_folders_button.setVisible(False)
        self.continue_folders_button.setEnabled(False)
        self.table.setVisible(True)
        self._folder_search_confirmed = True
        self.start_preview()
''',
        ),
    ],
}


def main() -> int:
    updates = {}

    for path, pairs in PATCHES.items():
        text = path.read_text(encoding="utf-8")

        for number, (old, new) in enumerate(pairs, 1):
            found = text.count(old)

            if found != 1:
                print(
                    f"{path.name} ANCHOR {number}: "
                    f"found {found} times — nothing changed"
                )
                return 1

            text = text.replace(old, new)

        updates[path] = text

    for path, text in updates.items():
        path.write_text(text, encoding="utf-8")
        print(f"PATCHED {path.name}")

    return 0


raise SystemExit(main())
