from __future__ import annotations

from PySide6.QtWidgets import QSplitter
from PySide6.QtWidgets import (
    QSplitterHandle,
    QSizePolicy,
    QLayout,
)
from PySide6.QtCore import QSettings

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from app.version import VERSION_LABEL, WINDOW_TITLE

from PySide6.QtCore import (
    QEvent,
    QProcess,
    QTimer,
    QRect,
    QRectF,
    Signal,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QProgressBar,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# LIGHTWEIGHT_STRUCTURE_CONTROLS_V3
# CENTERED_ROW_SELECTOR_AND_RESIZABLE_TEXT_V42
from PySide6.QtCore import QEvent as _RSQEvent
from PySide6.QtGui import QColor as _RSQColor
from PySide6.QtGui import QPen as _RSQPen
# GLOBAL_TEXT_EDITOR_COMMIT_V45
from PySide6.QtWidgets import (
    QAbstractItemDelegate as _RSQAbstractItemDelegate,
)
from PySide6.QtWidgets import (
    QApplication as _RSQApplication,
)
from PySide6.QtWidgets import QStyle as _RSQStyle
from PySide6.QtWidgets import (
    QStyleOptionButton as _RSQStyleOptionButton,
)
from PySide6.QtWidgets import (
    QStyleOptionViewItem as _RSQStyleOptionViewItem,
)


# VISIBLE_INSTAGRAM_THUMBNAILS_V51
from app.thumbnail_controller import (
    ROLE_THUMBNAIL_KEY,
    ROLE_THUMBNAIL_PIXMAP,
    ROLE_THUMBNAIL_URL,
    ROLE_THUMBNAIL_VISIBLE,
    ThumbnailController,
)


PROJECT = Path.home() / "Documents" / "ReferenceSync"
PYTHON = PROJECT / ".venv" / "bin" / "python"
REPORTS = PROJECT / "reports"


PLATFORMS = [
    ("IG", "Instagram", True),
    ("P", "Pinterest", False),
    ("Be", "Behance", False),
    ("Dr", "Dribbble", False),
    ("V", "Vimeo", False),
    ("Th", "Threads", False),
    ("X", "X", False),
    ("L", "Layers.to", False),
]


# LIGHTWEIGHT_TABLE_TEXT_EDITORS_V1
class LightweightTextItem(QTableWidgetItem):
    """
    Lightweight editable table item.

    Unlike the previous ResettableTextCell implementation, this
    object does not keep a QTextEdit and QPushButton alive for
    every table cell. A real editor is created by the delegate
    only while the user edits one particular item.
    """

    def __init__(
        self,
        on_change=None,
    ) -> None:
        super().__init__("")

        self.default_text = ""
        self.modified = False
        self.on_change = on_change
        self._internal_change = False

        self.setFlags(
            self.flags()
            | Qt.ItemFlag.ItemIsEditable
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEnabled
        )

        self.setTextAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
        )

    def set_generated_text(self, text: str) -> None:
        self.default_text = str(text or "")

        if self.modified:
            return

        self._internal_change = True

        try:
            self.setText(self.default_text)
        finally:
            self._internal_change = False

        self.modified = False
        self._refresh_visual_state()

    def reset_to_default(self) -> None:
        self._internal_change = True

        try:
            self.setText(self.default_text)
        finally:
            self._internal_change = False

        self.modified = False
        self._refresh_visual_state()

        if self.on_change is not None:
            self.on_change()

    def handle_text_changed(self) -> None:
        if self._internal_change:
            return

        self.modified = (
            self.text() != self.default_text
        )

        self._refresh_visual_state()

        if self.on_change is not None:
            self.on_change()

    def _refresh_visual_state(self) -> None:
        if self.modified:
            self.setToolTip(
                "Значение изменено вручную · "
                "нажмите «↻ сброс» в ячейке, "
                "чтобы вернуть сгенерированный текст"
            )
        else:
            self.setToolTip("")

        table = self.tableWidget()

        if table is not None:
            table.viewport().update()


