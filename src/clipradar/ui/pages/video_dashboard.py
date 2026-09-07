from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QPointF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QMouseEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QTextLayout,
    QTextOption,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from clipradar.settings.models import UIStateSettings
from clipradar.settings.service import SettingsService
from clipradar.storage.repositories import ChannelRepository
from clipradar.ui.analyze_video_dialog import AnalyzeVideoDialog
from clipradar.ui.common import card_layout, clear_layout, muted_label, title_label
from clipradar.youtube.client import RemoteVideo


class VideoCard(QFrame):
    analyze_requested = Signal(str, str)

    def __init__(self, video: RemoteVideo, parent: QWidget | None = None):
        super().__init__(parent)
        self.video = video
        self.setObjectName("VideoTile")
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.thumbnail = ThumbnailLabel("Loading thumbnail…")
        self.thumbnail.setObjectName("VideoThumbnail")
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.thumbnail)
        self.title = TwoLineElidedLabel(video.title)
        self.title.setObjectName("VideoCardTitle")
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.title)
        metadata = muted_label(_video_metadata(video))
        metadata.setObjectName("VideoMetadata")
        metadata.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(metadata)
        if not video.thumbnail_url:
            self.thumbnail.setText("No thumbnail")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            QDesktopServices.openUrl(QUrl(self.video.url))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.thumbnail.setFixedHeight(max(120, round(event.size().width() * 9 / 16)))
        super().resizeEvent(event)

    def _menu(self, point) -> None:
        menu = QMenu(self)
        open_action = menu.addAction("Open on YouTube")
        copy_action = menu.addAction("Copy link")
        menu.addSeparator()
        analyze_action = menu.addAction("Analyze…")
        selected = menu.exec(self.mapToGlobal(point))
        if selected == open_action:
            QDesktopServices.openUrl(QUrl(self.video.url))
        elif selected == copy_action:
            QApplication.clipboard().setText(self.video.url)
        elif selected == analyze_action:
            self.analyze_requested.emit(self.video.url, self.video.title)


class ThumbnailLabel(QLabel):
    """Keep dashboard thumbnails at YouTube's 16:9 ratio without affecting grid width."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self._source_pixmap: QPixmap | None = None
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return max(120, round(width * 9 / 16))

    def sizeHint(self) -> QSize:
        return QSize(320, 180)

    def set_source_pixmap(self, pixmap: QPixmap) -> None:
        self._source_pixmap = pixmap
        self._refresh_pixmap()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        if not self._source_pixmap or self.width() <= 0 or self.height() <= 0:
            return
        scaled = self._source_pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        left = max(0, (scaled.width() - self.width()) // 2)
        top = max(0, (scaled.height() - self.height()) // 2)
        self.setPixmap(scaled.copy(left, top, self.width(), self.height()))


class TwoLineElidedLabel(QLabel):
    """Paint a true two-line title and elide overflow instead of clipping it."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setToolTip(text)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * 2 + 3)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(self.palette().windowText().color())
        width = max(1.0, float(self.contentsRect().width()))
        layout = QTextLayout(self.text(), self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        lines = []
        layout.beginLayout()
        y = 0.0
        for _ in range(2):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0.0, y))
            y += line.height()
            lines.append(line)
        layout.endLayout()
        for index, line in enumerate(lines):
            is_last = index == len(lines) - 1
            has_overflow = line.textStart() + line.textLength() < len(self.text())
            if is_last and has_overflow:
                remaining = self.text()[line.textStart():].strip()
                elided = self.fontMetrics().elidedText(
                    remaining,
                    Qt.TextElideMode.ElideRight,
                    round(width),
                )
                painter.drawText(
                    0,
                    round(line.y()),
                    round(width),
                    round(line.height()),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    elided,
                )
            else:
                line.draw(painter, QPointF())


