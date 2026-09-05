from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from clipradar.publishing.service import PublishingService
from clipradar.settings.models import UIStateSettings
from clipradar.settings.service import SettingsService
from clipradar.storage.repositories import ChannelRepository, ClipRepository
from clipradar.ui.common import card_layout, format_time, muted_label, status_pill, title_label


REFRAME_OPTIONS = [
    ("Automatic fallback", "auto"),
    ("Important subject", "focus"),
    ("Facecam + gameplay", "gaming_split"),
    ("Keep full context", "contain"),
    ("Simple center crop", "center"),
]


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
        title = QLabel(clip["ai_title"] or clip["video_title"])
        title.setWordWrap(True)
        title.setMaximumHeight(42)
        layout.addWidget(title)
        footer = muted_label(f"{format_time(clip['duration_seconds'])}  ·  {clip['format']}")
        if not Path(clip["file_path"]).is_file():
            footer.setText(f"{footer.text()}  ·  File missing")
            footer.setStyleSheet("color:#ff7070")
        layout.addWidget(footer)


class PublishDialog(QDialog):
    def __init__(self, clip: dict, publishing: PublishingService, parent: QWidget | None = None):
        super().__init__(parent)
        self.clip = clip
        self.publishing = publishing
        self.setWindowTitle("Publish clip to YouTube")
        self.setMinimumSize(680, 650)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)
        root.addWidget(title_label("Review YouTube details"))
        generated = bool(clip["ai_title"] or clip["ai_description"] or clip["ai_tags_json"] != "[]")
        root.addWidget(muted_label(
            "Gemini generated the title, description, and tags. Edit anything before the upload is queued."
            if generated else
            "This clip predates Gemini metadata generation. Complete the fields before upload.",
            wrap=True,
        ))

        form = QFormLayout()
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.account = QComboBox()
        for account in publishing.repos.youtube_accounts.list_all():
            self.account.addItem(account.channel_name, account.id)
        self.title = QLineEdit(clip["ai_title"] or clip["video_title"][:100])
        self.title.setMaxLength(100)
        self.description = QPlainTextEdit(clip["ai_description"] or "")
        self.description.setMinimumHeight(150)
        try:
            tags = json.loads(clip["ai_tags_json"] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            tags = []
        self.tags = QLineEdit(", ".join(str(tag) for tag in tags))
        self.tags.setPlaceholderText("Gemini tags, comma-separated")
        self.privacy = QComboBox()
        self.privacy.addItems(["Private", "Unlisted", "Public"])
        self.privacy.setCurrentText(publishing.settings.publishing().default_privacy)
        self.schedule = QCheckBox("Schedule a public release")
        self.schedule.toggled.connect(self._schedule_toggled)
        self.publish_at = QDateTimeEdit()
        self.publish_at.setCalendarPopup(True)
        self.publish_at.setDisplayFormat("dd.MM.yyyy  HH:mm")
        self.publish_at.setToolTip("Your Windows local time")
        self.publish_at.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        self.publish_at.setMinimumDateTime(QDateTime.currentDateTime().addSecs(120))
        self.publish_at.setEnabled(False)
        timing = QWidget()
        timing_layout = QHBoxLayout(timing)
        timing_layout.setContentsMargins(0, 0, 0, 0)
        timing_layout.setSpacing(10)
        timing_layout.addWidget(self.schedule)
        timing_layout.addWidget(self.publish_at, 1)
        form.addRow("Channel", self.account)
        form.addRow("Title", self.title)
        form.addRow("Description", self.description)
        form.addRow("Tags", self.tags)
        form.addRow("Visibility", self.privacy)
        form.addRow("Timing", timing)
        root.addLayout(form)
        root.addWidget(muted_label(
            "Scheduled videos upload as private first; YouTube makes them public at the selected local time.",
            wrap=True,
        ))
        root.addStretch(1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Approve upload now")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("PrimaryButton")
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self._validate)
        root.addWidget(self.buttons)

    def _schedule_toggled(self, enabled: bool) -> None:
        self.publish_at.setEnabled(enabled)
        self.privacy.setEnabled(not enabled)
        if enabled:
            self.privacy.setCurrentText("Public")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Approve scheduled upload" if enabled else "Approve upload now"
        )

    def _validate(self) -> None:
        if self.account.currentData() is None:
            QMessageBox.warning(self, "No YouTube channel", "Connect a YouTube channel under Settings → Publishing.")
            return
        if not self.title.text().strip():
            QMessageBox.warning(self, "Title required", "Enter a YouTube title before uploading.")
            return
        self.accept()

    def payload(self) -> dict:
        scheduled_for = None
        if self.schedule.isChecked():
            seconds = self.publish_at.dateTime().toSecsSinceEpoch()
            scheduled_for = datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")
        return {
            "account_id": int(self.account.currentData()),
            "title": self.title.text().strip(),
            "description": self.description.toPlainText().strip(),
            "tags": [item.strip() for item in self.tags.text().split(",") if item.strip()],
            "privacy_status": self.privacy.currentText(),
            "scheduled_for": scheduled_for,
        }