# LIGHTWEIGHT_ROW_SELECTORS_V2
class LightweightRowSelectorItem(QTableWidgetItem):
    """
    Native checkable table item.

    It replaces a persistent QWidget + QHBoxLayout + QCheckBox
    combination for every result row.
    """

    def __init__(self) -> None:
        super().__init__("")

        self.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        self.setCheckState(
            Qt.CheckState.Checked
        )
        self.setTextAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.setToolTip(
            "Включить или исключить публикацию из импорта"
        )


class LightweightRowSelectorDelegate(
    QStyledItemDelegate
):
    """
    Paints checkbox and optional cached thumbnail.

    V6.4.8:
    - checkbox state changes on mouse-down;
    - dragging paints the same state across rows;
    - disabled rows remain untouched;
    - itemChanged updates counters immediately.
    """

    THUMBNAIL_SIZE = 60

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_active = False
        self._drag_checked = False
        self._drag_rows: set[int] = set()

    def _set_index_checked(
        self,
        model,
        index,
        checked: bool,
    ) -> bool:
        if not index.isValid():
            return False

        if not (
            index.flags() & Qt.ItemFlag.ItemIsEnabled
            and index.flags()
            & Qt.ItemFlag.ItemIsUserCheckable
        ):
            return False

        current = index.data(
            Qt.ItemDataRole.CheckStateRole
        )

        current_checked = (
            current == Qt.CheckState.Checked
            or current == 2
        )

        if current_checked == checked:
            return True

        return bool(
            model.setData(
                index,
                (
                    Qt.CheckState.Checked
                    if checked
                    else Qt.CheckState.Unchecked
                ),
                Qt.ItemDataRole.CheckStateRole,
            )
        )

    @staticmethod
    def checkbox_rect(
        style,
        option,
        checkbox_option,
        thumbnails_visible: bool,
    ):
        indicator = style.subElementRect(
            _RSQStyle.SubElement.SE_CheckBoxIndicator,
            checkbox_option,
            option.widget,
        )

        if thumbnails_visible:
            centre = option.rect.center()
            centre.setX(option.rect.left() + 16)
            indicator.moveCenter(centre)
        else:
            indicator.moveCenter(
                option.rect.center()
            )

        return indicator

    @staticmethod
    def thumbnail_rect(option):
        size = LightweightRowSelectorDelegate.THUMBNAIL_SIZE
        left = option.rect.left() + 40
        top = (
            option.rect.top()
            + max(0, (option.rect.height() - size) // 2)
        )
        return QRect(left, top, size, size)

    def paint(
        self,
        painter,
        option,
        index,
    ) -> None:
        style = (
            option.widget.style()
            if option.widget is not None
            else None
        )

        if style is None:
            super().paint(painter, option, index)
            return

        thumbnails_visible = bool(
            index.data(ROLE_THUMBNAIL_VISIBLE)
        )

        view_option = _RSQStyleOptionViewItem(option)
        self.initStyleOption(view_option, index)
        view_option.features = (
            view_option.features
            & ~_RSQStyleOptionViewItem
            .ViewItemFeature.HasCheckIndicator
        )
        view_option.text = ""

        style.drawControl(
            _RSQStyle.ControlElement.CE_ItemViewItem,
            view_option,
            painter,
            option.widget,
        )

        checkbox_option = _RSQStyleOptionButton()
        checkbox_option.rect = self.checkbox_rect(
            style,
            option,
            checkbox_option,
            thumbnails_visible,
        )

        if index.flags() & Qt.ItemFlag.ItemIsEnabled:
            checkbox_option.state |= (
                _RSQStyle.StateFlag.State_Enabled
            )

        state = index.data(
            Qt.ItemDataRole.CheckStateRole
        )

        if state == Qt.CheckState.Checked or state == 2:
            checkbox_option.state |= (
                _RSQStyle.StateFlag.State_On
            )
        elif (
            state == Qt.CheckState.PartiallyChecked
            or state == 1
        ):
            checkbox_option.state |= (
                _RSQStyle.StateFlag.State_NoChange
            )
        else:
            checkbox_option.state |= (
                _RSQStyle.StateFlag.State_Off
            )

        style.drawPrimitive(
            _RSQStyle.PrimitiveElement.PE_IndicatorCheckBox,
            checkbox_option,
            painter,
            option.widget,
        )

        if not thumbnails_visible:
            return

        target = self.thumbnail_rect(option)
        pixmap = index.data(ROLE_THUMBNAIL_PIXMAP)

        painter.save()
        painter.setPen(_RSQPen(_RSQColor("#3a414d"), 1))
        painter.setBrush(_RSQColor("#20242c"))
        painter.drawRoundedRect(target, 6, 6)

        if (
            hasattr(pixmap, "isNull")
            and not pixmap.isNull()
        ):
            x = (
                target.left()
                + (target.width() - pixmap.width()) // 2
            )
            y = (
                target.top()
                + (target.height() - pixmap.height()) // 2
            )
            painter.drawPixmap(x, y, pixmap)

        painter.restore()

    def editorEvent(
        self,
        event,
        model,
        option,
        index,
    ) -> bool:
        if not (
            index.flags() & Qt.ItemFlag.ItemIsEnabled
            and index.flags()
            & Qt.ItemFlag.ItemIsUserCheckable
        ):
            return False

        event_type = event.type()

        if (
            event_type
            == _RSQEvent.Type.MouseButtonPress
            and hasattr(event, "position")
            and hasattr(event, "button")
            and event.button() == Qt.MouseButton.LeftButton
        ):
            style = option.widget.style()
            checkbox_option = _RSQStyleOptionButton()
            indicator = self.checkbox_rect(
                style,
                option,
                checkbox_option,
                bool(index.data(ROLE_THUMBNAIL_VISIBLE)),
            )

            if not indicator.adjusted(
                -8, -8, 8, 8
            ).contains(event.position().toPoint()):
                return False

            current = index.data(
                Qt.ItemDataRole.CheckStateRole
            )
            current_checked = (
                current == Qt.CheckState.Checked
                or current == 2
            )

            self._drag_active = True
            self._drag_checked = not current_checked
            self._drag_rows = {index.row()}

            return self._set_index_checked(
                model,
                index,
                self._drag_checked,
            )

        if (
            event_type == _RSQEvent.Type.MouseMove
            and self._drag_active
            and hasattr(event, "buttons")
            and (
                event.buttons()
                & Qt.MouseButton.LeftButton
            )
        ):
            if index.row() in self._drag_rows:
                return True

            self._drag_rows.add(index.row())

            return self._set_index_checked(
                model,
                index,
                self._drag_checked,
            )

        if (
            event_type
            == _RSQEvent.Type.MouseButtonRelease
            and self._drag_active
        ):
            self._drag_active = False
            self._drag_rows.clear()
            return True

        if (
            event_type == _RSQEvent.Type.KeyPress
            and hasattr(event, "key")
            and event.key()
            in (
                Qt.Key.Key_Space,
                Qt.Key.Key_Select,
            )
        ):
            current = index.data(
                Qt.ItemDataRole.CheckStateRole
            )
            checked = (
                current == Qt.CheckState.Checked
                or current == 2
            )

            return self._set_index_checked(
                model,
                index,
                not checked,
            )

        return False




class LightweightStructureDelegate(
    QStyledItemDelegate
):
    """
    Draws carousel controls without creating one QPushButton
    per table row.

    Ordinary single-publication cells continue to use Qt's
    standard item painting.
    """

    ROLE_CAROUSEL = (
        int(Qt.ItemDataRole.UserRole) + 31
    )
    ROLE_CONTROL_ENABLED = (
        int(Qt.ItemDataRole.UserRole) + 32
    )

    def __init__(
        self,
        activate_callback,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.activate_callback = activate_callback

    @staticmethod
    def control_rect(option):
        return option.rect.adjusted(5, 4, -5, -4)

    def paint(
        self,
        painter,
        option,
        index,
    ) -> None:
        is_carousel = bool(
            index.data(self.ROLE_CAROUSEL)
        )

        if not is_carousel:
            super().paint(
                painter,
                option,
                index,
            )
            return

        enabled = bool(
            index.data(self.ROLE_CONTROL_ENABLED)
        ) and bool(
            index.flags()
            & Qt.ItemFlag.ItemIsEnabled
        )

        rect = self.control_rect(option)
        text = str(
            index.data(
                Qt.ItemDataRole.DisplayRole
            )
            or ""
        )

        painter.save()

        if enabled:
            background = _RSQColor("#292e38")
            border = _RSQColor("#454c5a")
            foreground = _RSQColor("#e6e8ed")
        else:
            background = _RSQColor("#1d2026")
            border = _RSQColor("#2b3039")
            foreground = _RSQColor("#666d79")

        painter.setBrush(background)
        painter.setPen(
            _RSQPen(border, 1)
        )
        painter.drawRoundedRect(
            rect,
            7,
            7,
        )

        text_rect = rect.adjusted(
            9,
            2,
            -9,
            -2,
        )

        elided = option.fontMetrics.elidedText(
            text,
            Qt.TextElideMode.ElideRight,
            max(0, text_rect.width()),
        )

        painter.setPen(foreground)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignCenter,
            elided,
        )

        painter.restore()

    def editorEvent(
        self,
        event,
        model,
        option,
        index,
    ) -> bool:
        if not bool(
            index.data(self.ROLE_CAROUSEL)
        ):
            return super().editorEvent(
                event,
                model,
                option,
                index,
            )

        if not bool(
            index.data(self.ROLE_CONTROL_ENABLED)
        ):
            return False

        if not (
            index.flags()
            & Qt.ItemFlag.ItemIsEnabled
        ):
            return False

        if (
            event.type()
            == _RSQEvent.Type.MouseButtonRelease
            and hasattr(event, "position")
            and self.control_rect(option).contains(
                event.position().toPoint()
            )
        ):
            self.activate_callback(index.row())
            return True

        return False


class LightweightTextDelegate(QStyledItemDelegate):
    """
    Creates one temporary QTextEdit only during active editing.

    The reset control is painted directly inside modified cells,
    so it does not require one QPushButton per table row.
    """

    RESET_WIDTH = 58
    RESET_HEIGHT = 20

    def __init__(
        self,
        parent=None,
        commit_callback=None,
    ) -> None:
        super().__init__(parent)
        self._active_text_editor = None
        self._global_filter_installed = False
        self._commit_callback = commit_callback

    def _install_global_editor_filter(
        self,
        editor,
    ) -> None:
        self._remove_global_editor_filter()

        self._active_text_editor = editor
        app = _RSQApplication.instance()

        if app is not None:
            app.installEventFilter(self)
            self._global_filter_installed = True

        editor.destroyed.connect(
            self._active_editor_destroyed
        )

    def _remove_global_editor_filter(
        self,
    ) -> None:
        if self._global_filter_installed:
            app = _RSQApplication.instance()

            if app is not None:
                app.removeEventFilter(self)

        self._global_filter_installed = False
        self._active_text_editor = None

    def _active_editor_destroyed(
        self,
        *_args,
    ) -> None:
        self._remove_global_editor_filter()

    def _commit_active_editor(
        self,
    ) -> None:
        editor = self._active_text_editor

        if editor is None:
            return

        # Remove the filter before emitting signals to prevent
        # recursive processing while Qt closes the editor.
        self._remove_global_editor_filter()

        self.commitData.emit(editor)
        self.closeEditor.emit(
            editor,
            _RSQAbstractItemDelegate
            .EndEditHint.NoHint,
        )

    def eventFilter(
        self,
        watched,
        event,
    ) -> bool:
        editor = self._active_text_editor

        if editor is None:
            return super().eventFilter(
                watched,
                event,
            )

        is_editor_or_child = (
            watched is editor
            or (
                isinstance(watched, QWidget)
                and editor.isAncestorOf(watched)
            )
        )

        # EDITOR_COMMIT_ESCAPE_UNDO_REDO_V46
        if (
            is_editor_or_child
            and event.type()
            == _RSQEvent.Type.KeyPress
            and hasattr(event, "key")
        ):
            key = event.key()
            modifiers = event.modifiers()

            if key == Qt.Key.Key_Escape:
                # Unlike the default delegate behaviour, Escape
                # confirms the current value instead of reverting
                # the edit session.
                self._commit_active_editor()
                return True

            command_modifier = bool(
                modifiers
                & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.MetaModifier
                )
            )

            if (
                command_modifier
                and key == Qt.Key.Key_Z
            ):
                if (
                    modifiers
                    & Qt.KeyboardModifier.ShiftModifier
                ):
                    editor.redo()
                else:
                    editor.undo()

                return True

        if (
            not is_editor_or_child
            and event.type()
            == _RSQEvent.Type.MouseButtonPress
        ):
            self._commit_active_editor()

        return super().eventFilter(
            watched,
            event,
        )

    def createEditor(
        self,
        parent,
        option,
        index,
    ):
        editor = QTextEdit(parent)
        editor.setAcceptRichText(False)
        editor.setPlaceholderText("—")
        editor.setFrameShape(
            QFrame.Shape.NoFrame
        )

        self._install_global_editor_filter(
            editor
        )

        return editor

    def setEditorData(
        self,
        editor,
        index,
    ) -> None:
        if isinstance(editor, QTextEdit):
            editor.setPlainText(
                str(
                    index.data(
                        Qt.ItemDataRole.EditRole
                    )
                    or ""
                )
            )
            editor.selectAll()
            return

        super().setEditorData(
            editor,
            index,
        )

    def setModelData(
        self,
        editor,
        model,
        index,
    ) -> None:
        if isinstance(editor, QTextEdit):
            old_text = str(
                index.data(
                    Qt.ItemDataRole.EditRole
                )
                or ""
            )
            new_text = editor.toPlainText()

            table = self.parent()
            item = None

            if (
                table is not None
                and hasattr(table, "item")
            ):
                item = table.item(
                    index.row(),
                    index.column(),
                )

            model.setData(
                index,
                new_text,
                Qt.ItemDataRole.EditRole,
            )

            if (
                old_text != new_text
                and self._commit_callback is not None
            ):
                self._commit_callback(
                    item,
                    index.row(),
                    index.column(),
                    old_text,
                    new_text,
                )

            return

        super().setModelData(
            editor,
            model,
            index,
        )

    def updateEditorGeometry(
        self,
        editor,
        option,
        index,
    ) -> None:
        editor.setGeometry(option.rect)

    def sizeHint(
        self,
        option,
        index,
    ) -> QSize:
        base = super().sizeHint(
            option,
            index,
        )

        text = str(
            index.data(
                Qt.ItemDataRole.DisplayRole
            )
            or ""
        )

        line_count = max(
            1,
            text.count("\n") + 1,
        )

        height = min(
            190,
            max(
                48,
                line_count * 18 + 14,
            ),
        )

        return QSize(
            base.width(),
            height,
        )

    def reset_rect(self, option) -> QRect:
        return QRect(
            option.rect.right()
            - self.RESET_WIDTH
            - 5,
            option.rect.top() + 4,
            self.RESET_WIDTH,
            self.RESET_HEIGHT,
        )

    def paint(
        self,
        painter,
        option,
        index,
    ) -> None:
        super().paint(
            painter,
            option,
            index,
        )

        table = self.parent()

        if not isinstance(table, QTableWidget):
            return

        item = table.item(
            index.row(),
            index.column(),
        )

        if (
            not isinstance(item, LightweightTextItem)
            or not item.modified
        ):
            return

        rect = self.reset_rect(option)

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(
            QColor(20, 22, 28, 215)
        )
        painter.drawRoundedRect(
            rect,
            6,
            6,
        )

        painter.setPen(
            QColor("#ff985c")
        )
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignCenter,
            "↻  сброс",
        )
        painter.restore()

    def editorEvent(
        self,
        event,
        model,
        option,
        index,
    ) -> bool:
        if (
            event.type()
            == QEvent.Type.MouseButtonRelease
        ):
            table = self.parent()

            if isinstance(table, QTableWidget):
                item = table.item(
                    index.row(),
                    index.column(),
                )

                if (
                    isinstance(
                        item,
                        LightweightTextItem,
                    )
                    and item.modified
                    and self.reset_rect(
                        option
                    ).contains(
                        event.position().toPoint()
                    )
                ):
                    item.reset_to_default()
                    table.setRowHeight(
                        index.row(),
                        76,
                    )
                    return True

        return super().editorEvent(
            event,
            model,
            option,
            index,
        )


