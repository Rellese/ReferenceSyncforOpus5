"""One-off patch: folder discovery progress bar and button layout."""

from pathlib import Path

GUI = Path("app/reference_sync_gui.py")

PAIRS = [
    (
        '''        self.continue_folders_button.setEnabled(False)
        self.continue_folders_button.setVisible(False)
''',
        '''        self.continue_folders_button.setEnabled(False)
        self.continue_folders_button.setVisible(False)
        self.continue_folders_button.setFixedHeight(24)
''',
    ),
    (
        '''        self.container_tree.setVisible(False)
        self.table.setVisible(True)
        self.continue_folders_button.setVisible(False)
''',
        '''        self.container_tree.setVisible(False)
        self.table.setVisible(True)
        self.operation_progress.setVisible(False)
        self.continue_folders_button.setVisible(False)
''',
    ),
    (
        '''        self.summary.setText(f"Папки: {total}")
        self.status.setText(
            "Отметьте нужные папки и нажмите «Продолжить»"
        )
''',
        '''        self.summary.setText(f"Папки: {total}")
        self.status.setText(
            "Отметьте нужные папки и нажмите «Продолжить»"
        )

        total_items = 0

        def count_items(record: dict) -> None:
            nonlocal total_items
            metadata = record.get("metadata") or {}
            value = (
                metadata.get("media_count")
                or metadata.get("pin_count")
            )

            try:
                total_items += int(value)
            except (TypeError, ValueError):
                pass

            for child in record.get("children") or []:
                if isinstance(child, dict):
                    count_items(child)

        count_items(root)

        self.container_total_count = total
        self.container_total_items = total_items
        self.operation_progress.setVisible(True)
        self.operation_progress.setRange(0, 100)
        self.operation_progress.setValue(100)
        self.operation_progress.setFormat(
            self.container_progress_text(0)
        )
''',
    ),
    (
        '''    def container_tree_item_changed(
''',
        '''    def container_progress_text(
        self,
        selected: int,
    ) -> str:
        total = getattr(
            self,
            "container_total_count",
            0,
        )
        items = getattr(
            self,
            "container_total_items",
            0,
        )

        text = f"Найдено папок: {total}"

        if items:
            text += f" · публикаций: {items}"

        if selected:
            text += f" · выбрано: {selected}"
        else:
            text += " · отметьте нужные"

        return text

    def container_tree_item_changed(
''',
    ),
    (
        '''        self.selected_container_records = selected
        self.continue_folders_button.setEnabled(
            bool(selected)
        )
''',
        '''        self.selected_container_records = selected
        self.continue_folders_button.setEnabled(
            bool(selected)
        )

        if self._folder_search_ready:
            self.operation_progress.setFormat(
                self.container_progress_text(
                    len(selected)
                )
            )
''',
    ),
]


def main() -> int:
    text = GUI.read_text(encoding="utf-8")

    for number, (old, new) in enumerate(PAIRS, 1):
        found = text.count(old)

        if found != 1:
            print(
                f"ANCHOR {number}: found {found} times "
                "— nothing changed"
            )
            return 1

        text = text.replace(old, new)

    GUI.write_text(text, encoding="utf-8")
    print("PATCH OK")
    return 0


raise SystemExit(main())
