from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from clipradar.storage.repositories import ClipRepository
from clipradar.ui.common import card_layout, format_time, muted_label, status_pill, title_label


class ReviewItemWidget(QWidget):
    def __init__(self, clip: dict, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 11, 12, 11)
        layout.setSpacing(5)
        top = QHBoxLayout()
        channel = QLabel(clip["channel_name"])
        channel.setStyleSheet("font-weight:650")
        score = status_pill(f"{round(clip['ai_score'] or 0)} score")
        top.addWidget(channel)
        top.addStretch(1)
        top.addWidget(score)
        layout.addLayout(top)
        title = QLabel(clip["video_title"])
        title.setWordWrap(True)
        title.setMaximumHeight(42)
        layout.addWidget(title)
        layout.addWidget(muted_label(f"{format_time(clip['duration_seconds'])}  ·  {clip['format']}"))


class ReviewPage(QWidget):
    approve_requested = Signal(int)
    reject_requested = Signal(int)
    regenerate_requested = Signal(int)

    def __init__(self, repository: ClipRepository, parent: QWidget | None = None):
        super().__init__(parent)
        self.repository = repository
        self.records: list[dict] = []
        self.current: dict | None = None
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.85)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(10)
        root.addWidget(splitter)

        left, left_layout = card_layout(object_name="Card")
        left.setMinimumWidth(330)
        header = QHBoxLayout()
        header.addWidget(title_label("Ready for review"))
        header.addStretch(1)
        self.count = muted_label("0 clips")
        header.addWidget(self.count)
        left_layout.addLayout(header)
        self.list = QListWidget()
        self.list.setSpacing(8)
        self.list.currentRowChanged.connect(self._select)
        left_layout.addWidget(self.list, 1)
        splitter.addWidget(left)

        right, right_layout = card_layout(object_name="Card")
        right.setMinimumWidth(580)
        preview_header = QHBoxLayout()
        preview_header.addWidget(title_label("Preview"))
        preview_header.addStretch(1)
        self.status = status_pill("Select a clip", "#8e9993")
        preview_header.addWidget(self.status)
        right_layout.addLayout(preview_header)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(420)
        self.video.setStyleSheet("background:#000;border:1px solid #1f2522;border-radius:8px")
        self.player.setVideoOutput(self.video)
        right_layout.addWidget(self.video, 1)

        controls = QHBoxLayout()
        self.play = QPushButton("Play")
        self.play.setFixedWidth(72)
        self.play.clicked.connect(self._toggle_play)
        self.position = muted_label("0:00")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.duration = muted_label("0:00")
        controls.addWidget(self.play)
        controls.addWidget(self.position)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.duration)
        right_layout.addLayout(controls)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_changed)

        self.detail_title = QLabel("Choose a clip from the queue")
        self.detail_title.setObjectName("SectionTitle")
        self.detail_reason = muted_label("The normal review flow is one preview followed by approve or reject.", wrap=True)
        right_layout.addWidget(self.detail_title)
        right_layout.addWidget(self.detail_reason)
        meta = QHBoxLayout()
        self.timestamp = muted_label("Timestamp —")
        self.clip_duration = muted_label("Duration —")
        meta.addWidget(self.timestamp)
        meta.addSpacing(20)
        meta.addWidget(self.clip_duration)
        meta.addStretch(1)
        right_layout.addLayout(meta)

        actions = QHBoxLayout()
        self.reject = QPushButton("Reject")
        self.reject.setObjectName("DangerButton")
        self.regenerate = QPushButton("Regenerate")
        self.open_file = QPushButton("Open file")
        self.open_source = QPushButton("Open source")
        self.approve = QPushButton("Approve")
        self.approve.setObjectName("PrimaryButton")
        self.reject.clicked.connect(lambda: self._emit(self.reject_requested))
        self.regenerate.clicked.connect(lambda: self._emit(self.regenerate_requested))
        self.approve.clicked.connect(lambda: self._emit(self.approve_requested))
        self.open_file.clicked.connect(self._open_file)
        self.open_source.clicked.connect(self._open_source)
        actions.addWidget(self.reject)
        actions.addWidget(self.regenerate)
        actions.addStretch(1)
        actions.addWidget(self.open_source)
        actions.addWidget(self.open_file)
        actions.addWidget(self.approve)
        right_layout.addLayout(actions)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 7)
        self._set_actions(False)

    def refresh(self) -> None:
        current_id = self.current["id"] if self.current else None
        self.records = self.repository.list_review()
        self.list.blockSignals(True)
        self.list.clear()
        selected_row = -1
        for index, clip in enumerate(self.records):
            item = QListWidgetItem()
            item.setSizeHint(ReviewItemWidget(clip).sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, ReviewItemWidget(clip))
            if clip["id"] == current_id:
                selected_row = index
        self.list.blockSignals(False)
        self.count.setText(f"{len(self.records)} clip{'s' if len(self.records) != 1 else ''}")
        if self.records:
            self.list.setCurrentRow(selected_row if selected_row >= 0 else 0)
            self._select(self.list.currentRow())
        else:
            self.current = None
            self.player.stop()
            self.player.setSource(QUrl())
            self.status.setText("  ●  Queue empty  ")
            self.detail_title.setText("Nothing waiting for review")
            self.detail_reason.setText("Rendered clips will appear here automatically.")
            self.timestamp.setText("Timestamp —")
            self.clip_duration.setText("Duration —")
            self._set_actions(False)

    def _select(self, row: int) -> None:
        if row < 0 or row >= len(self.records):
            return
        self.current = self.records[row]
        path = Path(self.current["file_path"])
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self.detail_title.setText(self.current["video_title"])
        self.detail_reason.setText(self.current["ai_reason"] or "Gemini did not provide a reason.")
        start = self.current["refined_start_seconds"] or self.current["start_seconds"]
        end = self.current["refined_end_seconds"] or self.current["end_seconds"]
        self.timestamp.setText(f"Source {format_time(start)} → {format_time(end)}")
        self.clip_duration.setText(f"Clip {format_time(self.current['duration_seconds'])}")
        self.status.setText(f"  ●  {round(self.current['ai_score'] or 0)}/100  ")
        self._set_actions(path.exists())

    def _set_actions(self, enabled: bool) -> None:
        for button in (self.reject, self.regenerate, self.open_file, self.open_source, self.approve, self.play):
            button.setEnabled(enabled)

    def _emit(self, signal: Signal) -> None:
        if self.current:
            self.player.pause()
            signal.emit(int(self.current["id"]))

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _playback_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play.setText("Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play")

    def _position_changed(self, position: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(position)
        self.position.setText(format_time(position / 1000))

    def _duration_changed(self, duration: int) -> None:
        self.slider.setRange(0, duration)
        self.duration.setText(format_time(duration / 1000))

    def _open_file(self) -> None:
        if self.current:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.current["file_path"]).resolve().parent)))

    def _open_source(self) -> None:
        if self.current:
            QDesktopServices.openUrl(QUrl(self.current["source_url"]))