class CarouselSelectionDialog(QDialog):
    """Select individual components of one carousel."""

    IMAGE_EXTENSIONS = {
        "jpg",
        "jpeg",
        "png",
        "webp",
        "gif",
        "avif",
        "heic",
    }

    VIDEO_EXTENSIONS = {
        "mp4",
        "mov",
        "webm",
        "mkv",
        "m4v",
    }

    def __init__(
        self,
        item: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.item = item
        self.setWindowTitle("Элементы карусели")
        self.setMinimumWidth(470)
        self.resize(520, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        username = str(
            item.get("username") or "@unknown"
        )

        title = QLabel(
            f"{username} · выберите нужные файлы"
        )
        title.setWordWrap(True)
        layout.addWidget(title)

        note = QLabel(
            "Миниатюры подключим после проверки "
            "выборочного импорта. Сейчас показаны "
            "номер и тип каждого компонента."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8d96a5;")
        layout.addWidget(note)

        controls = QHBoxLayout()
        controls.setSpacing(6)

        select_all = QPushButton("Выбрать всё")
        clear_all = QPushButton("Снять всё")
        images_only = QPushButton("Только изображения")
        videos_only = QPushButton("Только видео")

        controls.addWidget(select_all)
        controls.addWidget(clear_all)
        controls.addWidget(images_only)
        controls.addWidget(videos_only)

        layout.addLayout(controls)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        contents = QWidget()
        component_layout = QVBoxLayout(contents)
        component_layout.setContentsMargins(8, 8, 8, 8)
        component_layout.setSpacing(7)

        self.component_checks: list[
            tuple[QCheckBox, dict]
        ] = []

        components = item.get("component_items")

        if not isinstance(components, list):
            components = []

        component_count = int(
            item.get("component_count") or 1
        )

        if not components:
            components = [
                {
                    "component_index": index,
                    "media_id": None,
                    "extension": None,
                    "media_type": "unknown",
                }
                for index in range(
                    1,
                    component_count + 1,
                )
            ]

        imported_set = {
            int(value)
            for value in item.get(
                "imported_component_numbers",
                [],
            )
            if str(value).isdigit()
        }

        selected_components = item.get(
            "selected_components"
        )

        if not isinstance(selected_components, list):
            available_values = item.get(
                "available_component_numbers"
            )

            if isinstance(available_values, list):
                selected_components = [
                    int(value)
                    for value in available_values
                    if str(value).isdigit()
                ]
            else:
                selected_components = [
                    int(
                        component.get(
                            "component_index",
                            index,
                        )
                    )
                    for index, component in enumerate(
                        components,
                        start=1,
                    )
                    if int(
                        component.get(
                            "component_index",
                            index,
                        )
                    ) not in imported_set
                ]

        selected_set = {
            int(value)
            for value in selected_components
            if int(value) not in imported_set
        }

        for fallback_index, component in enumerate(
            components,
            start=1,
        ):
            try:
                component_index = int(
                    component.get("component_index")
                    or fallback_index
                )
            except (TypeError, ValueError):
                component_index = fallback_index

            extension = str(
                component.get("extension") or ""
            ).lower().lstrip(".")

            media_type = str(
                component.get("media_type") or "unknown"
            ).lower()

            if (
                media_type == "image"
                or extension in self.IMAGE_EXTENSIONS
            ):
                type_label = "Изображение"
            elif (
                media_type == "video"
                or extension in self.VIDEO_EXTENSIONS
            ):
                type_label = "Видео"
            else:
                type_label = "Медиафайл"

            extension_label = (
                f" · {extension.upper()}"
                if extension
                else ""
            )

            already_imported = (
                component_index in imported_set
            )

            imported_label = (
                " · уже в Eagle"
                if already_imported
                else ""
            )

            checkbox = QCheckBox(
                f"{component_index}. "
                f"{type_label}{extension_label}"
                f"{imported_label}"
            )
            checkbox.setChecked(
                component_index in selected_set
                and not already_imported
            )
            checkbox.setEnabled(
                not already_imported
            )

            normalized_component = dict(component)
            normalized_component[
                "component_index"
            ] = component_index

            self.component_checks.append(
                (checkbox, normalized_component)
            )
            component_layout.addWidget(checkbox)

        component_layout.addStretch()
        scroll.setWidget(contents)
        layout.addWidget(scroll, 1)

        self.summary = QLabel()
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        select_all.clicked.connect(
            lambda: self.set_filter("all")
        )
        clear_all.clicked.connect(
            lambda: self.set_filter("none")
        )
        images_only.clicked.connect(
            lambda: self.set_filter("image")
        )
        videos_only.clicked.connect(
            lambda: self.set_filter("video")
        )

        for checkbox, _component in (
            self.component_checks
        ):
            checkbox.stateChanged.connect(
                self.update_summary
            )

        self.update_summary()

    def component_kind(
        self,
        component: dict,
    ) -> str:
        extension = str(
            component.get("extension") or ""
        ).lower().lstrip(".")

        media_type = str(
            component.get("media_type") or ""
        ).lower()

        if (
            media_type == "image"
            or extension in self.IMAGE_EXTENSIONS
        ):
            return "image"

        if (
            media_type == "video"
            or extension in self.VIDEO_EXTENSIONS
        ):
            return "video"

        return "unknown"

    def set_filter(self, mode: str) -> None:
        for checkbox, component in (
            self.component_checks
        ):
            if not checkbox.isEnabled():
                continue

            if mode == "all":
                checked = True
            elif mode == "none":
                checked = False
            else:
                checked = (
                    self.component_kind(component) == mode
                )

            checkbox.setChecked(checked)

        self.update_summary()

    def selected_components(self) -> list[int]:
        result = []

        for checkbox, component in (
            self.component_checks
        ):
            if checkbox.isChecked():
                result.append(
                    int(component["component_index"])
                )

        return sorted(set(result))

    def update_summary(self, *_args) -> None:
        selected = len(self.selected_components())
        total = len(self.component_checks)

        self.summary.setText(
            f"Выбрано файлов: {selected} из {total}"
        )

    def accept_selection(self) -> None:
        if not self.selected_components():
            QMessageBox.warning(
                self,
                "Ничего не выбрано",
                "Выберите хотя бы один компонент карусели.",
            )
            return

        self.accept()


# ADAPTIVE_WORKSPACE_V56
class ResettableSplitterHandle(QSplitterHandle):
    """Splitter handle reset by a double click."""

    def mouseDoubleClickEvent(
        self,
        event,
    ) -> None:
        owner = self.splitter()

        if isinstance(owner, ResettableSplitter):
            owner.reset_to_default()
            event.accept()
            return

        super().mouseDoubleClickEvent(event)


class ResettableSplitter(QSplitter):
    """QSplitter with application-defined default sizes."""

    def __init__(
        self,
        orientation,
        parent=None,
    ) -> None:
        super().__init__(orientation, parent)
        self._default_sizes = []

    def createHandle(
        self,
    ) -> QSplitterHandle:
        return ResettableSplitterHandle(
            self.orientation(),
            self,
        )

    def set_default_sizes(
        self,
        sizes,
    ) -> None:
        self._default_sizes = [
            max(1, int(value))
            for value in sizes
        ]
        self.setSizes(self._default_sizes)

    def reset_to_default(
        self,
    ) -> None:
        if not self._default_sizes:
            return

        self.setSizes(self._default_sizes)



class ToggleSwitch(QWidget):
    """Compact ON/OFF switch with a movable circular handle."""

    toggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._checked = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(42, 24)
        self.setToolTip("Включить поиск в выбранных папках")

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)

        if checked == self._checked:
            return

        self._checked = checked
        self.update()
        self.toggled.emit(self._checked)

    def mouseReleaseEvent(self, event) -> None:
        if (
            self.isEnabled()
            and event.button()
            == Qt.MouseButton.LeftButton
        ):
            self.setChecked(not self._checked)
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in {
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        }:
            self.setChecked(not self._checked)
            event.accept()
            return

        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        if not self.isEnabled():
            track = QColor("#343943")
            handle = QColor("#777d88")
        elif self._checked:
            track = QColor("#7569e8")
            handle = QColor("#ffffff")
        else:
            track = QColor("#3a3f4a")
            handle = QColor("#d8dbe2")

        painter.setPen(
            QPen(
                QColor("#8d83ee")
                if self._checked
                else QColor("#555b67"),
                1,
            )
        )
        painter.setBrush(track)
        painter.drawRoundedRect(
            QRectF(0.5, 0.5, 41.0, 23.0),
            11.5,
            11.5,
        )

        handle_x = 20.0 if self._checked else 3.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(handle)
        painter.drawEllipse(
            QRectF(handle_x, 3.0, 18.0, 18.0)
        )