class VideoDashboardPage(QWidget):
    load_requested = Signal(int)
    analyze_requested = Signal(int, str, str)

    def __init__(
        self,
        channels: ChannelRepository,
        settings: SettingsService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.channels = channels
        self.settings = settings
        self.selected_channel_id = self.settings.ui_state().dashboard_channel_id
        self.cache: dict[int, list[RemoteVideo]] = {}
        self.loading_channel_id: int | None = None
        self.network = QNetworkAccessManager(self)
        self._thumbnail_cache: dict[str, QPixmap] = {}
        self._thumbnail_replies: set[QNetworkReply] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        toolbar, toolbar_layout = card_layout(object_name="Toolbar")
        row = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(3)
        self.channel_name = title_label("Channel videos")
        heading.addWidget(self.channel_name)
        self.channel_summary = muted_label("Browse recent uploads without downloading them.")
        heading.addWidget(self.channel_summary)
        row.addLayout(heading)
        row.addStretch(1)
        row.addWidget(muted_label("Channel"))
        self.channel_filter = QComboBox()
        self.channel_filter.setMinimumWidth(230)
        self.channel_filter.currentIndexChanged.connect(self._channel_changed)
        row.addWidget(self.channel_filter)
        self.reload = QPushButton("Refresh")
        self.reload.clicked.connect(lambda: self.request_current(force=True))
        row.addWidget(self.reload)
        toolbar_layout.addLayout(row)
        root.addWidget(toolbar)

        status_row = QHBoxLayout()
        self.feed_title = title_label("Recent uploads")
        status_row.addWidget(self.feed_title)
        status_row.addStretch(1)
        self.status = muted_label("Choose a channel")
        status_row.addWidget(self.status)
        root.addLayout(status_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.container.setMaximumWidth(1240)
        self.grid = QGridLayout(self.container)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(22)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        for column in range(3):
            self.grid.setColumnStretch(column, 1)
        self.scroll.setWidget(self.container)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self.scroll, 1)
        self.refresh_channels()

    def refresh_channels(self) -> None:
        channels = self.channels.list_all()
        valid_ids = {int(channel.id) for channel in channels if channel.id is not None}
        if self.selected_channel_id not in valid_ids:
            self.selected_channel_id = int(channels[0].id) if channels else None
        self.channel_filter.blockSignals(True)
        self.channel_filter.clear()
        for channel in channels:
            self.channel_filter.addItem(channel.name, int(channel.id))
        if self.selected_channel_id is not None:
            index = self.channel_filter.findData(self.selected_channel_id)
            self.channel_filter.setCurrentIndex(index if index >= 0 else 0)
        self.channel_filter.setEnabled(bool(channels))
        self.reload.setEnabled(bool(channels) and self.loading_channel_id is None)
        self.channel_filter.blockSignals(False)
        if not channels:
            self.channel_name.setText("Channel videos")
            self.channel_summary.setText("Add a channel to browse its latest uploads.")
            self.status.setText("Add a channel under Channels first")
            self._render([])
        elif self.selected_channel_id in self.cache:
            self._render(self.cache[self.selected_channel_id])
        self._update_channel_identity()

    def request_current(self, *, force: bool = False) -> None:
        channel_id = self.channel_filter.currentData()
        if channel_id is None or self.loading_channel_id is not None:
            return
        channel_id = int(channel_id)
        if not force and channel_id in self.cache:
            self._render(self.cache[channel_id])
            return
        self.loading_channel_id = channel_id
        self.reload.setEnabled(False)
        self.status.setText("Loading recent uploads…")
        self.load_requested.emit(channel_id)

    def set_videos(self, channel_id: int, videos: list[RemoteVideo]) -> None:
        self.cache[channel_id] = videos
        if self.loading_channel_id == channel_id:
            self.loading_channel_id = None
        self.reload.setEnabled(self.channel_filter.count() > 0)
        if self.selected_channel_id == channel_id:
            self._render(videos)

    def set_error(self, channel_id: int, message: str) -> None:
        if self.loading_channel_id == channel_id:
            self.loading_channel_id = None
        self.reload.setEnabled(self.channel_filter.count() > 0)
        if self.selected_channel_id == channel_id:
            self.status.setText(message)

    def _channel_changed(self, index: int) -> None:
        channel_id = self.channel_filter.itemData(index) if index >= 0 else None
        if channel_id is None:
            return
        self.selected_channel_id = int(channel_id)
        current = self.settings.ui_state()
        self.settings.save_ui_state(UIStateSettings(
            review_channel_id=current.review_channel_id,
            dashboard_channel_id=self.selected_channel_id,
        ))
        self._update_channel_identity()
        self.request_current()

    def _render(self, videos: list[RemoteVideo]) -> None:
        clear_layout(self.grid)
        if not videos:
            empty = muted_label("No finished uploads were found for this channel.", wrap=True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(empty, 0, 0, 1, 3)
            self.status.setText("No videos")
            return
        for index, video in enumerate(videos):
            card = VideoCard(video)
            card.analyze_requested.connect(
                lambda url, title, channel_id=self.selected_channel_id: self._analyze(
                    int(channel_id), url, title
                )
            )
            self.grid.addWidget(card, index // 3, index % 3)
            if video.thumbnail_url:
                self._load_thumbnail(video.thumbnail_url, card.thumbnail)
        self.status.setText(f"{len(videos)} recent video{'s' if len(videos) != 1 else ''}")

    def _update_channel_identity(self) -> None:
        channel_id = self.channel_filter.currentData()
        channel = self.channels.get(int(channel_id)) if channel_id is not None else None
        if not channel:
            return
        self.channel_name.setText(channel.name)
        self.channel_summary.setText("Latest channel uploads · open on YouTube or right-click to analyze")
        self.feed_title.setText(f"{channel.name} videos")

    def _analyze(self, channel_id: int, url: str, title: str) -> None:
        dialog = AnalyzeVideoDialog(
            self.settings.publishing().category_id,
            video_url=url,
            video_title=title,
            parent=self,
        )
        if dialog.exec():
            selected_url, category_id = dialog.payload()
            self.analyze_requested.emit(channel_id, selected_url, category_id)

    def _load_thumbnail(self, url: str, label: QLabel) -> None:
        cached = self._thumbnail_cache.get(url)
        if cached:
            self._set_thumbnail(label, cached)
            return
        reply = self.network.get(QNetworkRequest(QUrl(url)))
        self._thumbnail_replies.add(reply)

        def finish() -> None:
            self._thumbnail_replies.discard(reply)
            if reply.error() == QNetworkReply.NetworkError.NoError:
                pixmap = QPixmap()
                if pixmap.loadFromData(reply.readAll()):
                    self._thumbnail_cache[url] = pixmap
                    self._set_thumbnail(label, pixmap)
            elif isValid(label):
                label.setText("Thumbnail unavailable")
            reply.deleteLater()

        reply.finished.connect(finish)

    @staticmethod
    def _set_thumbnail(label: QLabel, pixmap: QPixmap) -> None:
        if not isValid(label):
            return
        label.setText("")
        if isinstance(label, ThumbnailLabel):
            label.set_source_pixmap(pixmap)
        else:
            label.setPixmap(pixmap)


def _relative_upload_time(value: str | None) -> str:
    if not value:
        return "Upload time unavailable"
    try:
        published = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        seconds = max(0, int((datetime.now(timezone.utc) - published.astimezone(timezone.utc)).total_seconds()))
    except ValueError:
        return "Upload time unavailable"
    if seconds < 3600:
        minutes = max(1, seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return published.astimezone().strftime("%d.%m.%Y")


def _video_metadata(video: RemoteVideo) -> str:
    uploaded = _relative_upload_time(video.published_at)
    if video.view_count is None:
        return uploaded
    return f"{_format_views(video.view_count)} views · {uploaded}"


def _format_views(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}".rstrip("0").rstrip(".") + "B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}".rstrip("0").rstrip(".") + "K"
    return str(value)
