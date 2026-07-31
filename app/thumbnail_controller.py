from __future__ import annotations

import hashlib
from collections import OrderedDict, deque
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)


ROLE_THUMBNAIL_KEY = (
    int(Qt.ItemDataRole.UserRole) + 51
)
ROLE_THUMBNAIL_URL = (
    int(Qt.ItemDataRole.UserRole) + 52
)
ROLE_THUMBNAIL_PIXMAP = (
    int(Qt.ItemDataRole.UserRole) + 53
)
ROLE_THUMBNAIL_VISIBLE = (
    int(Qt.ItemDataRole.UserRole) + 54
)


class ThumbnailController(QObject):
    """Loads only small previews for visible table rows."""

    MAX_RESPONSE_BYTES = 256 * 1024
    RAM_CACHE_LIMIT = 96
    DISK_CACHE_LIMIT = 100 * 1024 * 1024
    MAX_CONCURRENT = 2
    PREFETCH_ROWS = 3
    THUMBNAIL_SIZE = 60

    def __init__(
        self,
        *,
        table,
        toggle,
        statistics_label,
        cache_directory: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)

        self.table = table
        self.toggle = toggle
        self.statistics_label = statistics_label
        self.cache_directory = Path(cache_directory)
        self.cache_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.manager = QNetworkAccessManager(self)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(
            self.load_visible_rows
        )

        self.ram_cache: OrderedDict[
            str,
            QPixmap,
        ] = OrderedDict()

        self.queue = deque()
        self.active = {}
        self.requested_keys = set()
        self.failed_keys = set()
        self.rows_with_pixmaps = set()
        self.generation = 0

        self.downloaded = 0
        self.downloaded_bytes = 0
        self.cache_hits = 0
        self.failures = 0

        self.toggle.toggled.connect(
            self.set_enabled
        )
        self.table.verticalScrollBar().valueChanged.connect(
            self.schedule
        )

        self.prune_disk_cache()
        self.update_statistics()

    def schedule(self, *_args) -> None:
        if not self.toggle.isChecked():
            return

        self.timer.start()

    def set_enabled(self, enabled: bool) -> None:
        self.table.horizontalHeader().resizeSection(
            0,
            112 if enabled else 42,
        )

        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)

            if item is None:
                continue

            item.setData(
                ROLE_THUMBNAIL_VISIBLE,
                bool(enabled),
            )

            if not enabled:
                item.setData(
                    ROLE_THUMBNAIL_PIXMAP,
                    None,
                )

        if not enabled:
            self.cancel_requests()
            self.rows_with_pixmaps.clear()
            self.table.viewport().update()
            self.update_statistics()
            return

        self.schedule()

    def reset_for_results(self) -> None:
        self.cancel_requests()
        self.generation += 1
        self.failed_keys.clear()
        self.rows_with_pixmaps.clear()

        self.downloaded = 0
        self.downloaded_bytes = 0
        self.cache_hits = 0
        self.failures = 0

        enabled = self.toggle.isChecked()

        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)

            if item is None:
                continue

            item.setData(
                ROLE_THUMBNAIL_VISIBLE,
                enabled,
            )
            item.setData(
                ROLE_THUMBNAIL_PIXMAP,
                None,
            )

        self.table.horizontalHeader().resizeSection(
            0,
            112 if enabled else 42,
        )
        self.update_statistics()

        if enabled:
            self.schedule()

    def clear(self) -> None:
        self.cancel_requests()
        self.generation += 1
        self.failed_keys.clear()
        self.rows_with_pixmaps.clear()

        self.downloaded = 0
        self.downloaded_bytes = 0
        self.cache_hits = 0
        self.failures = 0
        self.update_statistics()

    def cancel_requests(self) -> None:
        self.timer.stop()
        self.queue.clear()
        self.requested_keys.clear()

        replies = list(self.active.keys())
        self.active.clear()

        for reply in replies:
            if reply.isRunning():
                reply.abort()

    def visible_range(self):
        row_count = self.table.rowCount()

        if row_count <= 0:
            return None

        viewport = self.table.viewport()
        first = self.table.rowAt(0)

        if first < 0:
            first = 0

        last = self.table.rowAt(
            max(0, viewport.height() - 1)
        )

        if last < 0:
            last = min(
                row_count - 1,
                first + 12,
            )

        first = max(
            0,
            first - self.PREFETCH_ROWS,
        )
        last = min(
            row_count - 1,
            last + self.PREFETCH_ROWS,
        )

        return first, last

    def load_visible_rows(self) -> None:
        if not self.toggle.isChecked():
            return

        visible = self.visible_range()

        if visible is None:
            return

        first, last = visible
        keep_rows = set(range(first, last + 1))

        # Remove decoded images from non-visible table items.
        # The small RAM/disk cache remains available.
        for row in list(self.rows_with_pixmaps):
            if row in keep_rows:
                continue

            item = self.table.item(row, 0)

            if item is not None:
                item.setData(
                    ROLE_THUMBNAIL_PIXMAP,
                    None,
                )

            self.rows_with_pixmaps.discard(row)

        for row in range(first, last + 1):
            item = self.table.item(row, 0)

            if item is None:
                continue

            key = str(
                item.data(ROLE_THUMBNAIL_KEY)
                or ""
            ).strip()
            url = str(
                item.data(ROLE_THUMBNAIL_URL)
                or ""
            ).strip()

            if not key or not url:
                continue

            current = item.data(
                ROLE_THUMBNAIL_PIXMAP
            )

            if (
                isinstance(current, QPixmap)
                and not current.isNull()
            ):
                continue

            pixmap = self.ram_cache.get(key)

            if pixmap is not None:
                self.ram_cache.move_to_end(key)
                self.cache_hits += 1
                self.apply_pixmap(row, key, pixmap)
                continue

            pixmap = self.read_disk_cache(key)

            if pixmap is not None:
                self.add_ram_cache(key, pixmap)
                self.cache_hits += 1
                self.apply_pixmap(row, key, pixmap)
                continue

            if (
                key in self.requested_keys
                or key in self.failed_keys
            ):
                continue

            self.requested_keys.add(key)
            self.queue.append((
                self.generation,
                row,
                key,
                url,
            ))

        self.update_statistics()
        self.pump_queue()

    def pump_queue(self) -> None:
        if not self.toggle.isChecked():
            return

        while (
            self.queue
            and len(self.active)
            < self.MAX_CONCURRENT
        ):
            generation, row, key, url = (
                self.queue.popleft()
            )

            if generation != self.generation:
                self.requested_keys.discard(key)
                continue

            request = QNetworkRequest(QUrl(url))
            request.setTransferTimeout(15000)
            request.setHeader(
                QNetworkRequest.KnownHeaders
                .UserAgentHeader,
                (
                    "Mozilla/5.0 ReferenceSync/"
                    "thumbnail-preview"
                ),
            )
            request.setRawHeader(
                b"Accept",
                b"image/avif,image/webp,image/*,*/*;q=0.8",
            )
            request.setAttribute(
                QNetworkRequest.Attribute
                .RedirectPolicyAttribute,
                QNetworkRequest.RedirectPolicy
                .NoLessSafeRedirectPolicy,
            )

            reply = self.manager.get(request)
            self.active[reply] = (
                generation,
                row,
                key,
                url,
            )
            reply.finished.connect(
                lambda current=reply:
                    self.request_finished(current)
            )

    def request_finished(self, reply) -> None:
        entry = self.active.pop(reply, None)

        if entry is None:
            reply.deleteLater()
            self.pump_queue()
            return

        generation, row, key, _url = entry
        self.requested_keys.discard(key)

        error = reply.error()
        data = bytes(reply.readAll())
        reply.deleteLater()

        if generation != self.generation:
            self.pump_queue()
            return

        if (
            error
            != QNetworkReply.NetworkError.NoError
            or not data
            or len(data) > self.MAX_RESPONSE_BYTES
        ):
            self.failures += 1
            self.failed_keys.add(key)
            self.update_statistics()
            self.pump_queue()
            return

        pixmap = QPixmap()

        if not pixmap.loadFromData(data):
            self.failures += 1
            self.failed_keys.add(key)
            self.update_statistics()
            self.pump_queue()
            return

        pixmap = pixmap.scaled(
            self.THUMBNAIL_SIZE,
            self.THUMBNAIL_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        self.write_disk_cache(key, data)
        self.add_ram_cache(key, pixmap)

        self.downloaded += 1
        self.downloaded_bytes += len(data)

        self.apply_pixmap(row, key, pixmap)
        self.update_statistics()
        self.pump_queue()

    def apply_pixmap(
        self,
        row: int,
        key: str,
        pixmap: QPixmap,
    ) -> None:
        if (
            row < 0
            or row >= self.table.rowCount()
        ):
            return

        item = self.table.item(row, 0)

        if item is None:
            return

        current_key = str(
            item.data(ROLE_THUMBNAIL_KEY)
            or ""
        )

        if current_key != key:
            return

        item.setData(
            ROLE_THUMBNAIL_PIXMAP,
            pixmap,
        )
        self.rows_with_pixmaps.add(row)

        self.table.viewport().update(
            self.table.visualItemRect(item)
        )

    def add_ram_cache(
        self,
        key: str,
        pixmap: QPixmap,
    ) -> None:
        self.ram_cache[key] = pixmap
        self.ram_cache.move_to_end(key)

        while (
            len(self.ram_cache)
            > self.RAM_CACHE_LIMIT
        ):
            self.ram_cache.popitem(last=False)

    def cache_path(self, key: str) -> Path:
        digest = hashlib.sha256(
            key.encode(
                "utf-8",
                errors="replace",
            )
        ).hexdigest()

        return self.cache_directory / (
            digest + ".thumb"
        )

    def read_disk_cache(
        self,
        key: str,
    ) -> QPixmap | None:
        path = self.cache_path(key)

        try:
            data = path.read_bytes()
        except OSError:
            return None

        if (
            not data
            or len(data) > self.MAX_RESPONSE_BYTES
        ):
            try:
                path.unlink()
            except OSError:
                pass
            return None

        pixmap = QPixmap()

        if not pixmap.loadFromData(data):
            try:
                path.unlink()
            except OSError:
                pass
            return None

        try:
            path.touch(exist_ok=True)
        except OSError:
            pass

        return pixmap.scaled(
            self.THUMBNAIL_SIZE,
            self.THUMBNAIL_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def write_disk_cache(
        self,
        key: str,
        data: bytes,
    ) -> None:
        if (
            not data
            or len(data) > self.MAX_RESPONSE_BYTES
        ):
            return

        path = self.cache_path(key)
        temporary = path.with_suffix(".tmp")

        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass

    def prune_disk_cache(self) -> None:
        try:
            files = [
                path
                for path in self.cache_directory.iterdir()
                if path.is_file()
            ]
        except OSError:
            return

        entries = []
        total = 0

        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue

            total += stat.st_size
            entries.append((
                stat.st_mtime,
                stat.st_size,
                path,
            ))

        if total <= self.DISK_CACHE_LIMIT:
            return

        entries.sort()

        for _mtime, size, path in entries:
            if total <= self.DISK_CACHE_LIMIT:
                break

            try:
                path.unlink()
                total -= size
            except OSError:
                continue

    def update_statistics(self) -> None:
        if not self.toggle.isChecked():
            self.statistics_label.setText(
                "Миниатюры: выключены"
            )
            return

        kib = self.downloaded_bytes / 1024

        self.statistics_label.setText(
            f"Миниатюры: {self.downloaded} загр. · "
            f"{kib:.1f} KiB · "
            f"кэш {self.cache_hits} · "
            f"ошибок {self.failures}"
        )
