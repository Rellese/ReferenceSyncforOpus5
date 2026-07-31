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



from app.ui.widgets import (
    LightweightTextItem,
    LightweightRowSelectorItem,
    LightweightRowSelectorDelegate,
    LightweightStructureDelegate,
    LightweightTextDelegate,
    CarouselSelectionDialog,
    ResettableSplitterHandle,
    ResettableSplitter,
    ToggleSwitch,
)



class ReferenceSyncWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.process: QProcess | None = None
        self.process_output = ""
        self._process_line_buffer = ""
        self.process_started_at = 0.0
        self.preview_items: list[dict] = []
        self._operation_last_percent = 0
        self.active_source = "instagram"
        self.active_operation_source = None
        self.pinterest_preview_output = None
        self.container_preview_output = None
        self.container_payload = None
        self.selected_container_records = []
        self._folder_search_ready = False
        self._folder_search_confirmed = False
        self._updating_container_tree = False

        # V6.4.8 STAGE6.2 CLEAN_EXIT_SESSION_CLEANUP
        #
        # This snapshot distinguishes jobs touched by the current
        # GUI session from jobs left by an earlier crash. Crash jobs
        # are intentionally preserved until the user resumes them.
        self._clean_close_requested = False
        self._clean_close_completed = False
        self._clean_close_wait_connected = False
        self._clean_close_runtime_errors = []
        self._session_staging_baseline = (
            self.snapshot_session_staging_jobs()
        )

        self.control_file = (
            PROJECT
            / "data"
            / "runtime"
            / "instagram_control.json"
        )
        self.control_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.settings = QSettings(
            "ReferenceSync",
            "ReferenceSync",
        )

        self.setWindowTitle(
            WINDOW_TITLE
        )
        # ALPHA02_DEFAULT_WINDOW_V55
        self.resize(1600, 1000)
        self.setMinimumSize(1180, 740)

        self.build_interface()
        self.apply_style()
        self.update_source_interface()
        self.update_search_interface()
        self.update_numbering_interface()
        self.load_settings()
        self.connect_settings_persistence()
        self.update_numbering_interface()

    # -----------------------------------------------------
    # Interface
    # -----------------------------------------------------

    def build_interface(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        main = QVBoxLayout(root)
        main.setContentsMargins(24, 20, 24, 20)
        main.setSpacing(14)

        header = QHBoxLayout()

        title_box = QVBoxLayout()

        title = QLabel("ReferenceSync")
        title.setObjectName("title")
        title.setFont(
            QFont("Arial", 25, QFont.Weight.Bold)
        )

        subtitle = QLabel(
            "Сохраняйте референсы из социальных сетей в Eagle"
        )
        subtitle.setObjectName("muted")

        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        header.addLayout(title_box)
        header.addStretch()

        alpha = QLabel(VERSION_LABEL)
        alpha.setObjectName("alphaBadge")
        header.addWidget(alpha)

        main.addLayout(header)

        # Platform navigation
        platform_card = self.make_card()
        platform_layout = QHBoxLayout(platform_card)
        platform_layout.setContentsMargins(14, 12, 14, 12)
        platform_layout.setSpacing(9)

        section_label = QLabel("Источник:")
        section_label.setObjectName("smallTitle")
        platform_layout.addWidget(section_label)

        self.platform_buttons = []
        self.platform_button_by_source = {}

        for short_name, full_name, active in PLATFORMS:
            button = QPushButton(short_name)
            button.setToolTip(full_name)
            button.setProperty(
                "platformActive",
                active,
            )
            button.setObjectName(
                "activePlatform"
                if active
                else "futurePlatform"
            )
            button.setFixedSize(48, 43)

            source_code = full_name.lower()

            if source_code in {
                "instagram",
                "pinterest",
            }:
                self.platform_button_by_source[
                    source_code
                ] = button
                button.clicked.connect(
                    lambda checked=False, code=source_code:
                    self.switch_platform(code)
                )
            else:
                button.clicked.connect(
                    lambda checked=False, name=full_name:
                    self.show_future_platform(name)
                )

            self.platform_buttons.append(button)
            platform_layout.addWidget(button)

        platform_layout.addStretch()

        self.active_platform_label = QLabel(
            "Instagram"
        )
        self.active_platform_label.setObjectName(
            "activePlatformLabel"
        )
        platform_layout.addWidget(
            self.active_platform_label
        )

        main.addWidget(platform_card)

        # RESIZABLE_WORKSPACE_ALPHA02_V55
        # AE-style adjustable workspace:
        # left search panel | right results workspace.
        self.workspace_splitter = ResettableSplitter(
            Qt.Orientation.Horizontal
        )
        self.workspace_splitter.setObjectName(
            "workspaceSplitter"
        )
        self.workspace_splitter.setChildrenCollapsible(
            False
        )
        self.workspace_splitter.setHandleWidth(3)
        main.addWidget(self.workspace_splitter, 1)

        # Left controls
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(
            QFrame.Shape.NoFrame
        )
        # ADAPTIVE_LEFT_PANEL_V56
        left_scroll.setMinimumWidth(320)
        left_scroll.setMaximumWidth(540)
        left_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        left_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        left_container = QWidget()
        left_container.setMinimumWidth(0)
        left_container.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

        left = QVBoxLayout(left_container)
        left.setSizeConstraint(
            QLayout.SizeConstraint.SetNoConstraint
        )
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(14)

        left_scroll.setWidget(left_container)
        self.workspace_splitter.addWidget(
            left_scroll
        )

        # Instagram source
        source_card = self.make_card()
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(20, 18, 20, 18)
        source_layout.setSpacing(12)

        self.source_title = QLabel("1. Откуда получить данные")
        self.source_title.setObjectName("sectionTitle")
        source_layout.addWidget(self.source_title)

        self.browser_source = QRadioButton(
            "Через авторизованный браузер"
        )
        self.meta_source = QRadioButton(
            "Из архива Meta"
        )
        self.browser_source.setChecked(True)

        self.source_group = QButtonGroup(self)
        self.source_group.addButton(
            self.browser_source
        )
        self.source_group.addButton(
            self.meta_source
        )

        source_layout.addWidget(self.browser_source)
        source_layout.addWidget(self.meta_source)

        self.browser_source.toggled.connect(
            self.update_source_interface
        )
        self.meta_source.toggled.connect(
            self.update_source_interface
        )

        # Browser settings
        self.browser_panel = QFrame()
        browser_panel_layout = QVBoxLayout(
            self.browser_panel
        )
        browser_panel_layout.setContentsMargins(
            0, 8, 0, 0
        )
        browser_panel_layout.setSpacing(10)

        browser_panel_layout.addWidget(
            QLabel("Instagram-аккаунт")
        )

        handle_frame = QFrame()
        handle_frame.setObjectName("handleField")
        handle_layout = QHBoxLayout(handle_frame)
        handle_layout.setContentsMargins(10, 0, 4, 0)
        handle_layout.setSpacing(2)

        handle_prefix = QLabel("@")
        handle_prefix.setObjectName("handlePrefix")
        handle_prefix.setFixedWidth(18)

        self.username = QLineEdit("rellese26")
        self.username.setObjectName("handleInput")
        self.username.setPlaceholderText(
            "имя пользователя"
        )

        handle_layout.addWidget(handle_prefix)
        handle_layout.addWidget(self.username)

        browser_panel_layout.addWidget(handle_frame)
        browser_panel_layout.addWidget(
            QLabel("Браузер с выполненным входом")
        )

        self.browser = QComboBox()

        # V6.4.2: prevent accidental browser changes while
        # scrolling the left settings panel.
        self.browser.installEventFilter(self)
        self.browser.addItem(
            "Google Chrome",
            "chrome",
        )
        self.browser.addItem(
            "Яндекс.Браузер",
            "yandex",
        )
        self.browser.addItem(
            "Safari",
            "safari",
        )
        self.browser.addItem(
            "Firefox",
            "firefox",
        )

        browser_panel_layout.addWidget(self.browser)

        # DOWNLOAD_SPEED_AND_VPN_SAFETY_V1
        browser_panel_layout.addWidget(
            QLabel("Скорость загрузки")
        )

        self.download_speed = QComboBox()
        self.download_speed.addItem(
            "Безопасная — медленнее, меньше риск блокировки",
            "safe",
        )
        self.download_speed.addItem(
            "Сбалансированная — немного быстрее",
            "balanced",
        )
        self.download_speed.setToolTip(
            "Безопасный режим рекомендуется для больших "
            "очередей. Сбалансированный режим сокращает "
            "паузы, но не отключает защиту Instagram."
        )
        browser_panel_layout.addWidget(
            self.download_speed
        )

        browser_hint = QLabel(
            "ReferenceSync использует существующий вход "
            "в Instagram и не запрашивает пароль."
        )
        browser_hint.setObjectName("hint")
        browser_hint.setWordWrap(True)
        browser_panel_layout.addWidget(browser_hint)

        source_layout.addWidget(self.browser_panel)

        # Meta archive settings
        self.meta_panel = QFrame()
        meta_layout = QVBoxLayout(self.meta_panel)
        meta_layout.setContentsMargins(0, 8, 0, 0)
        meta_layout.setSpacing(9)

        meta_layout.addWidget(
            QLabel("Архив Instagram от Meta")
        )

        meta_file_row = QHBoxLayout()

        self.meta_path = QLineEdit()
        self.meta_path.setReadOnly(True)
        self.meta_path.setPlaceholderText(
            "Выберите ZIP, JSON или HTML"
        )

        choose_meta = QPushButton("Выбрать…")
        choose_meta.clicked.connect(
            self.choose_meta_archive
        )

        meta_file_row.addWidget(self.meta_path, 1)
        meta_file_row.addWidget(choose_meta)

        meta_layout.addLayout(meta_file_row)

        meta_hint = QLabel(
            "Экспорт создаётся в Accounts Center → "
            "Ваша информация → Экспорт информации."
        )
        meta_hint.setObjectName("hint")
        meta_hint.setWordWrap(True)
        meta_layout.addWidget(meta_hint)

        source_layout.addWidget(self.meta_panel)

        self.pinterest_panel = QFrame()
        pinterest_layout = QVBoxLayout(
            self.pinterest_panel
        )
        pinterest_layout.setContentsMargins(
            0, 8, 0, 0
        )
        pinterest_layout.setSpacing(10)

        pinterest_layout.addWidget(
            QLabel("Pinterest-аккаунт")
        )

        pinterest_handle_frame = QFrame()
        pinterest_handle_frame.setObjectName("handleField")
        pinterest_handle_layout = QHBoxLayout(
            pinterest_handle_frame
        )
        pinterest_handle_layout.setContentsMargins(
            10, 0, 10, 0
        )
        pinterest_handle_layout.setSpacing(4)

        pinterest_at = QLabel("@")
        pinterest_at.setObjectName("atPrefix")

        self.pinterest_username = QLineEdit()
        self.pinterest_username.setFrame(False)
        self.pinterest_username.setPlaceholderText(
            "имя пользователя Pinterest"
        )

        pinterest_handle_layout.addWidget(pinterest_at)
        pinterest_handle_layout.addWidget(
            self.pinterest_username,
            1,
        )
        pinterest_layout.addWidget(
            pinterest_handle_frame
        )

        pinterest_layout.addWidget(
            QLabel("Браузер с выполненным входом")
        )

        self.pinterest_browser = QComboBox()
        self.pinterest_browser.installEventFilter(self)
        self.pinterest_browser.addItem(
            "Google Chrome",
            "chrome",
        )
        self.pinterest_browser.addItem(
            "Яндекс.Браузер",
            "yandex",
        )
        self.pinterest_browser.addItem(
            "Safari",
            "safari",
        )
        self.pinterest_browser.addItem(
            "Firefox",
            "firefox",
        )
        pinterest_layout.addWidget(
            self.pinterest_browser
        )

        pinterest_layout.addWidget(
            QLabel("Скорость загрузки")
        )

        self.pinterest_download_speed = QComboBox()

        for index in range(
            self.download_speed.count()
        ):
            self.pinterest_download_speed.addItem(
                self.download_speed.itemText(index),
                self.download_speed.itemData(index),
            )

        pinterest_layout.addWidget(
            self.pinterest_download_speed
        )

        pinterest_hint = QLabel(
            "ReferenceSync использует существующий вход "
            "в Pinterest и не запрашивает пароль."
        )
        pinterest_hint.setObjectName("hint")
        pinterest_hint.setWordWrap(True)
        pinterest_layout.addWidget(
            pinterest_hint
        )

        source_layout.addWidget(
            self.pinterest_panel
        )

        self.pinterest_archive_panel = QFrame()
        pinterest_archive_layout = QVBoxLayout(
            self.pinterest_archive_panel
        )
        pinterest_archive_layout.setContentsMargins(
            0, 8, 0, 0
        )
        pinterest_archive_layout.setSpacing(9)

        pinterest_archive_layout.addWidget(
            QLabel("Архив Pinterest")
        )

        pinterest_archive_row = QHBoxLayout()

        self.pinterest_archive_path = QLineEdit()
        self.pinterest_archive_path.setReadOnly(True)
        self.pinterest_archive_path.setPlaceholderText(
            "Выберите архив Pinterest"
        )

        choose_pinterest_archive = QPushButton(
            "Выбрать…"
        )
        choose_pinterest_archive.clicked.connect(
            self.choose_pinterest_archive
        )

        pinterest_archive_row.addWidget(
            self.pinterest_archive_path,
            1,
        )
        pinterest_archive_row.addWidget(
            choose_pinterest_archive
        )
        pinterest_archive_layout.addLayout(
            pinterest_archive_row
        )

        pinterest_archive_hint = QLabel(
            "Архив запрашивается в Pinterest: "
            "Settings → Privacy and data → "
            "Request your data."
        )
        pinterest_archive_hint.setObjectName("hint")
        pinterest_archive_hint.setWordWrap(True)
        pinterest_archive_layout.addWidget(
            pinterest_archive_hint
        )

        source_layout.addWidget(
            self.pinterest_archive_panel
        )

        self.pinterest_url = QLineEdit()
        self.pinterest_url.setVisible(False)

        left.addWidget(source_card)

        # Search mode
        search_card = self.make_card()
        self.search_card = search_card
        search_layout = QVBoxLayout(search_card)
        search_layout.setContentsMargins(20, 18, 20, 18)
        search_layout.setSpacing(10)

        search_title = QLabel("2. Как искать публикации")
        search_title.setObjectName("sectionTitle")
        search_layout.addWidget(search_title)

        self.smart_search = QRadioButton(
            "Найти только новые"
        )
        self.full_search = QRadioButton(
            "Проверить все сохранённые"
        )
        self.recent_search = QRadioButton(
            "Проверить только последние"
        )
        self.smart_search.setChecked(True)

        self.search_group = QButtonGroup(self)
        self.search_group.addButton(self.smart_search)
        self.search_group.addButton(self.full_search)
        self.search_group.addButton(self.recent_search)

        search_layout.addWidget(self.smart_search)

        smart_hint = QLabel(
            "Основной режим. Программа идёт от новых "
            "публикаций к старым и ищет границу "
            "предыдущей синхронизации."
        )
        smart_hint.setObjectName("radioHint")
        smart_hint.setWordWrap(True)
        search_layout.addWidget(smart_hint)

        search_layout.addWidget(self.full_search)

        full_hint = QLabel(
            "Полный анализ всего раздела Saved без ограничения "
            "по количеству. Подходит для первого переноса."
        )
        full_hint.setObjectName("radioHint")
        full_hint.setWordWrap(True)
        search_layout.addWidget(full_hint)

        recent_row = QHBoxLayout()
        recent_row.addWidget(self.recent_search)
        recent_row.addStretch()

        self.recent_limit = QSpinBox()
        self.recent_limit.setRange(1, 500)
        self.recent_limit.setValue(50)
        self.recent_limit.setSuffix(" постов")

        recent_row.addWidget(self.recent_limit)
        search_layout.addLayout(recent_row)

        recent_hint = QLabel(
            "Дополнительный режим для быстрой "
            "проверки или тестирования."
        )
        recent_hint.setObjectName("radioHint")
        recent_hint.setWordWrap(True)
        search_layout.addWidget(recent_hint)

        self.smart_search.toggled.connect(
            self.update_search_interface
        )
        self.full_search.toggled.connect(
            self.update_search_interface
        )
        self.recent_search.toggled.connect(
            self.update_search_interface
        )

        folder_search_row = QHBoxLayout()
        folder_search_row.setContentsMargins(0, 6, 0, 0)
        folder_search_row.setSpacing(8)

        self.selected_folders_toggle = ToggleSwitch()
        self.selected_folders_toggle.setChecked(False)
        self.selected_folders_toggle.toggled.connect(
            self.folder_search_toggled
        )

        self.folder_search_label = QLabel(
            "Искать в выбранных папках"
        )

        self.folder_search_info = QPushButton("i")
        self.folder_search_info.setObjectName(
            "folderSearchInfo"
        )
        self.folder_search_info.setFixedSize(24, 24)
        self.folder_search_info.setToolTip(
            "Как работает поиск в папках"
        )
        self.folder_search_info.setStyleSheet("""
            QPushButton#folderSearchInfo {
                border: 1px solid #626977;
                border-radius: 12px;
                background: #2b3039;
                color: #dfe2e8;
                font-weight: 700;
                padding: 0;
            }

            QPushButton#folderSearchInfo:hover {
                border-color: #8d83ee;
                background: #353a46;
                color: #ffffff;
            }
        """)
        self.folder_search_info.clicked.connect(
            self.show_folder_search_info
        )

        folder_search_row.addWidget(
            self.selected_folders_toggle
        )
        folder_search_row.addWidget(
            self.folder_search_label
        )
        folder_search_row.addWidget(
            self.folder_search_info
        )
        folder_search_row.addStretch()

        search_layout.addLayout(folder_search_row)

        left.addWidget(search_card)

        # Filters
        self.filters_group = QGroupBox(
            "Дополнительные фильтры"
        )
        self.filters_group.setCheckable(True)
        self.filters_group.setChecked(False)

        filters = QVBoxLayout(self.filters_group)
        filters.setContentsMargins(18, 18, 18, 16)
        filters.setSpacing(10)

        filters.addWidget(QLabel("Тип публикации"))

        types_row = QHBoxLayout()

        self.include_posts = QCheckBox(
            "Обычные посты"
        )
        self.include_reels = QCheckBox("Reels")
        self.include_carousels = QCheckBox(
            "Карусели"
        )

        self.include_posts.setChecked(True)
        self.include_reels.setChecked(True)
        self.include_carousels.setChecked(True)

        types_row.addWidget(self.include_posts)
        types_row.addWidget(self.include_reels)
        types_row.addWidget(
            self.include_carousels
        )
        types_row.addStretch()

        filters.addLayout(types_row)

        filters.addWidget(
            QLabel("Только выбранные авторы")
        )

        self.include_authors = QLineEdit()
        self.include_authors.setPlaceholderText(
            "@author1, @author2"
        )
        filters.addWidget(self.include_authors)

        filters.addWidget(
            QLabel("Исключить авторов")
        )

        self.exclude_authors = QLineEdit()
        self.exclude_authors.setPlaceholderText(
            "@author3, @author4"
        )
        filters.addWidget(self.exclude_authors)

        self.include_authors.textEdited.connect(
            lambda _text: self.format_author_field(
                self.include_authors
            )
        )
        self.exclude_authors.textEdited.connect(
            lambda _text: self.format_author_field(
                self.exclude_authors
            )
        )

        left.addWidget(self.filters_group)

        self.search_button = QPushButton(
            "Найти публикации"
        )
        self.search_button.setObjectName("primaryButton")
        self.search_button.clicked.connect(
            self.start_preview
        )
        left.addWidget(self.search_button)

        left.addStretch()

        # Right side
        right_container = QWidget()
        right_container.setMinimumWidth(640)

        right = QVBoxLayout(right_container)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(14)

        self.workspace_splitter.addWidget(
            right_container
        )
        self.workspace_splitter.setStretchFactor(
            0,
            0,
        )
        self.workspace_splitter.setStretchFactor(
            1,
            1,
        )
        self.workspace_splitter.set_default_sizes([
            360,
            1240,
        ])

        status_card = self.make_card()
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(18, 10, 18, 10)
        status_layout.setSpacing(7)

        status_top = QHBoxLayout()
        status_top.setContentsMargins(0, 0, 0, 0)

        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("statusDot")

        self.status = QLabel(
            "Готово к поиску публикаций"
        )
        self.status.setObjectName("statusText")

        self.search_duration_info = QLabel("")
        self.search_duration_info.setObjectName(
            "searchDurationInfo"
        )
        self.search_duration_info.setVisible(False)
        self.search_duration_info.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.search_duration_info.setToolTip(
            "Длительность последнего поиска"
        )
        self.search_duration_info.setStyleSheet(
            "QLabel#searchDurationInfo {"
            " color: #a9a1ff;"
            " font-size: 17px;"
            " font-weight: 700;"
            " padding: 1px 5px;"
            "}"
        )

        self.summary = QLabel("Найдено: 0")

        status_top.addWidget(self.status_dot)
        status_top.addWidget(self.status)
        status_top.addWidget(
            self.search_duration_info
        )
        status_top.addStretch()
        status_top.addWidget(self.summary)

        status_bottom = QHBoxLayout()
        status_bottom.setContentsMargins(0, 0, 0, 0)
        status_bottom.setSpacing(7)

        self.pause_button = QPushButton()
        self.resume_button = QPushButton()
        self.stop_button = QPushButton()

        self.pause_button.setIcon(
            self.style().standardIcon(
                _RSQStyle.StandardPixmap.SP_MediaPause
            )
        )
        self.resume_button.setIcon(
            self.style().standardIcon(
                _RSQStyle.StandardPixmap.SP_MediaPlay
            )
        )
        self.stop_button.setIcon(
            self.style().standardIcon(
                _RSQStyle.StandardPixmap.SP_MediaStop
            )
        )

        self.pause_button.setToolTip(
            "Приостановить загрузку"
        )
        self.resume_button.setToolTip(
            "Продолжить загрузку"
        )
        self.stop_button.setToolTip(
            "Остановить и сохранить очередь"
        )

        for control_button in (
            self.pause_button,
            self.resume_button,
            self.stop_button,
        ):
            control_button.setFixedSize(44, 27)
            control_button.setEnabled(False)
            control_button.setVisible(False)

        self.pause_button.clicked.connect(
            self.pause_import
        )
        self.resume_button.clicked.connect(
            self.resume_import
        )
        self.stop_button.clicked.connect(
            self.stop_import
        )

        self.operation_progress = QProgressBar()
        self.operation_progress.setObjectName(
            "operationProgress"
        )
        self.operation_progress.setRange(0, 100)
        self.operation_progress.setValue(0)
        self.operation_progress.setMinimumWidth(420)
        self.operation_progress.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.operation_progress.setFixedHeight(24)
        self.operation_progress.setTextVisible(True)
        self.operation_progress.setVisible(False)

        self.continue_folders_button = QPushButton(
            "Продолжить"
        )
        self.continue_folders_button.setToolTip(
            "Продолжить поиск публикаций "
            "в отмеченных папках."
        )
        self.continue_folders_button.setEnabled(False)
        self.continue_folders_button.setVisible(False)
        self.continue_folders_button.clicked.connect(
            self.continue_with_selected_folders
        )

        status_bottom.addWidget(self.pause_button)
        status_bottom.addWidget(self.resume_button)
        status_bottom.addWidget(self.stop_button)
        status_bottom.addWidget(
            self.operation_progress,
            1,
        )
        status_bottom.addWidget(
            self.continue_folders_button
        )

        status_layout.addLayout(status_top)
        status_layout.addLayout(status_bottom)

        right.addWidget(status_card)

        # The results table and naming controls can be resized
        # vertically, like docked panels in editing applications.
        self.right_workspace_splitter = ResettableSplitter(
            Qt.Orientation.Vertical
        )
        self.right_workspace_splitter.setObjectName(
            "rightWorkspaceSplitter"
        )
        self.right_workspace_splitter.setChildrenCollapsible(
            False
        )
        self.right_workspace_splitter.setHandleWidth(3)

        # PHYSICAL_SPLITTER_WIDTH_V58
        # Both handles have the same real 3px geometry.
        splitter_style = """
            QSplitter::handle {
                background: transparent;
                border: none;
                margin: 0;
                padding: 0;
                border-radius: 1px;
            }

            QSplitter::handle:hover {
                background: #7569e8;
            }

            QSplitter::handle:pressed {
                background: #958cff;
            }
        """

        self.workspace_splitter.setStyleSheet(
            splitter_style
        )
        self.right_workspace_splitter.setStyleSheet(
            splitter_style
        )

        right.addWidget(
            self.right_workspace_splitter,
            1,
        )

        results_card = self.make_card()
        results_layout = QVBoxLayout(results_card)
        results_layout.setContentsMargins(
            18, 16, 18, 18
        )
        results_layout.setSpacing(10)

        # STABLE_RESULTS_HEADER_V53
        # Two compact rows prevent optional controls from changing
        # the minimum width of the complete results panel.
        result_header = QVBoxLayout()
        result_header.setContentsMargins(0, 0, 0, 0)
        result_header.setSpacing(6)

        result_header_primary = QHBoxLayout()
        result_header_primary.setContentsMargins(
            0, 0, 0, 0
        )

        result_header_options = QHBoxLayout()
        result_header_options.setContentsMargins(
            0, 0, 0, 0
        )

        result_title = QLabel(
            "Найденные публикации"
        )
        result_title.setObjectName("sectionTitle")

        self.reset_all_edits = QPushButton(
            "↶ Сбросить все изменения"
        )
        self.reset_all_edits.setVisible(False)
        self.reset_all_edits.setToolTip(
            "Вернуть автоматически созданные названия "
            "и оригинальные описания Instagram"
        )
        self.reset_all_edits.clicked.connect(
            self.reset_all_manual_changes
        )

        self.select_all = QCheckBox("Выбрать все")
        self.select_all.setChecked(True)
        self.select_all.stateChanged.connect(
            self.toggle_all_rows
        )

        self.thumbnails_toggle = QCheckBox(
            "Миниатюры"
        )
        self.thumbnails_toggle.setChecked(False)
        self.thumbnails_toggle.setToolTip(
            "Загружать только маленькие обложки "
            "150×150 для видимых строк"
        )

        # Kept as a hidden compatibility target for
        # ThumbnailController. It is intentionally not added
        # to any visible layout.
        self.thumbnail_statistics = QLabel(
            "",
            results_card,
        )
        self.thumbnail_statistics.setVisible(False)

        self.clear_results_button = QPushButton(
            "Очистить найденное"
        )
        self.clear_results_button.setToolTip(
            "Очистить текущий список. Eagle, SQLite "
            "и загруженные файлы не изменяются."
        )
        self.clear_results_button.setEnabled(False)
        self.clear_results_button.clicked.connect(
            self.clear_found_results
        )

        result_header_primary.addWidget(result_title)
        result_header_primary.addStretch()
        result_header_primary.addWidget(
            self.reset_all_edits
        )
        result_header_primary.addWidget(
            self.clear_results_button
        )

        # RESULTS_OPTIONS_OPPOSITE_CORNERS_V54
        result_header_options.addWidget(
            self.thumbnails_toggle
        )
        result_header_options.addStretch()
        result_header_options.addWidget(
            self.select_all
        )

        result_header.addLayout(
            result_header_primary
        )
        result_header.addLayout(
            result_header_options
        )

        results_layout.addLayout(result_header)

        self.container_tree = QTreeWidget()
        self.container_tree.setObjectName(
            "sourceContainerTree"
        )
        self.container_tree.setHeaderLabels([
            "Доска, коллекция или раздел",
            "Тип",
            "Количество",
        ])
        self.container_tree.setAlternatingRowColors(True)
        self.container_tree.setRootIsDecorated(True)
        self.container_tree.setUniformRowHeights(True)
        self.container_tree.setVisible(False)
        self.container_tree.itemChanged.connect(
            self.container_tree_item_changed
        )
        self.container_tree.setStyleSheet("""
            QTreeWidget#sourceContainerTree {
                background: #15181e;
                alternate-background-color: #1c1f27;
                border: none;
                border-radius: 8px;
                color: #e4e6eb;
            }

            QTreeWidget#sourceContainerTree::item {
                min-height: 30px;
                padding: 3px;
            }

            QTreeWidget#sourceContainerTree::item:selected {
                background: #403a73;
            }
        """)
        results_layout.addWidget(self.container_tree)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "",
            "Автор",
            "Тип",
            "Структура",
            "Название",
            "Описание",
        ])
        # FIXED_ROWS_FITTING_COLUMNS_V43
        vertical_header = self.table.verticalHeader()
        vertical_header.setVisible(False)
        vertical_header.setSectionResizeMode(
            QHeaderView.ResizeMode.Fixed
        )
        vertical_header.setDefaultSectionSize(76)
        vertical_header.setMinimumSectionSize(76)
        vertical_header.setMaximumSectionSize(76)

        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )

        # Smooth table scrolling instead of jumping one
        # complete publication at a time.
        self.table.setVerticalScrollMode(
            QTableWidget.ScrollMode.ScrollPerPixel
        )
        self.table.setHorizontalScrollMode(
            QTableWidget.ScrollMode.ScrollPerPixel
        )
        self.table.verticalScrollBar().setSingleStep(12)
        self.table.horizontalScrollBar().setSingleStep(12)

        # CENTERED_ROW_SELECTOR_AND_RESIZABLE_TEXT_V42
        self.lightweight_row_selector_delegate = (
            LightweightRowSelectorDelegate(self.table)
        )
        self.table.setItemDelegateForColumn(
            0,
            self.lightweight_row_selector_delegate,
        )

        # LIGHTWEIGHT_STRUCTURE_CONTROLS_V3
        # One shared delegate replaces persistent QPushButton
        # widgets in the Structure column.
        self.lightweight_structure_delegate = (
            LightweightStructureDelegate(
                self.open_carousel_selection,
                self.table,
            )
        )
        self.table.setItemDelegateForColumn(
            3,
            self.lightweight_structure_delegate,
        )

        # GLOBAL_COMMITTED_TABLE_UNDO_V52_FIXED
        # History contains only user edits committed by the
        # temporary table text editor.
        self._text_undo_stack = []
        self._text_redo_stack = []
        self._text_history_limit = 500
        self._applying_text_history = False

        # LIGHTWEIGHT_TABLE_TEXT_EDITORS_V1
        # One shared delegate replaces two persistent QTextEdit
        # widgets and two layouts for every publication row.
        self.lightweight_text_delegate = (
            LightweightTextDelegate(
                self.table,
                self.record_committed_text_edit,
            )
        )
        self.table.setItemDelegateForColumn(
            4,
            self.lightweight_text_delegate,
        )
        self.table.setItemDelegateForColumn(
            5,
            self.lightweight_text_delegate,
        )
        self.table.itemChanged.connect(
            self.table_text_item_changed
        )

        # Listen after the temporary cell editor has closed.
        # While it is active, its own delegate handles Undo/Redo.
        app = _RSQApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        # Clearing/removing table rows invalidates item-based
        # history entries, so discard the old history.
        table_model = self.table.model()
        table_model.modelReset.connect(
            self.clear_text_edit_history
        )
        table_model.rowsRemoved.connect(
            lambda *_args: self.clear_text_edit_history()
        )

        # Prevent intermediate recalculations while hundreds of
        # lightweight rows are being constructed.
        self._populating_results = False

        header = self.table.horizontalHeader()
        # TABLE_LINEAR_SCALING_V41
        # ResizeToContents repeatedly scans all existing rows
        # during population and causes near-quadratic growth.
        header.setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Fixed,
        )
        header.setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.Interactive,
        )
        header.setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.Interactive,
        )
        header.setSectionResizeMode(
            3,
            QHeaderView.ResizeMode.Interactive,
        )

        header.resizeSection(0, 42)
        header.resizeSection(1, 115)
        header.resizeSection(2, 100)
        header.resizeSection(3, 230)
        # V4.3: Name can be resized manually. Description
        # absorbs the remaining viewport width, preventing the
        # large initial horizontal overflow introduced in V4.2.
        header.setSectionResizeMode(
            4,
            QHeaderView.ResizeMode.Interactive,
        )
        header.setSectionResizeMode(
            5,
            QHeaderView.ResizeMode.Stretch,
        )

        header.resizeSection(4, 230)
        header.setStretchLastSection(True)

        self.thumbnail_controller = ThumbnailController(
            table=self.table,
            toggle=self.thumbnails_toggle,
            statistics_label=self.thumbnail_statistics,
            cache_directory=(
                PROJECT
                / "data"
                / "cache"
                / "instagram_thumbnails"
            ),
            parent=self,
        )

        results_layout.addWidget(self.table)
        self.right_workspace_splitter.addWidget(
            results_card
        )

        # Naming settings, hidden before results.
        # ADAPTIVE_NAMING_SCROLL_V56
        self.naming_content = QGroupBox(
            "3. Названия, описания и нумерация"
        )
        self.naming_content.setMinimumSize(0, 0)
        self.naming_content.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

        naming = QGridLayout(self.naming_content)
        naming.setSizeConstraint(
            QLayout.SizeConstraint.SetNoConstraint
        )
        naming.setContentsMargins(18, 18, 18, 18)
        naming.setHorizontalSpacing(12)
        naming.setVerticalSpacing(10)

        self.naming_group = QScrollArea()
        self.naming_group.setObjectName(
            "namingWorkspaceScroll"
        )
        self.naming_group.setWidgetResizable(True)
        self.naming_group.setFrameShape(
            QFrame.Shape.NoFrame
        )
        self.naming_group.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.naming_group.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.naming_group.setMinimumHeight(95)
        self.naming_group.setWidget(
            self.naming_content
        )
        self.naming_group.setVisible(False)

        self.numbering_enabled = QCheckBox(
            "Использовать нумерацию"
        )
        self.numbering_enabled.setChecked(True)
        self.numbering_enabled.toggled.connect(
            self.update_numbering_interface
        )
        self.numbering_enabled.toggled.connect(
            self.refresh_generated_names
        )

        naming.addWidget(
            self.numbering_enabled,
            0, 0, 1, 2,
        )

        naming.addWidget(
            QLabel("Добавлять в:"),
            1, 0,
        )

        self.numbering_destination = QComboBox()
        self.numbering_destination.addItems([
            "Название",
            "Описание",
            "Название и описание",
        ])
        self.numbering_destination.currentIndexChanged.connect(
            self.refresh_generated_names
        )
        naming.addWidget(
            self.numbering_destination,
            1, 1,
        )

        naming.addWidget(
            QLabel("Текст перед номером:"),
            2, 0,
        )

        self.numbering_text = QLineEdit(
            "instpoporder-"
        )
        self.numbering_text.textChanged.connect(
            self.refresh_generated_names
        )
        naming.addWidget(
            self.numbering_text,
            2, 1,
        )

        naming.addWidget(
            QLabel("Начать с:"),
            3, 0,
        )

        self.naming_start = QSpinBox()
        self.naming_start.setRange(1, 999999999)
        self.naming_start.setValue(1)
        self.naming_start.valueChanged.connect(
            self.refresh_generated_names
        )
        naming.addWidget(
            self.naming_start,
            3, 1,
        )

        naming.addWidget(
            QLabel("Первое число:"),
            1, 2,
        )

        self.counter_one = QComboBox()
        self.counter_one.addItems([
            "Общий номер публикации",
            "Номер в текущей загрузке",
            "Номер публикации автора",
            "Номер публикации этого типа",
        ])
        naming.addWidget(
            self.counter_one,
            1, 3,
        )

        naming.addWidget(
            QLabel("Второе число:"),
            2, 2,
        )

        self.counter_two = QComboBox()
        self.counter_two.addItems([
            "Номер элемента карусели",
            "Не использовать",
            "Номер публикации автора",
            "Номер публикации этого типа",
        ])
        naming.addWidget(
            self.counter_two,
            2, 3,
        )

        self.add_counter = QPushButton(
            "＋ Добавить ещё счётчик"
        )
        self.add_counter.clicked.connect(
            self.show_counter_notice
        )
        naming.addWidget(
            self.add_counter,
            3, 3,
        )

        naming.addWidget(
            QLabel("Дополнительное описание:"),
            4, 0,
        )

        self.description_template = QLineEdit()
        self.description_template.setPlaceholderText(
            "Необязательный текст для выбранных публикаций"
        )
        self.description_template.textChanged.connect(
            self.refresh_generated_names
        )
        naming.addWidget(
            self.description_template,
            4, 1, 1, 3,
        )

        self.right_workspace_splitter.addWidget(
            self.naming_group
        )
        self.right_workspace_splitter.setStretchFactor(
            0,
            1,
        )
        self.right_workspace_splitter.setStretchFactor(
            1,
            0,
        )
        # Results receive most of the default height.
        # Double-clicking the horizontal handle restores this.
        self.right_workspace_splitter.set_default_sizes([
            720,
            220,
        ])

        actions = QHBoxLayout()

        self.log_button = QPushButton(
            "Показать технический журнал"
        )
        self.log_button.setCheckable(True)
        self.log_button.toggled.connect(
            lambda checked:
            self.log.setVisible(checked)
        )

        actions.addWidget(self.log_button)
        actions.addStretch()


        self.import_button = QPushButton(
            "Скачать и добавить в Eagle"
        )
        self.import_button.setObjectName("importButton")
        self.import_button.setEnabled(False)
        self.import_button.setToolTip(
            "Скачать выбранные публикации и добавить "
            "их в Eagle"
        )
        self.import_button.clicked.connect(
            self.start_import
        )

        actions.addWidget(self.import_button)
        right.addLayout(actions)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setVisible(False)
        self.log.setMaximumHeight(150)
        right.addWidget(self.log)

        footer = QLabel(
            "Сейчас работает безопасный preview: "
            "файлы не скачиваются, Eagle не изменяется"
        )
        footer.setObjectName("muted")
        footer.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        main.addWidget(footer)

    # -----------------------------------------------------
    # UI state
    # -----------------------------------------------------

    def show_operation_error(
        self,
        title: str,
        message: str,
    ) -> None:
        previous = getattr(
            self,
            "_operation_error_dialog",
            None,
        )

        if previous is not None:
            try:
                previous.close()
            except RuntimeError:
                pass

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(False)
        dialog.setWindowModality(
            Qt.WindowModality.NonModal
        )
        dialog.setWindowFlag(
            Qt.WindowType.Tool,
            True,
        )
        dialog.setAttribute(
            Qt.WidgetAttribute.WA_DeleteOnClose,
            True,
        )
        dialog.setMinimumWidth(470)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        heading = QLabel(title)
        heading.setStyleSheet(
            "color: #ff786b;"
            "font-size: 17px;"
            "font-weight: 700;"
        )
        layout.addWidget(heading)

        text = QLabel(message)
        text.setWordWrap(True)
        text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(text)

        note = QLabel(
            "Очередь и уже скачанные файлы сохранены. "
            "Подробная техническая информация доступна "
            "в журнале."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #9299a8;")
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch()

        log_button = QPushButton(
            "Показать технический журнал"
        )
        close_button = QPushButton("Закрыть")

        def show_log() -> None:
            self.log.setVisible(True)
            self.log_button.setChecked(True)
            self.raise_()
            self.activateWindow()

        log_button.clicked.connect(show_log)
        close_button.clicked.connect(
            dialog.close
        )

        buttons.addWidget(log_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        dialog.destroyed.connect(
            lambda *_args: setattr(
                self,
                "_operation_error_dialog",
                None,
            )
        )

        self._operation_error_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def make_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        return card

    def show_future_platform(
        self,
        name: str,
    ) -> None:
        QMessageBox.information(
            self,
            name,
            f"{name} появится в следующих версиях "
            "ReferenceSync.",
        )

    # PINTEREST_GUI_CONNECTION_V1
    def switch_platform(
        self,
        source_code: str,
    ) -> None:
        source_code = str(
            source_code or ""
        ).strip().lower()

        if source_code not in {
            "instagram",
            "pinterest",
        }:
            return

        if self.process is not None:
            QMessageBox.information(
                self,
                "Операция выполняется",
                "Сначала дождитесь завершения операции.",
            )
            return

        changed = source_code != self.active_source
        self.active_source = source_code

        for code, button in (
            self.platform_button_by_source.items()
        ):
            selected = code == source_code
            button.setObjectName(
                "activePlatform"
                if selected
                else "futurePlatform"
            )
            button.setProperty(
                "platformActive",
                selected,
            )
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

        if source_code == "pinterest":
            self.active_platform_label.setText(
                "Pinterest"
            )
            self.numbering_text.setText(
                "pinorder-"
            )
        else:
            self.active_platform_label.setText(
                "Instagram"
            )
            self.numbering_text.setText(
                "instpoporder-"
            )

        self.update_source_interface()

        if changed:
            self.reset_container_tree()

            self.thumbnail_controller.clear()
            self.table.setRowCount(0)
            self.preview_items = []
            self.naming_group.setVisible(False)
            self.import_button.setEnabled(False)
            self.clear_results_button.setEnabled(False)
            self.select_all.blockSignals(True)
            self.select_all.setChecked(False)
            self.select_all.blockSignals(False)
            self.summary.setText("Найдено: 0")
            self.status.setText(
                "Выбран источник: "
                + self.active_platform_label.text()
            )

    def update_source_interface(self) -> None:
        pinterest_mode = (
            self.active_source == "pinterest"
        )
        browser_mode = self.browser_source.isChecked()

        self.browser_source.setVisible(True)
        self.meta_source.setVisible(True)

        self.browser_source.setText(
            "Через авторизованный браузер"
        )
        self.meta_source.setText(
            "Из архива Pinterest"
            if pinterest_mode
            else "Из архива Meta"
        )

        self.browser_panel.setVisible(
            not pinterest_mode and browser_mode
        )
        self.meta_panel.setVisible(
            not pinterest_mode and not browser_mode
        )
        self.pinterest_panel.setVisible(
            pinterest_mode and browser_mode
        )
        self.pinterest_archive_panel.setVisible(
            pinterest_mode and not browser_mode
        )
        self.search_card.setVisible(True)
        self.filters_group.setVisible(
            not pinterest_mode
        )

        if pinterest_mode:
            self.source_title.setText(
                "1. Источник Pinterest"
            )
        else:
            self.source_title.setText(
                "1. Источник Instagram"
            )

        if self.selected_folders_toggle.isChecked():
            self.search_button.setText(
                "Показать папки"
            )
        else:
            self.search_button.setText(
                "Найти новые публикации"
            )

    def show_folder_search_info(self) -> None:
        QMessageBox.information(
            self,
            "Поиск в выбранных папках",
            "При включении этого режима ReferenceSync сначала "
            "найдёт доступные доски, коллекции и разделы. "
            "Выберите нужные папки и нажмите «Продолжить». "
            "После этого выбранный режим поиска будет применён "
            "только к содержимому отмеченных папок.\n\n"
            "Если режим выключен, поиск выполняется по общему "
            "списку всех сохранённых публикаций.",
        )

    def folder_search_toggled(
        self,
        enabled: bool,
    ) -> None:
        self.reset_container_tree()

        if enabled:
            self.search_button.setText("Показать папки")
            self.status.setText(
                "Сначала покажем папки источника"
            )
        else:
            self.search_button.setText(
                "Найти новые публикации"
            )
            self.status.setText(
                "Поиск выполняется по общему списку"
            )


    def reset_container_tree(self) -> None:
        self._folder_search_ready = False
        self._updating_container_tree = True

        try:
            self.container_tree.clear()
        finally:
            self._updating_container_tree = False

        self.container_tree.setVisible(False)
        self.table.setVisible(True)
        self.continue_folders_button.setVisible(False)
        self.continue_folders_button.setEnabled(False)
        self.container_payload = None
        self.selected_container_records = []

    def start_container_preview(self) -> None:
        if not self.browser_source.isChecked():
            QMessageBox.information(
                self,
                "Архив пока не подключён",
                "Выбор папок из архива будет доступен "
                "после подключения архивного парсера.",
            )
            return

        if self.active_source == "pinterest":
            username = (
                self.pinterest_username.text()
                .strip()
                .lstrip("@")
            )
            browser = (
                self.pinterest_browser.currentData()
                or "chrome"
            )
            missing_text = (
                "Введите имя Pinterest-аккаунта после @."
            )
        else:
            username = (
                self.username.text().strip().lstrip("@")
            )
            browser = (
                self.browser.currentData() or "chrome"
            )
            missing_text = (
                "Введите Instagram-никнейм после @."
            )

        if not username:
            QMessageBox.warning(
                self,
                "Не указан аккаунт",
                missing_text,
            )
            return

        output = (
            Path("/tmp")
            / (
                "reference_sync_containers_"
                + self.active_source
                + "_"
                + datetime.now().strftime(
                    "%Y%m%d_%H%M%S_%f"
                )
                + ".json"
            )
        )

        arguments = [
            "-m",
            "app.source_container_preview",
            "--source",
            self.active_source,
            "--username",
            username,
            "--browser",
            str(browser),
            "--output",
            str(output),
        ]

        self.reset_container_tree()
        self.container_preview_output = output
        self.active_operation_source = "containers"
        self.process_output = ""
        self._process_line_buffer = ""
        self.log.clear()
        self.process_started_at = datetime.now().timestamp()

        self.table.setVisible(False)
        self.operation_progress.setVisible(True)
        self.operation_progress.setRange(0, 0)
        self.operation_progress.setFormat(
            "Получаем список папок…"
        )
        self.search_button.setEnabled(False)
        self.status.setText(
            "Получаем доступные папки источника…"
        )
        self.summary.setText("Папки: —")

        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(PROJECT))
        self.process.setProgram(str(PYTHON))
        self.process.setArguments(arguments)
        self.process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.process.readyReadStandardOutput.connect(
            self.read_process_output
        )
        self.process.finished.connect(
            self.preview_finished
        )
        self.process.start()

    def finish_container_preview(
        self,
        exit_code: int,
    ) -> None:
        output = self.container_preview_output
        self.active_operation_source = None
        self.container_preview_output = None
        self.operation_progress.setVisible(False)

        if (
            exit_code != 0
            or output is None
            or not output.is_file()
        ):
            self.table.setVisible(True)
            self.status.setText(
                "Не удалось получить список папок"
            )
            self.log.setVisible(True)
            self.log_button.setChecked(True)
            return

        try:
            payload = json.loads(
                output.read_text(encoding="utf-8")
            )
        except Exception as error:
            self.table.setVisible(True)
            self.status.setText(
                "Не удалось прочитать дерево папок"
            )
            self.log.append(str(error))
            self.log.setVisible(True)
            self.log_button.setChecked(True)
            return
        finally:
            output.unlink(missing_ok=True)

        if payload.get("status") != "SUCCESS":
            self.table.setVisible(True)
            self.status.setText(
                str(
                    payload.get("error")
                    or "Получение папок завершилось с ошибкой"
                )
            )
            return

        self.container_payload = payload
        self.populate_container_tree(
            payload.get("root") or {}
        )

    def populate_container_tree(
        self,
        root: dict,
    ) -> None:
        self._updating_container_tree = True
        self.container_tree.clear()

        try:
            if not isinstance(root, dict) or not root:
                raise ValueError(
                    "Источник не вернул корневой контейнер"
                )

            def add_node(
                parent,
                record: dict,
            ) -> QTreeWidgetItem:
                name = str(
                    record.get("name") or "Без названия"
                )
                container_type = str(
                    record.get("type") or "container"
                )
                metadata = record.get("metadata") or {}
                count = (
                    metadata.get("media_count")
                    if container_type == "collection"
                    else metadata.get("pin_count")
                )

                item = QTreeWidgetItem([
                    name,
                    container_type,
                    "" if count is None else str(count),
                ])
                item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    record,
                )

                if bool(record.get("selectable", True)):
                    item.setFlags(
                        item.flags()
                        | Qt.ItemFlag.ItemIsUserCheckable
                    )
                    item.setCheckState(
                        0,
                        Qt.CheckState.Unchecked,
                    )

                if parent is None:
                    self.container_tree.addTopLevelItem(
                        item
                    )
                else:
                    parent.addChild(item)

                for child in record.get("children") or []:
                    if isinstance(child, dict):
                        add_node(item, child)

                return item

            top = add_node(None, root)
            top.setExpanded(True)

            for index in range(top.childCount()):
                top.child(index).setExpanded(True)

        finally:
            self._updating_container_tree = False

        self._folder_search_ready = True
        self.table.setVisible(False)
        self.container_tree.setVisible(True)
        self.continue_folders_button.setVisible(True)
        self.continue_folders_button.setEnabled(False)

        total = int(
            self.container_payload.get(
                "container_count",
                0,
            )
        )
        self.summary.setText(f"Папки: {total}")
        self.status.setText(
            "Отметьте нужные папки и нажмите «Продолжить»"
        )

    def container_tree_item_changed(
        self,
        item: QTreeWidgetItem,
        column: int,
    ) -> None:
        if self._updating_container_tree or column != 0:
            return

        if not (
            item.flags()
            & Qt.ItemFlag.ItemIsUserCheckable
        ):
            return

        state = item.checkState(0)
        self._updating_container_tree = True

        try:
            def apply_children(
                parent: QTreeWidgetItem,
            ) -> None:
                for index in range(parent.childCount()):
                    child = parent.child(index)

                    if (
                        child.flags()
                        & Qt.ItemFlag.ItemIsUserCheckable
                    ):
                        child.setCheckState(0, state)

                    apply_children(child)

            apply_children(item)
        finally:
            self._updating_container_tree = False

        self.refresh_container_selection()

    def refresh_container_selection(self) -> None:
        selected = []

        def visit(
            item: QTreeWidgetItem,
            ancestor_selected: bool = False,
        ) -> None:
            checkable = bool(
                item.flags()
                & Qt.ItemFlag.ItemIsUserCheckable
            )
            checked = (
                checkable
                and item.checkState(0)
                == Qt.CheckState.Checked
            )

            record = item.data(
                0,
                Qt.ItemDataRole.UserRole,
            )

            if (
                checked
                and not ancestor_selected
                and isinstance(record, dict)
            ):
                selected.append(record)

            inherited = ancestor_selected or checked

            for index in range(item.childCount()):
                visit(item.child(index), inherited)

        for index in range(
            self.container_tree.topLevelItemCount()
        ):
            visit(
                self.container_tree.topLevelItem(index)
            )

        self.selected_container_records = selected
        self.continue_folders_button.setEnabled(
            bool(selected)
        )
        self.status.setText(
            (
                f"Выбрано папок: {len(selected)}"
                if selected
                else "Отметьте хотя бы одну папку"
            )
        )

    def continue_with_selected_folders(self) -> None:
        if not self._folder_search_ready:
            return

        self.refresh_container_selection()

        if not self.selected_container_records:
            QMessageBox.warning(
                self,
                "Папки не выбраны",
                "Отметьте хотя бы одну папку.",
            )
            return

        selected_names = [
            str(item.get("name") or item.get("id"))
            for item in self.selected_container_records
        ]

        self.log.setPlainText(
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

    def format_author_field(
        self,
        field: QLineEdit,
    ) -> None:
        original = field.text()
        cursor = field.cursorPosition()

        parts = original.split(",")
        formatted = []

        for part in parts:
            stripped = part.strip()

            if not stripped:
                formatted.append("")
            else:
                formatted.append(
                    "@"
                    + stripped.lstrip("@")
                )

        result = ", ".join(formatted)

        if original.endswith(","):
            result = result.rstrip() + ", @"
        elif original.endswith(", "):
            result = result.rstrip() + ", @"

        if result == original:
            return

        field.blockSignals(True)

        try:
            field.setText(result)
            field.setCursorPosition(
                min(
                    len(result),
                    cursor + max(
                        0,
                        len(result) - len(original),
                    ),
                )
            )
        finally:
            field.blockSignals(False)

    def update_search_interface(self) -> None:
        self.recent_limit.setEnabled(
            self.recent_search.isChecked()
        )

    def update_numbering_interface(self) -> None:
        enabled = self.numbering_enabled.isChecked()

        for widget in (
            self.numbering_destination,
            self.numbering_text,
            self.naming_start,
            self.counter_one,
            self.counter_two,
            self.add_counter,
        ):
            widget.setEnabled(enabled)

    def load_settings(self) -> None:
        username = self.settings.value(
            "instagram/username",
            "rellese26",
            type=str,
        )
        self.username.setText(
            str(username).strip().lstrip("@")
        )

        browser_code = self.settings.value(
            "instagram/browser",
            "yandex",
            type=str,
        )

        browser_index = self.browser.findData(
            browser_code
        )

        if browser_index >= 0:
            self.browser.setCurrentIndex(browser_index)

        speed_profile = self.settings.value(
            "instagram/download_speed",
            "safe",
            type=str,
        )
        speed_index = self.download_speed.findData(
            speed_profile
        )

        if speed_index >= 0:
            self.download_speed.setCurrentIndex(
                speed_index
            )

        recent_limit = self.settings.value(
            "instagram/recent_limit",
            50,
            type=int,
        )
        self.recent_limit.setValue(
            max(
                self.recent_limit.minimum(),
                min(
                    self.recent_limit.maximum(),
                    int(recent_limit),
                ),
            )
        )

        numbering_enabled = self.settings.value(
            "naming/numbering_enabled",
            True,
            type=bool,
        )
        self.numbering_enabled.setChecked(
            numbering_enabled
        )

        destination = self.settings.value(
            "naming/destination",
            "Название",
            type=str,
        )
        destination_index = (
            self.numbering_destination.findText(
                destination
            )
        )

        if destination_index >= 0:
            self.numbering_destination.setCurrentIndex(
                destination_index
            )

        marker = self.settings.value(
            "naming/marker",
            "instpoporder-",
            type=str,
        )
        self.numbering_text.setText(marker)

        saved_start = self.settings.value(
            "naming/start_number",
            1,
            type=int,
        )
        self.naming_start.setValue(
            max(
                self.naming_start.minimum(),
                min(
                    self.naming_start.maximum(),
                    int(saved_start),
                ),
            )
        )

        description = self.settings.value(
            "naming/description",
            "",
            type=str,
        )
        self.description_template.setText(description)

        workspace_state = self.settings.value(
            "ui/workspace_splitter_v56"
        )

        if workspace_state:
            self.workspace_splitter.restoreState(
                workspace_state
            )

        right_workspace_state = self.settings.value(
            "ui/right_workspace_splitter_v56"
        )

        if right_workspace_state:
            self.right_workspace_splitter.restoreState(
                right_workspace_state
            )

    def connect_settings_persistence(self) -> None:
        self.username.textChanged.connect(
            self.save_settings
        )
        self.browser.currentIndexChanged.connect(
            self.save_settings
        )
        self.download_speed.currentIndexChanged.connect(
            self.save_settings
        )
        self.recent_limit.valueChanged.connect(
            self.save_settings
        )
        self.numbering_enabled.toggled.connect(
            self.save_settings
        )
        self.numbering_destination.currentIndexChanged.connect(
            self.save_settings
        )
        self.numbering_text.textChanged.connect(
            self.save_settings
        )
        self.naming_start.valueChanged.connect(
            self.save_settings
        )
        self.description_template.textChanged.connect(
            self.save_settings
        )

    def save_settings(self, *_args) -> None:
        self.settings.setValue(
            "instagram/username",
            self.username.text().strip().lstrip("@"),
        )
        self.settings.setValue(
            "instagram/browser",
            self.browser.currentData(),
        )
        self.settings.setValue(
            "instagram/download_speed",
            self.download_speed.currentData(),
        )
        self.settings.setValue(
            "instagram/recent_limit",
            self.recent_limit.value(),
        )
        self.settings.setValue(
            "naming/numbering_enabled",
            self.numbering_enabled.isChecked(),
        )
        self.settings.setValue(
            "naming/destination",
            self.numbering_destination.currentText(),
        )
        self.settings.setValue(
            "naming/marker",
            self.numbering_text.text(),
        )
        self.settings.setValue(
            "naming/start_number",
            self.naming_start.value(),
        )
        self.settings.setValue(
            "naming/description",
            self.description_template.text(),
        )
        self.settings.setValue(
            "ui/workspace_splitter_v56",
            self.workspace_splitter.saveState(),
        )
        self.settings.setValue(
            "ui/right_workspace_splitter_v56",
            self.right_workspace_splitter.saveState(),
        )
        self.settings.sync()

    # -------------------------------------------------
    # V6.4.8 STAGE6.2 CLEAN EXIT SESSION CLEANUP
    # -------------------------------------------------

    @staticmethod
    def instagram_incoming_directory() -> Path:
        return (
            PROJECT
            / "downloads"
            / "instagram"
            / "incoming"
        )

    def snapshot_session_staging_jobs(
        self,
    ) -> dict[str, int]:
        """
        Snapshot job.json mtimes at GUI startup.

        A pre-existing job is considered part of this session only
        if its job.json changes, which happens when True Resume
        opens and updates that exact job.
        """
        incoming = self.instagram_incoming_directory()
        snapshot = {}

        if not incoming.is_dir():
            return snapshot

        for job_directory in incoming.glob("instagram_*"):
            if not job_directory.is_dir():
                continue

            job_file = job_directory / "job.json"

            if not job_file.is_file():
                continue

            try:
                snapshot[job_directory.name] = (
                    job_file.stat().st_mtime_ns
                )
            except OSError:
                continue

        return snapshot

    @staticmethod
    def staging_job_safety_state(
        payload: dict,
    ) -> tuple[int, bool]:
        summary = payload.get("summary")

        if not isinstance(summary, dict):
            summary = {}

        raw_eagle_values = [
            payload.get("eagle_items_created"),
            summary.get("eagle_items_created"),
        ]
        eagle_items_created = 0

        for raw_value in raw_eagle_values:
            try:
                eagle_items_created = max(
                    eagle_items_created,
                    int(raw_value or 0),
                )
            except (TypeError, ValueError):
                # Unknown write state is not safe to delete.
                eagle_items_created = max(
                    eagle_items_created,
                    1,
                )

        database_modified = bool(
            payload.get("database_modified")
            or summary.get("database_modified")
        )

        return eagle_items_created, database_modified

    def cleanup_current_session_staging(
        self,
    ) -> dict:
        """
        Delete all Instagram staging jobs on a normal GUI exit.

        Policy:
        - Stop while the GUI remains open preserves staging.
        - Crash / Force Quit preserves staging because closeEvent
          does not complete.
        - Normal Quit deletes every real direct instagram_* job
          directory after the child process has stopped.
        - Eagle and SQLite are never modified by this cleanup.
        """
        incoming = self.instagram_incoming_directory()

        result = {
            "status": "CLEAN_EXIT_ALL_INSTAGRAM_STAGING",
            "cleanup_policy": (
                "DELETE_ALL_INSTAGRAM_STAGING_ON_NORMAL_EXIT"
            ),
            "clean_exit": True,
            "attempted": True,
            "jobs_examined": 0,
            "jobs_deleted": [],
            "files_deleted": 0,
            "bytes_freed": 0,
            "jobs_preserved": [],
            "errors": list(
                self._clean_close_runtime_errors
            ),
            "eagle_modified": False,
            "sqlite_modified": False,
        }

        process = self.process

        if (
            process is not None
            and process.state()
            != QProcess.ProcessState.NotRunning
        ):
            result["status"] = (
                "CLEAN_EXIT_CLEANUP_REFUSED_PROCESS_RUNNING"
            )
            result["errors"].append({
                "type": "CHILD_PROCESS_STILL_RUNNING",
                "message": (
                    "Instagram staging was preserved because "
                    "the child process was still running."
                ),
            })
            result["jobs_deleted_count"] = 0
            result["jobs_preserved_count"] = 0
            self.write_session_cleanup_report(result)
            return result

        if not incoming.is_dir():
            result["status"] = (
                "CLEAN_EXIT_NO_STAGING_DIRECTORY"
            )
            result["jobs_deleted_count"] = 0
            result["jobs_preserved_count"] = 0
            self.write_session_cleanup_report(result)
            return result

        try:
            resolved_incoming = incoming.resolve()
        except OSError as error:
            result["status"] = (
                "CLEAN_EXIT_STAGING_PATH_RESOLVE_FAILED"
            )
            result["errors"].append({
                "type": "STAGING_PATH_RESOLVE_FAILED",
                "message": str(error),
            })
            result["jobs_deleted_count"] = 0
            result["jobs_preserved_count"] = 0
            self.write_session_cleanup_report(result)
            return result

        for job_directory in sorted(
            incoming.glob("instagram_*")
        ):
            if not job_directory.is_dir():
                continue

            result["jobs_examined"] += 1
            job_name = job_directory.name

            if (
                not job_name.startswith("instagram_")
                or job_name in {"instagram_", ".", ".."}
            ):
                result["jobs_preserved"].append({
                    "job": job_name,
                    "reason": "INVALID_JOB_DIRECTORY_NAME",
                })
                continue

            if job_directory.is_symlink():
                result["jobs_preserved"].append({
                    "job": job_name,
                    "reason": "SYMLINK_REFUSED",
                })
                continue

            try:
                resolved_job = job_directory.resolve()
                relative_job = resolved_job.relative_to(
                    resolved_incoming
                )
            except (OSError, ValueError) as error:
                result["jobs_preserved"].append({
                    "job": job_name,
                    "reason": (
                        "PATH_OUTSIDE_STAGING: "
                        + str(error)
                    ),
                })
                continue

            if (
                resolved_job == resolved_incoming
                or len(relative_job.parts) != 1
            ):
                result["jobs_preserved"].append({
                    "job": job_name,
                    "reason": "NOT_A_DIRECT_STAGING_CHILD",
                })
                continue

            previous_status = "UNKNOWN"
            job_file = job_directory / "job.json"

            if job_file.is_file():
                try:
                    payload = json.loads(
                        job_file.read_text(encoding="utf-8")
                    )

                    if isinstance(payload, dict):
                        previous_status = str(
                            payload.get("status")
                            or "UNKNOWN"
                        ).strip()
                    else:
                        previous_status = (
                            "JOB_JSON_NOT_OBJECT"
                        )
                except Exception:
                    previous_status = "INVALID_JOB_JSON"
            else:
                previous_status = "JOB_JSON_MISSING"

            files = []
            bytes_to_free = 0

            try:
                for path in job_directory.rglob("*"):
                    if not path.is_file():
                        continue

                    files.append(path)

                    try:
                        bytes_to_free += path.stat().st_size
                    except OSError:
                        pass
            except OSError as error:
                result["errors"].append({
                    "job": job_name,
                    "error": (
                        "STAGING_SCAN_FAILED: "
                        + str(error)
                    ),
                })

            try:
                shutil.rmtree(job_directory)
            except Exception as error:
                result["errors"].append({
                    "job": job_name,
                    "error": (
                        "STAGING_DELETE_FAILED: "
                        + str(error)
                    ),
                })
                result["jobs_preserved"].append({
                    "job": job_name,
                    "reason": "STAGING_DELETE_FAILED",
                })
                continue

            result["jobs_deleted"].append({
                "job_id": job_name,
                "previous_status": previous_status,
                "files_deleted": len(files),
                "bytes_freed": bytes_to_free,
            })
            result["files_deleted"] += len(files)
            result["bytes_freed"] += bytes_to_free

        result["jobs_deleted_count"] = len(
            result["jobs_deleted"]
        )
        result["jobs_preserved_count"] = len(
            result["jobs_preserved"]
        )

        if result["jobs_preserved"]:
            result["status"] = (
                "CLEAN_EXIT_ALL_STAGING_PARTIALLY_PRESERVED"
            )
        elif result["errors"]:
            result["status"] = (
                "CLEAN_EXIT_ALL_STAGING_COMPLETED_WITH_ERRORS"
            )
        else:
            result["status"] = (
                "CLEAN_EXIT_ALL_INSTAGRAM_STAGING_DELETED"
            )

        self.write_session_cleanup_report(result)
        return result

    def write_session_cleanup_report(
        self,
        result: dict,
    ) -> None:
        try:
            REPORTS.mkdir(
                parents=True,
                exist_ok=True,
            )

            timestamp = datetime.now().strftime(
                "%Y%m%d_%H%M%S_%f"
            )
            report_path = (
                REPORTS
                / (
                    "v648_session_cleanup_"
                    + timestamp
                    + ".json"
                )
            )

            report_payload = {
                "created_at": (
                    datetime.now().isoformat()
                ),
                **result,
            }

            report_path.write_text(
                json.dumps(
                    report_payload,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            # Cleanup reports must never prevent application exit.
            pass

    def clean_close_process_finished(
        self,
        *_args,
    ) -> None:
        if not self._clean_close_requested:
            return

        # Let the original preview_finished/import_finished slot run
        # first and release self.process before entering closeEvent
        # again.
        QTimer.singleShot(
            0,
            self.finish_clean_close,
        )

    def finish_clean_close(self) -> None:
        if not self._clean_close_requested:
            return

        self.close()

    def force_finish_clean_close(self) -> None:
        """
        Fallback only for a process that ignored the normal Stop.

        Force Quit remains distinguishable: the OS terminates the GUI
        without executing this timer or cleanup method.
        """
        if not self._clean_close_requested:
            return

        process = self.process

        if (
            process is not None
            and process.state()
            != QProcess.ProcessState.NotRunning
        ):
            self._clean_close_runtime_errors.append({
                "type": "GRACEFUL_STOP_TIMEOUT",
                "message": (
                    "The child process did not finish within "
                    "20 seconds during normal application exit."
                ),
            })

            process.terminate()

            if not process.waitForFinished(3000):
                process.kill()
                process.waitForFinished(3000)

        QTimer.singleShot(
            0,
            self.finish_clean_close,
        )

    def closeEvent(self, event) -> None:
        self.save_settings()

        process_running = (
            self.process is not None
            and self.process.state()
            != QProcess.ProcessState.NotRunning
        )

        if (
            not self._clean_close_requested
            and process_running
        ):
            self._clean_close_requested = True

            try:
                self.write_process_control("stop")
            except Exception as error:
                self._clean_close_runtime_errors.append({
                    "type": "STOP_CONTROL_WRITE_FAILED",
                    "message": str(error),
                })

            self.pause_button.setEnabled(False)
            self.resume_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.status.setText(
                "Завершаем процесс и очищаем "
                "временные файлы…"
            )

            if not self._clean_close_wait_connected:
                try:
                    self.process.finished.connect(
                        self.clean_close_process_finished
                    )
                    self._clean_close_wait_connected = True
                except RuntimeError as error:
                    self._clean_close_runtime_errors.append({
                        "type": (
                            "FINISHED_SIGNAL_CONNECT_FAILED"
                        ),
                        "message": str(error),
                    })

            # Keep Qt's event loop alive while the existing process
            # handles Stop and writes its final STOPPED_BY_USER state.
            QTimer.singleShot(
                20000,
                self.force_finish_clean_close,
            )
            event.ignore()
            return

        if (
            self._clean_close_requested
            and process_running
        ):
            event.ignore()
            return

        if not self._clean_close_completed:
            self._clean_close_requested = True

            try:
                cleanup_result = (
                    self.cleanup_current_session_staging()
                )

                deleted_jobs = int(
                    cleanup_result.get(
                        "jobs_deleted_count",
                        0,
                    )
                    or 0
                )
                deleted_files = int(
                    cleanup_result.get(
                        "files_deleted",
                        0,
                    )
                    or 0
                )

                if deleted_jobs:
                    print(
                        "REFERENCE_SYNC_CLEAN_EXIT_CLEANUP "
                        f"jobs={deleted_jobs} "
                        f"files={deleted_files}",
                        flush=True,
                    )
            except Exception as error:
                # Refuse unsafe deletion, but never trap the user
                # inside the application during normal Quit.
                self.write_session_cleanup_report({
                    "status": (
                        "CLEAN_EXIT_CLEANUP_FAILED_SAFE"
                    ),
                    "clean_exit": True,
                    "jobs_deleted": [],
                    "files_deleted": 0,
                    "bytes_freed": 0,
                    "error": str(error),
                    "eagle_modified": False,
                    "sqlite_modified": False,
                })

            self._clean_close_completed = True

        super().closeEvent(event)

    def choose_pinterest_archive(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите архив Pinterest",
            str(Path.home() / "Downloads"),
            (
                "Pinterest archive (*.zip *.json);;"
                "All files (*)"
            ),
        )

        if path:
            self.pinterest_archive_path.setText(path)

    def choose_meta_archive(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите архив Meta",
            str(Path.home() / "Downloads"),
            (
                "Meta archive (*.zip *.json *.html);;"
                "All files (*)"
            ),
        )

        if path:
            self.meta_path.setText(path)

    # -----------------------------------------------------
    # Preview process
    # -----------------------------------------------------

    def start_preview(self) -> None:
        if self.process is not None:
            return

        if self.selected_folders_toggle.isChecked():
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

        if self.active_source == "pinterest":
            self.start_pinterest_preview()
            return

        if self.meta_source.isChecked():
            if not self.meta_path.text():
                QMessageBox.warning(
                    self,
                    "Архив не выбран",
                    "Сначала выберите архив Meta.",
                )
                return

            QMessageBox.information(
                self,
                "Архив Meta",
                "Интерфейс готов. Парсер архива Meta "
                "подключим следующим этапом.",
            )
            return

        username = self.username.text().strip()

        if not username:
            QMessageBox.warning(
                self,
                "Не указан аккаунт",
                "Введите Instagram-никнейм после @.",
            )
            return

        browser_code = self.browser.currentData()

        # UNLIMITED_SAVED_RETRIEVAL_V61
        # The full Saved mode uses gallery-dl cursor pagination
        # without --post-range. A zero limit is an explicit
        # sentinel and is ignored by discovery in full mode.
        if self.recent_search.isChecked():
            search_mode = "recent"
            limit = self.recent_limit.value()
        elif self.smart_search.isChecked():
            search_mode = "smart"
            # V6.2: unlimited stream; backend stops when the
            # first fully known Saved publication is reached.
            limit = 0
        else:
            search_mode = "full"
            limit = 0

        arguments = [
            "-m",
            "app.instagram_sync",
            "--username",
            username,
            "--browser",
            str(browser_code),
            "--speed-profile",
            str(self.download_speed.currentData()),
            "--search-mode",
            search_mode,
            "--limit",
            str(limit),
            "--batch-size",
            "50",
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

        self.search_duration_info.setVisible(False)
        self.search_duration_info.setToolTip(
            "Поиск выполняется…"
        )

        self._operation_last_percent = 0
        self.operation_progress.setVisible(True)
        self.operation_progress.setRange(0, 0)
        self.operation_progress.setFormat(
            "Подготовка сканирования…"
        )
        self.thumbnail_controller.clear()
        self.table.setRowCount(0)
        self.preview_items = []
        self.naming_group.setVisible(False)
        self.import_button.setEnabled(False)
        self.log.clear()
        self.process_output = ""
        self.process_started_at = (
            datetime.now().timestamp()
        )

        self.status.setText(
            "Ищем публикации и проверяем Eagle…"
        )
        self.summary.setText("Найдено: —")
        self.search_button.setEnabled(False)

        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(PROJECT))
        self.process.setProgram(str(PYTHON))
        self.process.setArguments(arguments)
        self.process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.process.readyReadStandardOutput.connect(
            self.read_process_output
        )
        self.process.finished.connect(
            self.preview_finished
        )
        self.process.start()

    def start_pinterest_preview(self) -> None:
        if not self.browser_source.isChecked():
            QMessageBox.information(
                self,
                "Архив Pinterest",
                "Парсер официального архива Pinterest "
                "пока не подключён.",
            )
            return

        username = (
            self.pinterest_username.text()
            .strip()
            .lstrip("@")
        )

        if not username:
            QMessageBox.warning(
                self,
                "Не указан аккаунт",
                "Введите имя Pinterest-аккаунта после @.",
            )
            return

        url = (
            "https://www.pinterest.com/"
            + username
            + "/"
        )

        job_id = (
            "pinterest-gui-preview-"
            + datetime.now().strftime(
                "%Y%m%d-%H%M%S"
            )
        )
        output = (
            Path("/tmp")
            / f"{job_id}.json"
        )

        arguments = [
            "-m",
            "app.source_download_staging",
            "preview",
            "--source-code",
            "pinterest",
            "--job-id",
            job_id,
            "--url",
            url,
            "--limit",
            "1",
            "--cookies-browser",
            str(
                self.pinterest_browser.currentData()
                or "chrome"
            ),
            "--output",
            str(output),
        ]

        self.active_operation_source = "pinterest"
        self.pinterest_preview_output = output
        self.process_output = ""
        self._process_line_buffer = ""
        self.log.clear()
        self.table.setRowCount(0)
        self.preview_items = []
        self.import_button.setEnabled(False)
        self.clear_results_button.setEnabled(False)
        self.search_button.setEnabled(False)
        self.status.setText(
            "Проверяем план Pinterest"
        )
        self.summary.setText("Проверка ссылки")
        self.operation_progress.setVisible(True)
        self.operation_progress.setRange(0, 0)
        self.operation_progress.setFormat(
            "Pinterest preview"
        )
        self.process_started_at = (
            datetime.now().timestamp()
        )

        self.process = QProcess(self)
        self.process.setWorkingDirectory(
            str(PROJECT)
        )
        self.process.setProgram(str(PYTHON))
        self.process.setArguments(arguments)
        self.process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.process.readyReadStandardOutput.connect(
            self.read_process_output
        )
        self.process.finished.connect(
            self.preview_finished
        )
        self.process.start()

    def finish_pinterest_preview(
        self,
        exit_code: int,
    ) -> None:
        output = self.pinterest_preview_output
        self.active_operation_source = None
        self.operation_progress.setVisible(False)

        if (
            exit_code != 0
            or output is None
            or not output.is_file()
        ):
            self.status.setText(
                "Pinterest preview завершился с ошибкой"
            )
            self.log.setVisible(True)
            self.log_button.setChecked(True)
            return

        try:
            payload = json.loads(
                output.read_text(encoding="utf-8")
            )
        except Exception as error:
            self.status.setText(
                "Не удалось прочитать Pinterest preview"
            )
            self.log.append(str(error))
            self.log.setVisible(True)
            self.log_button.setChecked(True)
            return

        status = str(
            payload.get("status") or ""
        )
        url = str(
            payload.get("source_url")
            or self.pinterest_url.text().strip()
        )

        if status != "PREVIEW_ONLY":
            self.status.setText(
                f"Pinterest: {status or 'неизвестный статус'}"
            )
            self.log.setVisible(True)
            self.log_button.setChecked(True)
            return

        self.table.setRowCount(1)

        selector = LightweightRowSelectorItem()
        selector.setCheckState(
            Qt.CheckState.Unchecked
        )
        selector.setFlags(
            selector.flags()
            & ~Qt.ItemFlag.ItemIsEnabled
        )
        selector.setToolTip(
            "Импорт включим после проверки интерфейса."
        )
        self.table.setItem(0, 0, selector)

        values = [
            "Pinterest",
            "Ссылка",
            "Preview",
            url,
            "Eagle не изменён",
        ]

        for column, value in enumerate(
            values,
            start=1,
        ):
            cell = QTableWidgetItem(value)
            self.table.setItem(
                0,
                column,
                cell,
            )

        self.preview_items = []
        self.select_all.blockSignals(True)
        self.select_all.setChecked(False)
        self.select_all.blockSignals(False)
        self.import_button.setEnabled(False)
        self.clear_results_button.setEnabled(True)
        self.summary.setText("Pinterest: 1 ссылка")
        self.status.setText(
            "Pinterest preview готов"
        )
        self.log.setPlainText(
            "Источник: Pinterest\n"
            f"Ссылка: {url}\n"
            "Сеть: не использовалась\n"
            "Eagle: без изменений\n"
            "SQLite: без изменений\n"
        )

    def write_process_control(
        self,
        command: str,
    ) -> None:
        temporary = self.control_file.with_suffix(
            ".json.tmp"
        )
        temporary.write_text(
            json.dumps(
                {
                    "command": command,
                    "updated_at": (
                        datetime.now().isoformat()
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.control_file)

    def pause_import(self) -> None:
        if self.process is None:
            return

        self.write_process_control("pause")
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.status.setText("Загрузка приостановлена")

    def resume_import(self) -> None:
        if self.process is None:
            return

        self.write_process_control("resume")
        self.pause_button.setEnabled(True)
        self.resume_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText("Загрузка продолжается")

    def stop_import(self) -> None:
        if self.process is None:
            return

        self.write_process_control("stop")
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.status.setText(
            "Остановка загрузки; очередь сохраняется"
        )

    def start_operation_progress(self) -> None:
        self._operation_last_percent = 0
        self.operation_progress.setStyleSheet("""
            QProgressBar#operationProgress {
                background: #222630;
                border: 1px solid #363c49;
                border-radius: 7px;
                color: #f2f3f6;
                font-size: 11px;
                text-align: center;
            }

            QProgressBar#operationProgress::chunk {
                background: #7569e8;
                border-radius: 6px;
            }
        """)
        self.operation_progress.setRange(0, 100)
        self.operation_progress.setValue(2)
        self.operation_progress.setFormat(
            "2% · Подготовка импорта"
        )
        self.operation_progress.setVisible(True)

    def set_normal_progress_style(self) -> None:
        self.operation_progress.setStyleSheet("""
            QProgressBar#operationProgress {
                background: #222630;
                border: 1px solid #363c49;
                border-radius: 7px;
                color: #f2f3f6;
                font-size: 11px;
                text-align: center;
            }

            QProgressBar#operationProgress::chunk {
                background: #7569e8;
                border-radius: 6px;
            }
        """)

    def set_paused_progress_style(self) -> None:
        self.operation_progress.setStyleSheet("""
            QProgressBar#operationProgress {
                background: #3b3219;
                border: 1px solid #d8a936;
                border-radius: 7px;
                color: #ffffff;
                font-size: 11px;
                text-align: center;
            }

            QProgressBar#operationProgress::chunk {
                background: #d8a936;
                border-radius: 6px;
            }
        """)

    def set_stopped_progress_style(self) -> None:
        self.operation_progress.setStyleSheet("""
            QProgressBar#operationProgress {
                background: #24272d;
                border: 1px solid #646b76;
                border-radius: 7px;
                color: #d7d9de;
                font-size: 11px;
                text-align: center;
            }

            QProgressBar#operationProgress::chunk {
                background: #646b76;
                border-radius: 6px;
            }
        """)

    def update_operation_progress(
        self,
        payload: dict,
    ) -> None:
        if not self.operation_progress.isVisible():
            return

        state = str(
            payload.get("state") or ""
        ).strip().upper()

        stage = str(
            payload.get("stage") or ""
        ).strip()

        if state in {
            "CONNECTION_LOST",
            "PAUSED_BY_USER",
            "RETRYING_NETWORK",
        }:
            self.set_paused_progress_style()

            # Preserve the current percentage. Pause and
            # connection-loss events are state changes, not new
            # progress values.
            current_value = max(
                0,
                self.operation_progress.value(),
            )
            self.operation_progress.setRange(0, 100)
            self.operation_progress.setValue(
                current_value
            )
            self.operation_progress.setFormat(
                stage or "Загрузка приостановлена"
            )
            self.status.setText(
                stage or "Загрузка приостановлена"
            )
            return

        if state == "RESUMED":
            self.set_normal_progress_style()
            self.operation_progress.setFormat(
                stage or "Загрузка продолжается"
            )
            self.status.setText(
                stage or "Загрузка продолжается"
            )
            # Do not apply the synthetic zero percent emitted by
            # the process supervisor.
            return

        if state in {
            "STOP_REQUESTED",
            "STOPPED_BY_USER",
        }:
            self.set_stopped_progress_style()

            current_value = max(
                0,
                self.operation_progress.value(),
            )
            self.operation_progress.setRange(0, 100)
            self.operation_progress.setValue(
                current_value
            )
            self.operation_progress.setFormat(
                stage or "Процесс остановлен"
            )
            self.status.setText(
                stage or "Процесс остановлен"
            )
            return

        # A genuine progress event means that a request succeeded.
        # Only then clear the yellow reconnect state.
        self.set_normal_progress_style()

        if bool(payload.get("indeterminate")):
            elapsed = int(
                payload.get("elapsed_seconds") or 0
            )
            posts_scanned = int(
                payload.get("posts_scanned")
                or payload.get("current")
                or 0
            )
            minutes, seconds = divmod(
                max(0, elapsed),
                60,
            )

            self.operation_progress.setRange(0, 0)
            self.operation_progress.setFormat(
                f"{stage} — найдено: {posts_scanned}; "
                f"{minutes:02d}:{seconds:02d}"
            )
            self.status.setText(
                f"{stage} — найдено {posts_scanned}"
            )
            self.summary.setText(
                f"Найдено: {posts_scanned}"
            )
            return

        try:
            percent = int(
                payload.get("percent", 0)
            )
        except (TypeError, ValueError):
            return

        percent = max(0, min(100, percent))

        # Nested staging/Eagle modules can report their own
        # percentages. Never move the complete operation bar
        # backwards.
        percent = max(
            self._operation_last_percent,
            percent,
        )
        self._operation_last_percent = percent

        current = payload.get("current")
        total = payload.get("total")
        suffix = ""

        if current is not None and total is not None:
            suffix = f"  {current}/{total}"

        self.operation_progress.setRange(0, 100)
        self.operation_progress.setValue(percent)
        self.operation_progress.setFormat(
            f"{percent}%  {stage}{suffix}"
        )

        if stage:
            self.status.setText(stage)

        if percent >= 100:
            self.operation_progress.setStyleSheet("""
                QProgressBar#operationProgress {
                    background: #173128;
                    border: 1px solid #2f9e72;
                    border-radius: 7px;
                    color: #ffffff;
                    font-size: 11px;
                    text-align: center;
                }

                QProgressBar#operationProgress::chunk {
                    background: #169b70;
                    border-radius: 6px;
                }
            """)

    def fail_operation_progress(self) -> None:
        self.operation_progress.setVisible(True)
        self.show_operation_error(
            "Ошибка операции",
            (
                "Операция не была завершена. "
                "Проверьте технический журнал."
            ),
        )
        self.operation_progress.setFormat(
            "Остановлено · требуется проверка журнала"
        )
        self.operation_progress.setStyleSheet("""
            QProgressBar#operationProgress {
                background: #35221f;
                border: 1px solid #d06455;
                border-radius: 7px;
                color: #ffffff;
                font-size: 11px;
                text-align: center;
            }

            QProgressBar#operationProgress::chunk {
                background: #b84c40;
                border-radius: 6px;
            }
        """)

    def append_process_log_line(
        self,
        line: str,
    ) -> None:
        self.process_output += line

        self.log.moveCursor(
            self.log.textCursor().MoveOperation.End
        )
        self.log.insertPlainText(line)
        self.log.moveCursor(
            self.log.textCursor().MoveOperation.End
        )

    def consume_process_line(
        self,
        line: str,
    ) -> None:
        stripped = line.strip()

        if stripped.startswith("RS_PROGRESS "):
            raw_payload = stripped[len("RS_PROGRESS "):]

            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                self.append_process_log_line(line)
                return

            if isinstance(payload, dict):
                self.update_operation_progress(payload)
                return

        self.append_process_log_line(line)

    def read_process_output(self) -> None:
        if self.process is None:
            return

        text = bytes(
            self.process.readAllStandardOutput()
        ).decode("utf-8", errors="replace")

        self._process_line_buffer += text

        while "\n" in self._process_line_buffer:
            line, self._process_line_buffer = (
                self._process_line_buffer.split(
                    "\n",
                    1,
                )
            )

            self.consume_process_line(line + "\n")


    def preview_finished(
        self,
        exit_code: int,
        _exit_status,
    ) -> None:
        completed_search_duration_seconds = max(
            0,
            int(
                round(
                    datetime.now().timestamp()
                    - self.process_started_at
                )
            ),
        )

        completed_minutes, completed_seconds = divmod(
            completed_search_duration_seconds,
            60,
        )

        completed_duration_text = (
            f"{completed_minutes:02d}:"
            f"{completed_seconds:02d}"
        )

        operation_source = (
            self.active_operation_source
        )

        self.process = None
        self.search_button.setEnabled(True)

        if operation_source == "containers":
            self.finish_container_preview(
                exit_code
            )
            return
        if operation_source == "pinterest":
            self.finish_pinterest_preview(
                exit_code
            )
            return
        if exit_code == 0:
            self.search_duration_info.setToolTip(
                "Длительность поиска — "
                f"{completed_duration_text}"
            )
            self.search_duration_info.setVisible(True)
        else:
            self.search_duration_info.setVisible(False)


        if exit_code != 0:
            self.status.setText(
                "Поиск завершился с ошибкой"
            )
            self.log.setVisible(True)
            self.log_button.setChecked(True)

            return

        report = self.latest_report()

        if report is None:
            self.status.setText(
                "Не удалось прочитать отчёт"
            )
            return

        result = report.get("result", report)
        status = result.get("status")

        if status == "NUMBERING_COLLISIONS_FOUND":
            self.status.setText(
                "Автоматические номера уже заняты"
            )
            self.populate_collisions(
                result.get("collisions", [])
            )
            return

        if status == "NO_NEW_POSTS":
            self.table.setRowCount(0)
            self.preview_items = []

            self.select_all.blockSignals(True)
            self.select_all.setChecked(False)
            self.select_all.blockSignals(False)

            self.naming_group.setVisible(False)
            self.reset_all_edits.setVisible(False)
            self.clear_results_button.setEnabled(False)

            self.status.setText(
                "Новых публикаций не найдено"
            )
            self.summary.setText("Найдено: 0")
            self.update_import_button()
            return

        if status != "NEW_POSTS_FOUND_PREVIEW":
            self.status.setText(
                str(status or "Поиск завершён")
            )
            return

        items = result.get(
            "numbering_preview",
            [],
        )

        items = self.apply_author_filters(items)

        self.preview_items = items
        self.populate_results(items)

        total = result.get(
            "new_posts_available",
            len(items),
        )

        initially_selected = min(
            50,
            len(items),
        )

        self.summary.setText(
            f"Найдено: {total}  "
            f"Показано: {len(items)}  "
            f"Выбрано: {initially_selected}/50"
        )
        self.status.setText(
            "Поиск завершён — выберите публикации"
        )

        if items:
            first_number = int(
                items[0].get("post_number") or 1
            )
            self.naming_start.setValue(first_number)
            self.naming_group.setVisible(True)
            self.refresh_generated_names()

    def latest_report(self) -> dict | None:
        candidates = sorted(
            REPORTS.glob(
                "instagram_sync_sync_*.json"
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

        for path in candidates:
            if (
                path.stat().st_mtime + 2
                < self.process_started_at
            ):
                continue

            try:
                return json.loads(
                    path.read_text(encoding="utf-8")
                )
            except Exception:
                continue

        return None

    # -----------------------------------------------------
    # Results
    # -----------------------------------------------------

    def parse_authors(self, text: str) -> set[str]:
        return {
            value.strip().lower().lstrip("@")
            for value in text.split(",")
            if value.strip()
        }

    def apply_author_filters(
        self,
        items: list[dict],
    ) -> list[dict]:
        if not self.filters_group.isChecked():
            return items

        included = self.parse_authors(
            self.include_authors.text()
        )
        excluded = self.parse_authors(
            self.exclude_authors.text()
        )

        result = []

        for item in items:
            username = str(
                item.get("username") or ""
            ).lower().lstrip("@")

            if included and username not in included:
                continue

            if username in excluded:
                continue

            try:
                component_count = int(
                    item.get("component_count")
                    or item.get(
                        "total_component_count"
                    )
                    or 1
                )
            except (TypeError, ValueError):
                component_count = 1

            type_text = " ".join([
                str(item.get("publication_type") or ""),
                str(item.get("post_type") or ""),
                str(item.get("type") or ""),
                str(item.get("canonical_url") or ""),
            ]).lower()

            is_reel = (
                "reel" in type_text
                or "/reel/" in type_text
            )
            is_carousel = component_count > 1

            if is_carousel:
                if not self.include_carousels.isChecked():
                    continue
            elif is_reel:
                if not self.include_reels.isChecked():
                    continue
            elif not self.include_posts.isChecked():
                continue

            result.append(item)

        return result

    def clear_found_results(self) -> None:
        """
        Clear only the current result snapshot.

        This method does not modify Eagle, SQLite, downloads,
        staging folders, reports, or discovery data.
        """
        if self.process is not None:
            QMessageBox.information(
                self,
                "Операция выполняется",
                "Дождитесь завершения текущей операции.",
            )
            return

        self.thumbnail_controller.clear()
        self.table.setRowCount(0)
        self.preview_items = []

        self.select_all.blockSignals(True)
        self.select_all.setChecked(False)
        self.select_all.blockSignals(False)

        self.naming_group.setVisible(False)
        self.reset_all_edits.setVisible(False)
        self.clear_results_button.setEnabled(False)

        self.summary.setText("Найдено: 0")
        self.status.setText(
            "Список найденных публикаций очищен"
        )
        self.update_import_button()

    def mark_active_import_rows_completed(self) -> None:
        """
        Update the current table after a verified import.

        Completed posts are locked. A partial carousel remains
        available when its remaining components and media IDs can
        be updated safely from the current discovery snapshot.
        """

        # CONTINUE_PARTIAL_WITHOUT_NEW_SEARCH_V1
        active_post_ids = {
            str(value).strip()
            for value in getattr(
                self,
                "_active_import_post_ids",
                set(),
            )
            if str(value).strip()
        }

        if not active_post_ids:
            self.clear_results_button.setEnabled(
                self.table.rowCount() > 0
            )
            return

        partial_rows_updated = []

        for row, item in enumerate(self.preview_items):
            post_id = str(
                item.get("post_id") or ""
            ).strip()

            if post_id not in active_post_ids:
                continue

            selected_now = set(
                self.selected_component_indexes(item)
            )

            # CONTINUE_NEW_PARTIAL_WITHOUT_SEARCH_V2
            try:
                component_count = int(
                    item.get("component_count")
                    or item.get("total_component_count")
                    or 1
                )
            except (TypeError, ValueError):
                component_count = 1

            previously_imported = set()

            for value in item.get(
                "imported_component_numbers",
                [],
            ):
                try:
                    component_index = int(value)
                except (TypeError, ValueError):
                    continue

                if component_index > 0:
                    previously_imported.add(
                        component_index
                    )

            updated_imported = (
                previously_imported | selected_now
            )

            imported_media_ids = {
                str(value).strip()
                for value in item.get(
                    "imported_media_ids",
                    [],
                )
                if str(value).strip()
            }

            media_id_by_component = {}

            component_items = item.get(
                "component_items",
                [],
            )

            if isinstance(component_items, list):
                for fallback_index, component in enumerate(
                    component_items,
                    start=1,
                ):
                    if not isinstance(component, dict):
                        continue

                    raw_index = (
                        component.get("component_index")
                        or component.get("num")
                        or component.get("index")
                        or fallback_index
                    )

                    try:
                        component_index = int(raw_index)
                    except (TypeError, ValueError):
                        continue

                    media_id = str(
                        component.get("media_id")
                        or component.get("id")
                        or component.get("pk")
                        or ""
                    ).strip()

                    if media_id:
                        media_id_by_component[
                            component_index
                        ] = media_id

            newly_imported_media_ids = {
                media_id_by_component[component_index]
                for component_index in selected_now
                if component_index in media_id_by_component
            }

            all_new_media_ids_known = (
                len(newly_imported_media_ids)
                == len(selected_now)
            )

            all_component_numbers = set(
                range(1, component_count + 1)
            )

            remaining_components = sorted(
                all_component_numbers
                - updated_imported
            )

            # Preserve the publication number for both:
            # 1. a carousel that was partial before this operation;
            # 2. a new carousel that became partial in this operation.
            assigned_post_number = None

            try:
                assigned_post_number = int(
                    item.get("existing_post_number")
                )
            except (TypeError, ValueError):
                assigned_post_number = None

            candidate_name_texts = []

            for key in (
                "generated_name",
                "generated_names",
                "name",
                "names",
            ):
                value = item.get(key)

                if isinstance(value, str):
                    candidate_name_texts.append(value)
                elif isinstance(value, (list, tuple)):
                    candidate_name_texts.extend(
                        str(part)
                        for part in value
                        if part is not None
                    )

            # The generated-name table cell is still present when
            # the verified import finishes. Scan cells instead of
            # depending on a fixed column number.
            for column in range(self.table.columnCount()):
                cell = self.table.item(row, column)

                if cell is not None:
                    candidate_name_texts.append(cell.text())

            if not assigned_post_number:
                for candidate_text in candidate_name_texts:
                    candidate_text = str(candidate_text)

                    if "instpoporder-" not in candidate_text:
                        continue

                    tail = candidate_text.rsplit(
                        "instpoporder-",
                        1,
                    )[1]

                    number_text = tail.split("-", 1)[0].strip()

                    try:
                        parsed_number = int(number_text)
                    except (TypeError, ValueError):
                        continue

                    if parsed_number > 0:
                        assigned_post_number = parsed_number
                        break

            can_continue_locally = (
                component_count > 1
                and bool(remaining_components)
                and bool(selected_now)
                and all_new_media_ids_known
                and assigned_post_number is not None
                and assigned_post_number > 0
            )

            selector = self.row_selector_item(row)

            structure_item = self.table.item(
                row,
                3,
            )

            if can_continue_locally:
                imported_media_ids.update(
                    newly_imported_media_ids
                )

                item["imported_component_numbers"] = sorted(
                    updated_imported
                )
                item["imported_media_ids"] = sorted(
                    imported_media_ids
                )
                item["available_component_numbers"] = list(
                    remaining_components
                )
                item["selected_components"] = list(
                    remaining_components
                )
                item["resume_partial"] = True
                item["existing_post_number"] = (
                    assigned_post_number
                )
                item["total_component_count"] = (
                    component_count
                )
                item["_imported_this_session"] = False

                if selector is not None:
                    self.set_row_selector_state(
                        row,
                        checked=False,
                        enabled=True,
                        tooltip=(
                            "Часть карусели добавлена. "
                            "Можно выбрать строку и скачать "
                            "оставшиеся компоненты без нового поиска."
                        ),
                    )

                if structure_item is not None:
                    structure_item.setFlags(
                        structure_item.flags()
                        | Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                    )
                    structure_item.setData(
                        LightweightStructureDelegate
                        .ROLE_CAROUSEL,
                        True,
                    )
                    structure_item.setData(
                        LightweightStructureDelegate
                        .ROLE_CONTROL_ENABLED,
                        True,
                    )
                    structure_item.setText(
                        "Частично · в Eagle "
                        f"{len(updated_imported)}/"
                        f"{component_count} · осталось: "
                        + ", ".join(
                            map(str, remaining_components)
                        )
                    )
                    structure_item.setToolTip(
                        "Нажмите, чтобы выбрать оставшиеся "
                        "компоненты карусели."
                    )

                partial_rows_updated.append(row)
                continue

            # Fully completed post, or a snapshot that cannot
            # be updated safely without another search.
            item["_imported_this_session"] = True

            if selector is not None:
                selector_tooltip = (
                    "Публикация полностью добавлена "
                    "в этой операции."
                    if not remaining_components
                    else (
                        "Импорт выполнен, но локальное состояние "
                        "компонентов требует нового поиска."
                    )
                )

                self.set_row_selector_state(
                    row,
                    checked=False,
                    enabled=False,
                    tooltip=selector_tooltip,
                )

            if structure_item is not None:
                if component_count > 1:
                    structure_item.setData(
                        LightweightStructureDelegate
                        .ROLE_CAROUSEL,
                        True,
                    )

                    if not remaining_components:
                        structure_item.setText(
                            f"Добавлено · {component_count}/"
                            f"{component_count}"
                        )
                        structure_item.setToolTip(
                            "Все компоненты находятся в Eagle."
                        )
                    else:
                        structure_item.setText(
                            "Добавлено · обновите поиск"
                        )
                        structure_item.setToolTip(
                            "Не удалось безопасно обновить "
                            "media_id компонентов из текущего "
                            "снимка. Выполните новый поиск."
                        )
                else:
                    structure_item.setText("Добавлено")
                    structure_item.setToolTip(
                        "Публикация находится в Eagle."
                    )

                structure_item.setData(
                    LightweightStructureDelegate
                    .ROLE_CONTROL_ENABLED,
                    False,
                )
                structure_item.setFlags(
                    structure_item.flags()
                    & ~Qt.ItemFlag.ItemIsEnabled
                )

        self._active_import_post_ids = set()

        self.select_all.blockSignals(True)
        self.select_all.setChecked(False)
        self.select_all.blockSignals(False)

        self.clear_results_button.setEnabled(
            self.table.rowCount() > 0
        )

        if partial_rows_updated:
            self.refresh_generated_names()

            self.status.setText(
                "Импорт завершён · часть карусели "
                "можно продолжить без нового поиска"
            )

        self.update_import_button()

    def populate_results(
        self,
        items: list[dict],
    ) -> None:
        # UNLIMITED_SAVED_RETRIEVAL_V61
        # Avoid repeated sorting and repainting while a large Saved
        # result set is being converted into table items.
        self._populating_results = True
        sorting_was_enabled = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(items))
        self.clear_results_button.setEnabled(bool(items))

        for row, item in enumerate(items):
            selector = LightweightRowSelectorItem()
            selector.setCheckState(
                Qt.CheckState.Checked
                if row < 50
                else Qt.CheckState.Unchecked
            )

            self.table.setItem(
                row,
                0,
                selector,
            )

            component_count = int(
                item.get("component_count")
                or item.get("components")
                or 1
            )

            raw_extensions = item.get("extensions")
            extensions = (
                raw_extensions
                if isinstance(raw_extensions, dict)
                else {}
            )

            image_count = sum(
                int(extensions.get(extension) or 0)
                for extension in (
                    "jpg",
                    "jpeg",
                    "png",
                    "webp",
                    "gif",
                )
            )
            video_count = sum(
                int(extensions.get(extension) or 0)
                for extension in (
                    "mp4",
                    "mov",
                    "webm",
                    "mkv",
                )
            )

            if image_count and video_count:
                media_type = "Смешанный"
            elif video_count:
                media_type = "Видео"
            elif image_count:
                media_type = "Изображение"
            else:
                media_type = "Публикация"

            component_items = item.get(
                "component_items"
            )

            if not isinstance(component_items, list):
                component_items = []

            item["component_items"] = component_items

            preview_components = [
                component
                for component in component_items
                if (
                    isinstance(component, dict)
                    and component.get("preview_url")
                )
            ]

            preview_components.sort(
                key=lambda component: int(
                    component.get("component_index")
                    or 1
                )
            )

            preview_component = (
                preview_components[0]
                if preview_components
                else None
            )

            if preview_component is not None:
                preview_url = str(
                    preview_component.get("preview_url")
                    or ""
                ).strip()
                preview_key = str(
                    preview_component.get("media_id")
                    or item.get("post_id")
                    or preview_url
                ).strip()

                selector.setData(
                    ROLE_THUMBNAIL_KEY,
                    preview_key,
                )
                selector.setData(
                    ROLE_THUMBNAIL_URL,
                    preview_url,
                )

            component_indexes = []

            for fallback_index, component in enumerate(
                component_items,
                start=1,
            ):
                try:
                    component_index = int(
                        component.get("component_index")
                        or fallback_index
                    )
                except (TypeError, ValueError):
                    component_index = fallback_index

                component_indexes.append(component_index)

            if not component_indexes:
                component_indexes = list(
                    range(1, component_count + 1)
                )

            existing_selection = item.get(
                "selected_components"
            )

            if not isinstance(existing_selection, list):
                item["selected_components"] = list(
                    component_indexes
                )

            values = [
                str(item.get("username") or ""),
                media_type,
            ]

            for column, value in enumerate(
                values,
                start=1,
            ):
                cell = QTableWidgetItem(value)

                if column == 2:
                    cell.setTextAlignment(
                        Qt.AlignmentFlag.AlignCenter
                    )

                self.table.setItem(
                    row,
                    column,
                    cell,
                )

            structure_cell = QTableWidgetItem()
            structure_cell.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter
            )
            structure_cell.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )

            if component_count > 1:
                structure_cell.setData(
                    LightweightStructureDelegate.ROLE_CAROUSEL,
                    True,
                )
                structure_cell.setData(
                    LightweightStructureDelegate
                    .ROLE_CONTROL_ENABLED,
                    True,
                )
            else:
                structure_cell.setText("Одиночный")
                structure_cell.setData(
                    LightweightStructureDelegate.ROLE_CAROUSEL,
                    False,
                )
                structure_cell.setData(
                    LightweightStructureDelegate
                    .ROLE_CONTROL_ENABLED,
                    False,
                )

            self.table.setItem(
                row,
                3,
                structure_cell,
            )

            if component_count > 1:
                self.update_structure_button(row)

            name_editor = LightweightTextItem(
                on_change=self.update_reset_all_button
            )
            description_editor = LightweightTextItem(
                on_change=self.update_reset_all_button
            )

            self.table.setItem(
                row,
                4,
                name_editor,
            )
            self.table.setItem(
                row,
                5,
                description_editor,
            )

        # Keep _populating_results enabled while generated text is
        # assigned, preventing thousands of individual row-resize
        # operations.
        self.thumbnail_controller.reset_for_results()
        self.refresh_generated_names()

        self._populating_results = False
        self.table.setSortingEnabled(sorting_was_enabled)
        self.table.setUpdatesEnabled(True)
        self.table.viewport().update()
        self.update_import_button()

    def populate_collisions(
        self,
        collisions: list[dict],
    ) -> None:
        self.table.setRowCount(len(collisions))
        self.clear_results_button.setEnabled(
            bool(collisions)
        )

        for row, collision in enumerate(collisions):
            values = [
                "—",
                "—",
                "Занятый номер",
                str(collision.get("name") or ""),
                "Конфликт нумерации",
            ]

            for column, value in enumerate(
                values,
                start=1,
            ):
                self.table.setItem(
                    row,
                    column,
                    QTableWidgetItem(value),
                )

    def selected_component_indexes(
        self,
        item: dict,
    ) -> list[int]:
        values = item.get("selected_components")
        imported = {
            int(value)
            for value in item.get(
                "imported_component_numbers",
                [],
            )
            if str(value).isdigit()
        }

        if isinstance(values, list):
            result = []

            for value in values:
                try:
                    index = int(value)
                except (TypeError, ValueError):
                    continue

                if (
                    index > 0
                    and index not in imported
                    and index not in result
                ):
                    result.append(index)

            return sorted(result)

        available = item.get(
            "available_component_numbers"
        )

        if isinstance(available, list):
            return sorted({
                int(value)
                for value in available
                if (
                    str(value).isdigit()
                    and int(value) not in imported
                )
            })

        component_count = int(
            item.get("component_count") or 1
        )

        return [
            index
            for index in range(
                1,
                component_count + 1,
            )
            if index not in imported
        ]

    def update_structure_button(
        self,
        row: int,
    ) -> None:
        if row < 0 or row >= len(self.preview_items):
            return

        item = self.preview_items[row]
        component_count = int(
            item.get("component_count") or 1
        )

        structure_item = self.table.item(row, 3)

        if (
            structure_item is None
            or component_count <= 1
        ):
            return

        structure_item.setData(
            LightweightStructureDelegate.ROLE_CAROUSEL,
            True,
        )
        structure_item.setData(
            LightweightStructureDelegate
            .ROLE_CONTROL_ENABLED,
            bool(
                structure_item.flags()
                & Qt.ItemFlag.ItemIsEnabled
            ),
        )

        selected_count = len(
            self.selected_component_indexes(item)
        )

        imported_count = len({
            int(value)
            for value in item.get(
                "imported_component_numbers",
                [],
            )
            if str(value).isdigit()
        })

        if imported_count:
            structure_item.setText(
                f"Карусель · выбрано "
                f"{selected_count} · в Eagle "
                f"{imported_count}/{component_count}"
            )
        else:
            structure_item.setText(
                f"Карусель · выбрано "
                f"{selected_count}/{component_count}"
            )

        structure_item.setToolTip(
            "Выбрать отдельные изображения или видео"
        )

        self.table.viewport().update(
            self.table.visualItemRect(structure_item)
        )

    def open_carousel_selection(
        self,
        row: int,
    ) -> None:
        if row < 0 or row >= len(self.preview_items):
            return

        item = self.preview_items[row]

        if int(item.get("component_count") or 1) <= 1:
            return

        dialog = CarouselSelectionDialog(
            item,
            self,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        item["selected_components"] = (
            dialog.selected_components()
        )

        self.update_structure_button(row)
        self.refresh_generated_names()
        self.update_import_button()

    def row_selector_item(
        self,
        row: int,
    ) -> LightweightRowSelectorItem | None:
        item = self.table.item(row, 0)

        if isinstance(
            item,
            LightweightRowSelectorItem,
        ):
            return item

        return None

    def row_selector_enabled(
        self,
        row: int,
    ) -> bool:
        selector = self.row_selector_item(row)

        return bool(
            selector is not None
            and (
                selector.flags()
                & Qt.ItemFlag.ItemIsEnabled
            )
        )

    def row_is_selected(
        self,
        row: int,
    ) -> bool:
        selector = self.row_selector_item(row)

        return bool(
            selector is not None
            and self.row_selector_enabled(row)
            and selector.checkState()
            == Qt.CheckState.Checked
        )

    def set_row_selector_state(
        self,
        row: int,
        *,
        checked: bool,
        enabled: bool,
        tooltip: str,
    ) -> None:
        selector = self.row_selector_item(row)

        if selector is None:
            return

        previous_block_state = (
            self.table.blockSignals(True)
        )

        try:
            flags = selector.flags()

            if enabled:
                flags |= Qt.ItemFlag.ItemIsEnabled
            else:
                flags &= ~Qt.ItemFlag.ItemIsEnabled

            selector.setFlags(flags)
            selector.setCheckState(
                Qt.CheckState.Checked
                if checked
                else Qt.CheckState.Unchecked
            )
            selector.setToolTip(tooltip)
        finally:
            self.table.blockSignals(
                previous_block_state
            )

    def selected_media_count(self) -> int:
        total = 0

        for row in range(self.table.rowCount()):
            if (
                not self.row_is_selected(row)
                or row >= len(self.preview_items)
            ):
                continue

            total += len(
                self.selected_component_indexes(
                    self.preview_items[row]
                )
            )

        return total

    # GLOBAL_COMMITTED_TABLE_UNDO_V52_FIXED
    def clear_text_edit_history(
        self,
        *_args,
    ) -> None:
        undo_stack = getattr(
            self,
            "_text_undo_stack",
            None,
        )
        redo_stack = getattr(
            self,
            "_text_redo_stack",
            None,
        )

        if undo_stack is not None:
            undo_stack.clear()

        if redo_stack is not None:
            redo_stack.clear()

    def record_committed_text_edit(
        self,
        item,
        row,
        column,
        old_text,
        new_text,
    ) -> None:
        if getattr(
            self,
            "_applying_text_history",
            False,
        ):
            return

        old_text = str(old_text or "")
        new_text = str(new_text or "")

        if old_text == new_text:
            return

        # Prefer the item object because it stays associated with
        # the same publication if the user sorts the table.
        entry = (
            item,
            int(row),
            int(column),
            old_text,
            new_text,
        )

        self._text_undo_stack.append(entry)

        limit = max(
            1,
            int(
                getattr(
                    self,
                    "_text_history_limit",
                    500,
                )
            ),
        )

        if len(self._text_undo_stack) > limit:
            del self._text_undo_stack[:-limit]

        # A new edit creates a new history branch.
        self._text_redo_stack.clear()

    def apply_text_history_entry(
        self,
        entry,
        use_new_text,
    ) -> bool:
        (
            item,
            original_row,
            column,
            old_text,
            new_text,
        ) = entry

        target_text = (
            new_text
            if use_new_text
            else old_text
        )

        target_item = item

        try:
            if target_item is not None:
                current_row = self.table.row(target_item)

                if current_row < 0:
                    target_item = None
        except RuntimeError:
            target_item = None

        # Fallback for an item that was not available at commit
        # time. This is safe only while the same table remains.
        if target_item is None:
            if (
                0 <= original_row < self.table.rowCount()
                and 0 <= column < self.table.columnCount()
            ):
                target_item = self.table.item(
                    original_row,
                    column,
                )

        if target_item is None:
            return False

        self._applying_text_history = True

        try:
            target_item.setData(
                Qt.ItemDataRole.EditRole,
                target_text,
            )
            self.table.scrollToItem(target_item)
            self.table.setCurrentItem(target_item)
        except RuntimeError:
            return False
        finally:
            self._applying_text_history = False

        return True

    def undo_committed_text_edit(
        self,
    ) -> bool:
        while self._text_undo_stack:
            entry = self._text_undo_stack.pop()

            if self.apply_text_history_entry(
                entry,
                use_new_text=False,
            ):
                self._text_redo_stack.append(entry)
                return True

        return False

    def redo_committed_text_edit(
        self,
    ) -> bool:
        while self._text_redo_stack:
            entry = self._text_redo_stack.pop()

            if self.apply_text_history_entry(
                entry,
                use_new_text=True,
            ):
                self._text_undo_stack.append(entry)
                return True

        return False

    def eventFilter(
        self,
        watched,
        event,
    ) -> bool:
        # V6.4.5_GLOBAL_WINDOW_KEYS
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()

            close_modifiers = (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.MetaModifier
            )

            if (
                key == Qt.Key.Key_W
                and bool(modifiers & close_modifiers)
            ):
                target = QApplication.activeModalWidget()

                if target is None:
                    target = QApplication.activeWindow()

                if target is not None:
                    target.close()
                    return True

            if key in {
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
            }:
                modal = QApplication.activeModalWidget()

                if isinstance(modal, QMessageBox):
                    default_button = modal.defaultButton()

                    if (
                        default_button is not None
                        and default_button.isEnabled()
                    ):
                        default_button.click()
                        return True

        # BROWSER_SELECTOR_WHEEL_GUARD_V642
        #
        # Ignore wheel/trackpad scrolling only for the closed
        # browser selector. All other controls retain their
        # original wheel behavior.
        if (
            watched in {
                getattr(self, "browser", None),
                getattr(
                    self,
                    "pinterest_browser",
                    None,
                ),
            }
            and event.type()
            == QEvent.Type.Wheel
        ):
            event.accept()
            return True

        if (
            event.type()
            != _RSQEvent.Type.KeyPress
            or not hasattr(event, "key")
        ):
            return super().eventFilter(
                watched,
                event,
            )

        delegate = getattr(
            self,
            "lightweight_text_delegate",
            None,
        )

        # Preserve native QTextEdit Undo/Redo while a table cell
        # is still being edited.
        if (
            delegate is not None
            and getattr(
                delegate,
                "_active_text_editor",
                None,
            )
            is not None
        ):
            return super().eventFilter(
                watched,
                event,
            )

        key = event.key()
        modifiers = event.modifiers()

        command_modifier = bool(
            modifiers
            & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.MetaModifier
            )
        )
        shift_modifier = bool(
            modifiers
            & Qt.KeyboardModifier.ShiftModifier
        )
        alt_modifier = bool(
            modifiers
            & Qt.KeyboardModifier.AltModifier
        )

        if (
            not command_modifier
            or alt_modifier
            or key != Qt.Key.Key_Z
        ):
            return super().eventFilter(
                watched,
                event,
            )

        # Do not steal Undo from ordinary search fields, naming
        # fields, spin boxes, or other text controls.
        focus = _RSQApplication.focusWidget()

        if focus is not None:
            try:
                is_regular_text_control = any(
                    focus.inherits(class_name)
                    for class_name in (
                        "QLineEdit",
                        "QTextEdit",
                        "QPlainTextEdit",
                        "QAbstractSpinBox",
                    )
                )
            except RuntimeError:
                is_regular_text_control = False

            if is_regular_text_control:
                return super().eventFilter(
                    watched,
                    event,
                )

        if shift_modifier:
            handled = self.redo_committed_text_edit()
        else:
            handled = self.undo_committed_text_edit()

        if handled:
            event.accept()
            return True

        return super().eventFilter(
            watched,
            event,
        )

    def table_text_item_changed(
        self,
        item: QTableWidgetItem,
    ) -> None:
        if isinstance(
            item,
            LightweightRowSelectorItem,
        ):
            if not self._populating_results:
                self.selection_changed()
            return

        if not isinstance(
            item,
            LightweightTextItem,
        ):
            return

        item.handle_text_changed()

        if self._populating_results:
            return

        self.table.resizeRowToContents(
            item.row()
        )

    def editable_cell(
        self,
        row: int,
        column: int,
    ) -> LightweightTextItem | None:
        item = self.table.item(
            row,
            column,
        )

        if isinstance(
            item,
            LightweightTextItem,
        ):
            return item

        return None

    def update_reset_all_button(self) -> None:
        modified = False

        for row in range(self.table.rowCount()):
            for column in (4, 5):
                editor = self.editable_cell(
                    row,
                    column,
                )

                if (
                    editor is not None
                    and editor.modified
                ):
                    modified = True
                    break

            if modified:
                break

        self.reset_all_edits.setVisible(modified)

    def reset_all_manual_changes(self) -> None:
        for row in range(self.table.rowCount()):
            for column in (4, 5):
                editor = self.editable_cell(
                    row,
                    column,
                )

                if (
                    editor is not None
                    and editor.modified
                ):
                    editor.reset_to_default()

        self.update_reset_all_button()
        self.table.verticalHeader().setDefaultSectionSize(76)

    def toggle_all_rows(self) -> None:
        checked = self.select_all.isChecked()

        previous_block_state = (
            self.table.blockSignals(True)
        )

        try:
            for row in range(
                self.table.rowCount()
            ):
                selector = self.row_selector_item(row)

                if (
                    selector is None
                    or not self.row_selector_enabled(row)
                ):
                    continue

                selector.setCheckState(
                    Qt.CheckState.Checked
                    if checked
                    else Qt.CheckState.Unchecked
                )
        finally:
            self.table.blockSignals(
                previous_block_state
            )

        self.refresh_generated_names()
        self.update_import_button()

    def selected_count(self) -> int:
        return sum(
            1
            for row in range(
                self.table.rowCount()
            )
            if self.row_is_selected(row)
        )

    def selection_changed(self, *_args) -> None:
        self.refresh_generated_names()
        self.update_import_button()

    def update_import_button(self) -> None:
        count = self.selected_count()

        media_count = self.selected_media_count()

        self.import_button.setText(
            f"Скачать и добавить в Eagle · "
            f"{count} публ. / {media_count} файлов"
        )

        self.import_button.setEnabled(
            count > 0
            and media_count > 0
            and bool(self.preview_items)
            and self.process is None
        )

    def selected_preview_items(self) -> list[dict]:
        return [
            self.preview_items[row]
            for row in range(
                min(
                    self.table.rowCount(),
                    len(self.preview_items),
                )
            )
            if self.row_is_selected(row)
        ]

    def start_import(self) -> None:
        if self.process is not None:
            return

        if self.active_source == "pinterest":
            QMessageBox.information(
                self,
                "Pinterest preview",
                "Импорт включим после проверки "
                "таблицы и иерархии.",
            )
            return

        selected = self.selected_preview_items()

        if not selected:
            QMessageBox.warning(
                self,
                "Ничего не выбрано",
                "Выберите хотя бы одну публикацию.",
            )
            return

        if len(selected) > 50:
            QMessageBox.warning(
                self,
                "Выбрано больше 50 публикаций",
                (
                    "Неограниченный режим относится к поиску и "
                    "отображению Saved. За одну операцию импорта "
                    "сейчас можно обработать не более 50 "
                    "публикаций.\n\n"
                    "Снимите выбор с лишних строк. Автоматическая "
                    "очередь блоков по 50 будет добавлена "
                    "отдельным этапом."
                ),
            )
            return

        post_ids = [
            str(item.get("post_id") or "").strip()
            for item in selected
        ]

        if any(
            not post_id or not post_id.isdigit()
            for post_id in post_ids
        ):
            QMessageBox.warning(
                self,
                "Некорректный результат",
                "У выбранной публикации отсутствует post_id.",
            )
            return

        manifest_posts = {}

        for item in selected:
            post_id = str(
                item.get("post_id") or ""
            ).strip()

            matching_row = None

            for row, preview_item in enumerate(
                self.preview_items
            ):
                if str(
                    preview_item.get("post_id") or ""
                ).strip() == post_id:
                    matching_row = row
                    break

            if matching_row is None:
                QMessageBox.warning(
                    self,
                    "Ошибка таблицы",
                    f"Не найдена строка для post_id {post_id}",
                )
                return

            name_cell = self.editable_cell(
                matching_row,
                4,
            )
            description_cell = self.editable_cell(
                matching_row,
                5,
            )

            name_text = (
                name_cell.text()
                if name_cell is not None
                else ""
            )

            names = [
                line.strip()
                for line in name_text.splitlines()
                if line.strip()
            ]

            component_count = int(
                item.get("component_count") or 1
            )
            selected_components = (
                self.selected_component_indexes(item)
            )
            selected_count = len(
                selected_components
            )

            if len(names) != selected_count:
                QMessageBox.warning(
                    self,
                    "Некорректные названия",
                    (
                        f"Для {item.get('username')} нужно "
                        f"{selected_count} строк названий, "
                        f"сейчас указано {len(names)}.\n\n"
                        "Для карусели каждая строка "
                        "соответствует отдельному файлу."
                    ),
                )
                return

            manifest_posts[post_id] = {
                "names": names,
                "resume_partial": bool(
                    item.get("resume_partial")
                ),
                "restore_deleted": bool(
                    item.get("restore_deleted")
                ),
                "existing_post_number": (
                    item.get("existing_post_number")
                ),
                "imported_component_numbers": (
                    item.get(
                        "imported_component_numbers",
                        [],
                    )
                ),
                "imported_media_ids": (
                    item.get(
                        "imported_media_ids",
                        [],
                    )
                ),
                "selected_components": (
                    selected_components
                ),
                "names_by_component": {
                    str(component_index): name
                    for component_index, name in zip(
                        selected_components,
                        names,
                    )
                },
                "total_component_count": (
                    component_count
                ),
                "description": (
                    description_cell.text()
                    if description_cell is not None
                    else ""
                ),
            }

        start_number = self.naming_start.value()
        total_components = sum(
            len(self.selected_component_indexes(item))
            for item in selected
        )

        preview_lines = []

        # V6.4.4: confirmation must display the same mixed
        # numbering model as instagram_sync. Partial resumes keep
        # their registered numbers; only new publications consume
        # numbers beginning at start_number.
        reserved_partial_numbers = set()

        for item in selected:
            if not (
                item.get("resume_partial")
                or item.get("restore_deleted")
            ):
                continue

            try:
                reserved_number = int(
                    item.get("existing_post_number")
                )
            except (TypeError, ValueError):
                continue

            if reserved_number > 0:
                reserved_partial_numbers.add(reserved_number)

        used_preview_numbers = set()
        next_new_number = start_number

        for item in selected:
            username = str(
                item.get("username") or "@unknown"
            )
            component_count = int(
                item.get("component_count") or 1
            )
            selected_components = (
                self.selected_component_indexes(item)
            )

            if (
                item.get("resume_partial")
                or item.get("restore_deleted")
            ):
                try:
                    number = int(
                        item.get("existing_post_number")
                    )
                except (TypeError, ValueError):
                    number = next_new_number
                    next_new_number += 1
            else:
                while (
                    next_new_number in reserved_partial_numbers
                    or next_new_number in used_preview_numbers
                ):
                    next_new_number += 1

                number = next_new_number
                next_new_number += 1

            used_preview_numbers.add(number)

            preview_lines.append(
                f"{username} — № {number} "
                f"({len(selected_components)} из "
                f"{component_count} файл(ов); "
                f"элементы: "
                f"{', '.join(map(str, selected_components))})"
            )

        confirmation = (
            "Будут скачаны и добавлены в Eagle:\n\n"
            + "\n".join(preview_lines)
            + "\n\n"
            + f"Публикаций: {len(selected)}\n"
            + f"Медиафайлов: {total_components}\n"
            + f"Первый номер: {start_number}\n\n"
            + "Продолжить реальный импорт?"
        )

        resumed_posts = [
            item
            for item in selected
            if (
                item.get("resume_partial")
                or item.get("restore_deleted")
            )
        ]

        # V6.4.5_MULTI_PARTIAL_CAROUSEL_QUEUE
        #
        # The backend resolves and validates every partial resume
        # independently. Each carousel keeps its registered post
        # number, while ordinary new posts consume numbers beginning
        # at start_number.
        if resumed_posts:
            resume_warning_blocks = [
                (
                    "Распознаны частично импортированные "
                    f"карусели: {len(resumed_posts)}."
                )
            ]
            seen_existing_numbers = set()

            for resume_item in resumed_posts:
                try:
                    existing_number = int(
                        resume_item.get(
                            "existing_post_number"
                        )
                    )
                except (TypeError, ValueError):
                    QMessageBox.warning(
                        self,
                        "Некорректный номер карусели",
                        (
                            "У частично импортированной "
                            "карусели отсутствует корректный "
                            "сохранённый номер.\n\n"
                            f"Автор: "
                            f"{resume_item.get('username')}"
                        ),
                    )
                    return

                if existing_number < 1:
                    QMessageBox.warning(
                        self,
                        "Некорректный номер карусели",
                        (
                            "Сохранённый номер частичной "
                            "карусели должен быть больше нуля."
                        ),
                    )
                    return

                if existing_number in seen_existing_numbers:
                    QMessageBox.warning(
                        self,
                        "Повторяющийся номер карусели",
                        (
                            "Несколько частичных каруселей "
                            "имеют один сохранённый номер: "
                            f"{existing_number}.\n\n"
                            "Импорт остановлен до исправления "
                            "реестра."
                        ),
                    )
                    return

                seen_existing_numbers.add(existing_number)

                remaining = (
                    self.selected_component_indexes(
                        resume_item
                    )
                )
                imported_numbers = resume_item.get(
                    "imported_component_numbers",
                    [],
                )

                resume_warning_blocks.append(
                    (
                        f"Автор: "
                        f"{resume_item.get('username')}\n"
                        f"Сохранённый номер: "
                        f"{existing_number}\n"
                        "Уже находятся в Eagle: "
                        + (
                            ", ".join(
                                map(str, imported_numbers)
                            )
                            if imported_numbers
                            else "нет данных"
                        )
                        + "\nБудут дозагружены элементы: "
                        + (
                            ", ".join(map(str, remaining))
                            if remaining
                            else "нет"
                        )
                    )
                )

            resume_warning_blocks.append(
                (
                    "Перед импортом ReferenceSync проверит "
                    "соответствие Eagle и SQLite для каждой "
                    "частичной карусели. При любом расхождении "
                    "процесс будет остановлен."
                )
            )

            confirmation = (
                "\n\n".join(resume_warning_blocks)
                + "\n\n"
                + confirmation
            )

        # V6.4.4_SCROLLABLE_IMPORT_CONFIRMATION
        #
        # QMessageBox.question() attempts to display the complete
        # text at once and can grow beyond the available screen.
        # Keep the decision and totals in the fixed section, while
        # placing the complete publication list in Qt's scrollable
        # detailed-text area.
        confirmation_box = QMessageBox(self)
        confirmation_box.setIcon(
            QMessageBox.Icon.Question
        )
        confirmation_box.setWindowTitle(
            "Подтверждение импорта"
        )
        confirmation_box.setText(
            "Будут скачаны и добавлены в Eagle "
            f"{len(selected)} публикаций."
        )

        partial_count = len(resumed_posts)
        partial_summary = (
            f"\nЧастичных каруселей: {partial_count}"
            if partial_count
            else ""
        )

        confirmation_box.setInformativeText(
            f"Медиафайлов: {total_components}\n"
            f"Первый новый номер: {start_number}"
            f"{partial_summary}\n\n"
            "Откройте «Показать подробности», чтобы "
            "проверить полный список.\n\n"
            "Продолжить реальный импорт?"
        )
        confirmation_box.setDetailedText(confirmation)
        confirmation_box.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        )
        # V6.4.5: Enter confirms this specific dialog.
        # Escape remains explicitly mapped to No below.
        confirmation_box.setDefaultButton(
            QMessageBox.StandardButton.Yes
        )
        confirmation_box.setEscapeButton(
            QMessageBox.StandardButton.No
        )

        screen = self.screen()
        if screen is None:
            screen = QApplication.primaryScreen()

        if screen is not None:
            available = screen.availableGeometry()
            dialog_width = min(
                740,
                max(600, int(available.width() * 0.62)),
            )
            details_height = min(
                480,
                max(260, int(available.height() * 0.52)),
            )
        else:
            dialog_width = 700
            details_height = 360

        # Keep the same moderate width before and after expanding
        # the detailed text. Only the height is allowed to grow.
        confirmation_box.setFixedWidth(dialog_width)

        details_editor = confirmation_box.findChild(
            QTextEdit
        )
        if details_editor is not None:
            details_editor.setReadOnly(True)
            details_editor.setLineWrapMode(
                QTextEdit.LineWrapMode.WidgetWidth
            )
            details_editor.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            details_editor.setMinimumSize(
                0,
                details_height,
            )
            details_editor.setMaximumWidth(
                max(420, dialog_width - 48)
            )
            details_editor.setMaximumHeight(
                details_height
            )

        # V6.4.6_CONFIRMATION_SCREEN_CLAMP
        # QMessageBox changes size after Details is expanded.
        # A short-lived timer keeps it inside the active screen.
        geometry_timer = QTimer(confirmation_box)
        geometry_timer.setInterval(50)

        def clamp_confirmation_geometry() -> None:
            active_screen = confirmation_box.screen()

            if active_screen is None:
                active_screen = QApplication.primaryScreen()

            if active_screen is None:
                return

            available_geometry = (
                active_screen.availableGeometry()
            )
            maximum_height = max(
                320,
                available_geometry.height() - 100,
            )

            current_size = confirmation_box.size()
            target_height = min(
                current_size.height(),
                maximum_height,
            )

            confirmation_box.resize(
                dialog_width,
                target_height,
            )

            frame = confirmation_box.frameGeometry()

            left = max(
                available_geometry.left(),
                min(
                    frame.left(),
                    available_geometry.right()
                    - frame.width()
                    + 1,
                ),
            )
            top = max(
                available_geometry.top(),
                min(
                    frame.top(),
                    available_geometry.bottom()
                    - frame.height()
                    + 1,
                ),
            )

            confirmation_box.move(left, top)

        geometry_timer.timeout.connect(
            clamp_confirmation_geometry
        )
        geometry_timer.start()
        QTimer.singleShot(
            0,
            clamp_confirmation_geometry,
        )

        answer = confirmation_box.exec()
        geometry_timer.stop()

        if answer != QMessageBox.StandardButton.Yes:
            return

        username = self.username.text().strip()
        browser_code = self.browser.currentData()

        # UNLIMITED_SAVED_RETRIEVAL_V61
        # The full Saved mode uses gallery-dl cursor pagination
        # without --post-range. A zero limit is an explicit
        # sentinel and is ignored by discovery in full mode.
        if self.recent_search.isChecked():
            search_mode = "recent"
            limit = self.recent_limit.value()
        elif self.smart_search.isChecked():
            search_mode = "smart"
            # V6.2: unlimited stream; backend stops when the
            # first fully known Saved publication is reached.
            limit = 0
        else:
            search_mode = "full"
            limit = 0

        arguments = [
            "-m",
            "app.instagram_sync",
            "--username",
            username,
            "--browser",
            str(browser_code),
            "--speed-profile",
            str(self.download_speed.currentData()),
            "--search-mode",
            search_mode,
            "--limit",
            str(limit),
            "--batch-size",
            str(len(selected)),
            "--start-number",
            str(start_number),
        ]

        for post_id in post_ids:
            arguments.extend([
                "--post-id",
                post_id,
            ])

        manifests_directory = (
            PROJECT
            / "data"
            / "gui_manifests"
        )
        manifests_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        manifest_path = (
            manifests_directory
            / (
                "instagram_naming_"
                + datetime.now().strftime(
                    "%Y%m%d_%H%M%S_%f"
                )
                + ".json"
            )
        )

        manifest_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "created_at": (
                        datetime.now().isoformat()
                    ),
                    "posts": manifest_posts,
                    "discovery_posts": [
                        {
                            **dict(item),
                            "components": (
                                item.get("component_items", [])
                                if isinstance(
                                    item.get("component_items"),
                                    list,
                                )
                                else []
                            ),
                            "component_count_returned": int(
                                item.get("component_count")
                                or len(
                                    item.get(
                                        "component_items",
                                        [],
                                    )
                                    if isinstance(
                                        item.get(
                                            "component_items"
                                        ),
                                        list,
                                    )
                                    else []
                                )
                                or 1
                            ),
                            "discovery_status": (
                                item.get("discovery_status")
                                or "NEW_POST_CANDIDATE"
                            ),
                        }
                        for item in selected
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        arguments.extend([
            "--naming-manifest",
            str(manifest_path),
            "--discovery-manifest",
            str(manifest_path),
            "--commit",
        ])

        self._active_import_post_ids = set(post_ids)

        self.process_output = ""
        self._process_line_buffer = ""
        self.log.clear()
        self.process_started_at = (
            datetime.now().timestamp()
        )

        self.status.setText(
            "Скачиваем, проверяем и добавляем в Eagle…"
        )
        self.start_operation_progress()
        self.search_button.setEnabled(False)
        self.import_button.setEnabled(False)

        self.write_process_control("run")

        for control_button in (
            self.pause_button,
            self.resume_button,
            self.stop_button,
        ):
            control_button.setVisible(True)

        self.pause_button.setEnabled(True)
        self.resume_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        arguments.extend([
            "--control-file",
            str(self.control_file),
        ])

        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(PROJECT))
        self.process.setProgram(str(PYTHON))
        self.process.setArguments(arguments)
        self.process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.process.readyReadStandardOutput.connect(
            self.read_process_output
        )
        self.process.finished.connect(
            self.import_finished
        )
        self.process.start()

    def import_finished(
        self,
        exit_code: int,
        _exit_status,
    ) -> None:
        if self.process is not None:
            self.read_process_output()

        self.process = None
        self.search_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.stop_button.setEnabled(False)

        for control_button in (
            self.pause_button,
            self.resume_button,
            self.stop_button,
        ):
            control_button.setVisible(False)

        report = self.latest_report()

        if exit_code != 0 or report is None:
            self.status.setText(
                "Импорт остановлен из-за ошибки"
            )
            self.fail_operation_progress()
            self.log.setVisible(True)
            self.log_button.setChecked(True)
            self.update_import_button()

            self.status.setText(
                "Ошибка импорта — подробности в журнале"
            )
            self.log.setVisible(True)
            self.log_button.setChecked(True)
            return

        result = report.get("result", report)
        status = result.get("status")

        if status == "DOWNLOAD_STOPPED_BY_USER":
            self.status.setText(
                "Процесс остановлен пользователем"
            )
            self.set_stopped_progress_style()
            self.operation_progress.setFormat(
                "Процесс остановлен пользователем"
            )
            self.log.setVisible(False)
            self.log_button.setChecked(False)
            self.update_import_button()
            return

        if status == "SYNC_COMPLETED":
            imported_items = int(
                result.get("imported_items") or 0
            )
            posts_registered = int(
                result.get("posts_registered") or 0
            )

            self.status.setText(
                "Импорт успешно завершён"
            )
            self.summary.setText(
                f"Добавлено файлов: {imported_items}"
            )


            self.mark_active_import_rows_completed()
            self.update_reset_all_button()
            self.table.verticalHeader().setDefaultSectionSize(76)
            return

        self.status.setText(
            f"Импорт требует проверки: {status}"
        )
        self.log.setVisible(True)
        self.log_button.setChecked(True)
        self.update_import_button()
        self.show_operation_error(
            "Операция требует проверки",
            (
                "ReferenceSync остановил автоматическое "
                f"продолжение. Статус: {status}"
            ),
        )


    def refresh_generated_names(self) -> None:
        if not self.preview_items:
            return

        start = self.naming_start.value()
        marker = self.numbering_text.text()
        destination = (
            self.numbering_destination.currentText()
        )
        extra_description = (
            self.description_template.text().strip()
        )

        selected_rows = [
            candidate_row
            for candidate_row in range(
                self.table.rowCount()
            )
            if self.row_is_selected(candidate_row)
        ]

        # LINEAR_GENERATED_NUMBERING_V44
        # Calculate the complete row-to-number mapping once.
        # The previous implementation repeated these scans for
        # every row, which caused O(n²) population time.
        selected_row_set = set(selected_rows)
        resume_numbers = {}
        reserved_numbers = set()

        for selected_row in selected_rows:
            if selected_row >= len(self.preview_items):
                continue

            selected_item = self.preview_items[
                selected_row
            ]

            if not (
                selected_item.get("resume_partial")
                or selected_item.get("restore_deleted")
            ):
                continue

            try:
                existing_number = int(
                    selected_item.get(
                        "existing_post_number"
                    )
                )
            except (TypeError, ValueError):
                continue

            if existing_number <= 0:
                continue

            resume_numbers[selected_row] = (
                existing_number
            )
            reserved_numbers.add(existing_number)

        assigned_post_numbers = {}
        candidate_number = start

        for selected_row in selected_rows:
            if selected_row >= len(self.preview_items):
                continue

            if selected_row in resume_numbers:
                assigned_post_numbers[selected_row] = (
                    resume_numbers[selected_row]
                )
                continue

            while candidate_number in reserved_numbers:
                candidate_number += 1

            assigned_post_numbers[selected_row] = (
                candidate_number
            )
            candidate_number += 1

        for row, item in enumerate(
            self.preview_items
        ):
            username = str(
                item.get("username") or "@unknown"
            )

            component_count = int(
                item.get("component_count") or 1
            )
            selected_components = (
                self.selected_component_indexes(item)
            )

            # V4.4: O(1) lookup. Unchecked rows are absent
            # from the mapping and therefore show no number.
            post_number = (
                assigned_post_numbers.get(row)
                if row in selected_row_set
                else None
            )

            numbering_parts = []

            if (
                self.numbering_enabled.isChecked()
                and post_number is not None
            ):
                if component_count <= 1:
                    numbering_parts = [
                        f"{marker}{post_number}"
                    ]
                else:
                    numbering_parts = [
                        (
                            f"{marker}{post_number}-"
                            f"{component}"
                        )
                        for component in selected_components
                    ]

            source_description = str(
                item.get("description") or ""
            ).strip()

            base_description_parts = [
                part
                for part in (
                    source_description,
                    extra_description,
                )
                if part
            ]
            base_description = "\n\n".join(
                base_description_parts
            )

            if not numbering_parts:
                generated_name = username
                generated_description = (
                    base_description
                )
            else:
                numbering_text = "\n".join(
                    numbering_parts
                )

                if destination == "Название":
                    generated_name = "\n".join(
                        f"{username} {part}"
                        for part in numbering_parts
                    )
                    generated_description = (
                        base_description
                    )
                elif destination == "Описание":
                    generated_name = username
                    generated_description = "\n\n".join(
                        part
                        for part in (
                            numbering_text,
                            base_description,
                        )
                        if part
                    )
                else:
                    generated_name = "\n".join(
                        f"{username} {part}"
                        for part in numbering_parts
                    )
                    generated_description = "\n\n".join(
                        part
                        for part in (
                            numbering_text,
                            base_description,
                        )
                        if part
                    )

            name_editor = self.editable_cell(row, 4)
            description_editor = self.editable_cell(
                row,
                5,
            )

            if name_editor is not None:
                name_editor.set_generated_text(
                    generated_name
                )

            if description_editor is not None:
                description_editor.set_generated_text(
                    generated_description
                )

        # One batch geometry pass is substantially cheaper
        # than recalculating table geometry after every row.
        self.table.verticalHeader().setDefaultSectionSize(76)

        self.update_reset_all_button()

    def show_counter_notice(self) -> None:
        QMessageBox.information(
            self,
            "Конструктор счётчиков",
            "Кнопка уже заложена в интерфейс. "
            "Динамическое добавление нескольких "
            "независимых счётчиков подключим после "
            "утверждения этого экрана.",
        )

    # -----------------------------------------------------
    # Appearance
    # -----------------------------------------------------

    def apply_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #101217;
                color: #f2f3f6;
                font-size: 14px;
            }

            QLabel#title {
                color: #ffffff;
            }

            QLabel#muted, QLabel#hint,
            QLabel#radioHint {
                color: #9299a8;
            }

            QLabel#hint, QLabel#radioHint {
                font-size: 12px;
            }

            QLabel#radioHint {
                margin-left: 25px;
                margin-bottom: 5px;
            }

            QLabel#sectionTitle {
                font-size: 17px;
                font-weight: 700;
            }

            QLabel#smallTitle {
                font-weight: 700;
            }

            QLabel#alphaBadge {
                background: #342f64;
                color: #bdb5ff;
                border-radius: 8px;
                padding: 6px 10px;
                font-weight: 800;
            }

            QLabel#activePlatformLabel {
                color: #bdb7ff;
                font-weight: 700;
            }

            QFrame#card, QGroupBox {
                background: #191c23;
                border: 1px solid #292e38;
                border-radius: 13px;
            }

            QGroupBox {
                margin-top: 12px;
                padding-top: 12px;
                font-weight: 700;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 15px;
                padding: 0 6px;
            }

            QLineEdit, QComboBox, QSpinBox,
            QTextEdit {
                background: #222630;
                border: 1px solid #363c49;
                border-radius: 8px;
                padding: 8px;
                color: #ffffff;
            }

            QLineEdit:focus, QComboBox:focus,
            QSpinBox:focus {
                border: 1px solid #7d70f8;
            }

            QFrame#handleField {
                background: #222630;
                border: 1px solid #363c49;
                border-radius: 8px;
            }

            QLabel#handlePrefix {
                color: #aeb4c0;
                font-size: 16px;
                font-weight: 700;
                background: transparent;
            }

            QLineEdit#handleInput {
                border: none;
                background: transparent;
            }

            QPushButton {
                min-height: 38px;
                border-radius: 9px;
                padding: 3px 13px;
                color: #e6e8ed;
                background: #292e38;
                border: 1px solid #373d49;
                font-weight: 700;
            }

            QPushButton:hover {
                background: #333945;
            }

            QPushButton#primaryButton {
                background: #7668f4;
                color: white;
                border: none;
                min-height: 46px;
            }

            QPushButton#primaryButton:hover {
                background: #887bff;
            }

            QPushButton#importButton {
                background: #27815f;
                color: white;
                border: none;
                min-width: 270px;
            }

            QPushButton#importButton:disabled,
            QPushButton#importButton:disabled:hover {
                background: #1c211f;
                color: #68716d;
                border: 1px solid #2a312e;
            }

            QPushButton:disabled {
                background: #292d35;
                color: #6f7581;
                border-color: #30343d;
            }

            QPushButton#activePlatform {
                background: #7668f4;
                color: white;
                border: none;
                font-size: 15px;
            }

            QPushButton#futurePlatform {
                background: #232730;
                color: #aeb4c0;
            }

            QTableWidget {
                background: #15181e;
                alternate-background-color: #1c1f27;
                border: none;
                border-radius: 8px;
                gridline-color: #292e38;
            }

            QHeaderView::section {
                background: #242933;
                color: #cfd3dc;
                border: none;
                padding: 9px;
                font-weight: 700;
            }

            QLabel#statusDot {
                color: #53d59a;
                font-size: 18px;
            }

            QLabel#statusText {
                font-weight: 700;
            }

            QRadioButton, QCheckBox {
                spacing: 8px;
            }

            QScrollArea {
                background: transparent;
            }

            QScrollBar:vertical {
                background: #171a20;
                width: 10px;
                border-radius: 5px;
            }

            QScrollBar::handle:vertical {
                background: #3b414e;
                min-height: 30px;
                border-radius: 5px;
            }
        """)


def main() -> None:
    application = QApplication(sys.argv)
    application.setApplicationName("ReferenceSync")
    application.setStyle("Fusion")

    window = ReferenceSyncWindow()
    window.show()

    raise SystemExit(application.exec())


if __name__ == "__main__":
    main()