class ReviewPage(QWidget):
    approve_requested = Signal(int)
    reject_requested = Signal(int)
    delete_requested = Signal(int)
    regenerate_requested = Signal(int, str)
    publish_requested = Signal(int, dict)

    def __init__(
        self,
        repository: ClipRepository,
        channels: ChannelRepository,
        settings: SettingsService,
        publishing: PublishingService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.repository = repository
        self.channels = channels
        self.settings = settings
        self.publishing = publishing
        self.records: list[dict] = []
        self.current: dict | None = None
        self.selected_channel_id = self.settings.ui_state().review_channel_id
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
        left.setMinimumWidth(260)
        header = QHBoxLayout()
        header.addWidget(title_label("Ready for review"))
        header.addStretch(1)
        self.count = muted_label("0 clips")
        header.addWidget(self.count)
        left_layout.addLayout(header)
        channel_row = QHBoxLayout()
        channel_row.addWidget(muted_label("Channel"))
        self.channel_filter = QComboBox()
        self.channel_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.channel_filter.setMinimumContentsLength(18)
        self.channel_filter.currentIndexChanged.connect(self._channel_changed)
        channel_row.addWidget(self.channel_filter, 1)
        left_layout.addLayout(channel_row)
        self.list = QListWidget()
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSpacing(8)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.currentRowChanged.connect(self._select)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        left_layout.addWidget(self.list, 1)
        splitter.addWidget(left)

        right, right_layout = card_layout(object_name="Card")
        right.setMinimumWidth(480)
        preview_header = QHBoxLayout()
        preview_header.addWidget(title_label("Preview"))
        preview_header.addStretch(1)
        self.status = status_pill("Select a clip", "#8e9993")
        preview_header.addWidget(self.status)
        right_layout.addLayout(preview_header)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(350)
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
        self.detail_reason = muted_label("Preview the clip, then approve, reject, or publish it.", wrap=True)
        right_layout.addWidget(self.detail_title)
        right_layout.addWidget(self.detail_reason)
        meta = QHBoxLayout()
        self.timestamp = muted_label("Timestamp —")
        self.clip_duration = muted_label("Duration —")
        self.metadata_status = muted_label("Metadata —")
        meta.addWidget(self.timestamp)
        meta.addSpacing(20)
        meta.addWidget(self.clip_duration)
        meta.addSpacing(20)
        meta.addWidget(self.metadata_status)
        meta.addStretch(1)
        right_layout.addLayout(meta)

        edit_row = QHBoxLayout()
        edit_row.addWidget(muted_label("Vertical framing"))
        self.reframe = QComboBox()
        for label, value in REFRAME_OPTIONS:
            self.reframe.addItem(label, value)
        edit_row.addWidget(self.reframe, 1)
        self.regenerate = QPushButton("Regenerate framing")
        self.regenerate.setToolTip(
            "Gemini compares the original segment with this render and creates a new framing plan."
        )
        self.regenerate.clicked.connect(self._emit_regenerate)
        edit_row.addWidget(self.regenerate)
        right_layout.addLayout(edit_row)

        self.reject = QPushButton("Reject")
        self.reject.setObjectName("DangerButton")
        self.more = QPushButton("More")
        more_menu = QMenu(self.more)
        self.open_source = more_menu.addAction("Open source")
        self.open_file = more_menu.addAction("Open file location")
        more_menu.addSeparator()
        self.delete_clip = more_menu.addAction("Delete clip permanently…")
        self.more.setMenu(more_menu)
        self.approve = QPushButton("Approve only")
        self.publish = QPushButton("Publish…")
        self.publish.setObjectName("PrimaryButton")
        self.reject.clicked.connect(lambda: self._emit(self.reject_requested))
        self.approve.clicked.connect(lambda: self._emit(self.approve_requested))
        self.publish.clicked.connect(self._publish)
        self.open_file.triggered.connect(self._open_file)
        self.open_source.triggered.connect(self._open_source)
        self.delete_clip.triggered.connect(self._confirm_delete)

        actions = QHBoxLayout()
        actions.addWidget(self.reject)
        actions.addWidget(self.more)
        actions.addStretch(1)
        actions.addWidget(self.approve)
        actions.addWidget(self.publish)
        right_layout.addLayout(actions)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 7)
        self._set_actions(False, False)

    def refresh(self) -> None:
        current_id = self.current["id"] if self.current else None
        all_records = self.repository.list_review()
        channels = self.channels.list_all()
        valid_channel_ids = {int(channel.id) for channel in channels if channel.id is not None}
        if self.selected_channel_id not in valid_channel_ids:
            self.selected_channel_id = int(channels[0].id) if channels else None
        counts: dict[int, int] = {}
        for clip in all_records:
            channel_id = int(clip["channel_id"])
            counts[channel_id] = counts.get(channel_id, 0) + 1
        self.channel_filter.blockSignals(True)
        self.channel_filter.clear()
        for channel in channels:
            channel_id = int(channel.id)
            count = counts.get(channel_id, 0)
            self.channel_filter.addItem(f"{channel.name}  ·  {count}", channel_id)
        if self.selected_channel_id is not None:
            index = self.channel_filter.findData(self.selected_channel_id)
            self.channel_filter.setCurrentIndex(index if index >= 0 else 0)
        self.channel_filter.setEnabled(bool(channels))
        self.channel_filter.blockSignals(False)
        self.records = [
            clip for clip in all_records
            if self.selected_channel_id is not None and int(clip["channel_id"]) == self.selected_channel_id
        ]
        self.list.blockSignals(True)
        self.list.clear()
        selected_row = -1
        for index, clip in enumerate(self.records):
            item = QListWidgetItem()
            item_widget = ReviewItemWidget(clip)
            item.setSizeHint(item_widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, item_widget)
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
            self._set_status("Queue empty", "#8e9993")
            self.detail_title.setText("Nothing waiting for review")
            self.detail_reason.setText("Rendered clips will appear here automatically.")
            self.timestamp.setText("Timestamp —")
            self.clip_duration.setText("Duration —")
            self.metadata_status.setText("Metadata —")
            self._set_actions(False, False)

    def _channel_changed(self, index: int) -> None:
        channel_id = self.channel_filter.itemData(index) if index >= 0 else None
        if channel_id is None:
            return
        self.selected_channel_id = int(channel_id)
        self.settings.save_ui_state(UIStateSettings(review_channel_id=self.selected_channel_id))
        self.current = None
        self.refresh()

    def _select(self, row: int) -> None:
        if row < 0 or row >= len(self.records):
            return
        self.current = self.records[row]
        path = Path(self.current["file_path"])
        has_file = path.is_file()
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path.resolve())) if has_file else QUrl())
        self.detail_title.setText(self.current["ai_title"] or self.current["video_title"])
        reason = self.current["ai_reason"] or "Gemini did not provide a reason."
        if not has_file:
            reason = f"The rendered file was removed outside ClipRadar. You can reject or permanently delete this entry.\n\n{reason}"
        self.detail_reason.setText(reason)
        start = self.current["refined_start_seconds"] or self.current["start_seconds"]
        end = self.current["refined_end_seconds"] or self.current["end_seconds"]
        self.timestamp.setText(f"Source {format_time(start)} → {format_time(end)}")
        self.clip_duration.setText(f"Clip {format_time(self.current['duration_seconds'])}")
        if self.current.get("status") == "Regenerating":
            self._set_status("Regenerating…", "#caff00")
        else:
            self._set_status(
                "File missing" if not has_file else f"{round(self.current['ai_score'] or 0)}/100",
                "#ff7070" if not has_file else "#caff00",
            )
        mode_index = self.reframe.findData(self.current.get("reframe_mode") or "auto")
        self.reframe.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        generated = bool(
            self.current["ai_title"]
            or self.current["ai_description"]
            or self.current["ai_tags_json"] != "[]"
        )
        self.metadata_status.setText("Gemini metadata ready" if generated else "Metadata needs review")
        self._set_actions(True, has_file)

    def _set_actions(self, has_record: bool, has_file: bool) -> None:
        regenerating = bool(self.current and self.current.get("status") == "Regenerating")
        can_decide = has_record and not regenerating
        self.reject.setEnabled(can_decide)
        self.more.setEnabled(has_record)
        self.open_source.setEnabled(has_record)
        self.open_file.setEnabled(has_file)
        self.delete_clip.setEnabled(can_decide)
        for button in (self.regenerate, self.approve, self.play):
            button.setEnabled(can_decide and has_file)
        has_account = bool(self.publishing.repos.youtube_accounts.list_all())
        self.publish.setEnabled(can_decide and has_file and has_account)
        self.publish.setToolTip("" if has_account else "Connect YouTube under Settings → Publishing first")
        self.reframe.setEnabled(can_decide and has_file)

    def _set_status(self, text: str, color: str) -> None:
        self.status.setText(f"  ●  {text}  ")
        self.status.setStyleSheet(
            f"color:{color}; background:#111612; border:1px solid #2a322d; "
            "border-radius:11px; padding:3px 7px;"
        )

    def _emit(self, signal: Signal) -> None:
        if self.current:
            self.player.pause()
            signal.emit(int(self.current["id"]))

    def _emit_regenerate(self) -> None:
        if self.current:
            self.player.pause()
            self.regenerate_requested.emit(int(self.current["id"]), str(self.reframe.currentData()))

    def _show_context_menu(self, point) -> None:
        item = self.list.itemAt(point)
        if item is None:
            return
        self.list.setCurrentItem(item)
        menu = QMenu(self.list)
        action = menu.addAction("Delete clip permanently…")
        action.setEnabled(bool(self.current and self.current.get("status") != "Regenerating"))
        action.triggered.connect(self._confirm_delete)
        menu.exec(self.list.viewport().mapToGlobal(point))

    def _confirm_delete(self) -> None:
        if not self.current:
            return
        path = Path(self.current["file_path"])
        file_note = (
            "The local MP4 file will also be deleted."
            if path.is_file()
            else "The local file is already missing; its queue record will be removed."
        )
        answer = QMessageBox.warning(
            self,
            "Permanently delete clip?",
            f"{file_note}\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            clip_id = int(self.current["id"])
            self.player.stop()
            self.player.setSource(QUrl())
            self.delete_requested.emit(clip_id)

    def _publish(self) -> None:
        if not self.current:
            return
        self.player.pause()
        dialog = PublishDialog(self.current, self.publishing, self)
        if dialog.exec():
            self.publish_requested.emit(int(self.current["id"]), dialog.payload())

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
