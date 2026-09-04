from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from clipradar.models import Channel
from clipradar.storage.repositories import ChannelRepository
from clipradar.ui.common import card_layout, clear_layout, format_timestamp, muted_label, status_pill, title_label


class ChannelSettingsDialog(QDialog):
    def __init__(self, channel: Channel, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{channel.name} settings")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(14)
        self.delay = QSpinBox()
        self.delay.setRange(0, 24 * 60)
        self.delay.setSuffix(" min")
        self.delay.setValue(channel.analysis_delay_minutes)
        self.max_clips = QSpinBox()
        self.max_clips.setRange(1, 10)
        self.max_clips.setValue(channel.max_clips_per_video)
        self.minimum = QSpinBox(); self.minimum.setRange(5, 180); self.minimum.setValue(channel.min_duration_seconds); self.minimum.setSuffix(" sec")
        self.target = QSpinBox(); self.target.setRange(5, 180); self.target.setValue(channel.target_duration_seconds); self.target.setSuffix(" sec")
        self.maximum = QSpinBox(); self.maximum.setRange(5, 180); self.maximum.setValue(channel.max_duration_seconds); self.maximum.setSuffix(" sec")
        form.addRow("Analysis delay", self.delay)
        form.addRow("Maximum clips", self.max_clips)
        form.addRow("Minimum duration", self.minimum)
        form.addRow("Target duration", self.target)
        form.addRow("Maximum duration", self.maximum)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        if not self.minimum.value() <= self.target.value() <= self.maximum.value():
            QMessageBox.warning(self, "Invalid duration", "Use minimum ≤ target ≤ maximum.")
            return
        self.accept()

    def values(self) -> dict[str, int]:
        return {
            "analysis_delay_minutes": self.delay.value(),
            "max_clips_per_video": self.max_clips.value(),
            "min_duration_seconds": self.minimum.value(),
            "target_duration_seconds": self.target.value(),
            "max_duration_seconds": self.maximum.value(),
        }


class ChannelCard(QFrame):
    toggle_requested = Signal(int, bool)
    latest_requested = Signal(int)
    specific_requested = Signal(int, str)
    remove_requested = Signal(int)
    update_requested = Signal(int, dict)

    def __init__(self, channel: Channel, parent: QWidget | None = None):
        super().__init__(parent)
        self.channel = channel
        self.setObjectName("ChannelCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 17, 20, 17)
        layout.setSpacing(12)
        top = QHBoxLayout()
        avatar = QLabel(channel.name[:1].upper())
        avatar.setFixedSize(42, 42)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet("font-size:18px;font-weight:700;background:#1a211d;border:1px solid #354038;border-radius:21px;color:#caff00")
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(title_label(channel.name))
        names.addWidget(muted_label(channel.url))
        top.addWidget(avatar)
        top.addSpacing(4)
        top.addLayout(names, 1)
        top.addWidget(status_pill("Active" if channel.monitoring_enabled else "Paused", "#caff00" if channel.monitoring_enabled else "#8e9993"))
        layout.addLayout(top)
        details = muted_label(
            f"Checked {format_timestamp(channel.last_checked_at)}   ·   Delay {channel.analysis_delay_minutes} min   ·   Up to {channel.max_clips_per_video} clips"
        )
        layout.addWidget(details)
        actions = QHBoxLayout()
        analyze = QPushButton("Analyze latest")
        analyze.setObjectName("PrimaryButton")
        analyze.clicked.connect(lambda: self.latest_requested.emit(int(channel.id)))
        specific = QPushButton("Specific video")
        specific.clicked.connect(self._specific)
        pause = QPushButton("Pause" if channel.monitoring_enabled else "Resume")
        pause.clicked.connect(lambda: self.toggle_requested.emit(int(channel.id), not channel.monitoring_enabled))
        settings = QPushButton("Settings")
        settings.clicked.connect(self._settings)
        remove = QPushButton("Remove")
        remove.setObjectName("DangerButton")
        remove.clicked.connect(self._remove)
        actions.addWidget(analyze)
        actions.addWidget(specific)
        actions.addStretch(1)
        actions.addWidget(pause)
        actions.addWidget(settings)
        actions.addWidget(remove)
        layout.addLayout(actions)

    def _specific(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Analyze specific video")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        layout.addWidget(title_label("YouTube video URL"))
        field = QLineEdit()
        field.setPlaceholderText("https://www.youtube.com/watch?v=…")
        layout.addWidget(field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        if dialog.exec() and field.text().strip():
            self.specific_requested.emit(int(self.channel.id), field.text().strip())

    def _settings(self) -> None:
        dialog = ChannelSettingsDialog(self.channel, self)
        if dialog.exec():
            self.update_requested.emit(int(self.channel.id), dialog.values())

    def _remove(self) -> None:
        answer = QMessageBox.question(
            self,
            "Remove channel?",
            f"Remove {self.channel.name} and its local ClipRadar records? Rendered files on disk are kept.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_requested.emit(int(self.channel.id))


class ChannelsPage(QWidget):
    add_requested = Signal(str)
    toggle_requested = Signal(int, bool)
    latest_requested = Signal(int)
    specific_requested = Signal(int, str)
    remove_requested = Signal(int)
    update_requested = Signal(int, dict)

    def __init__(self, repository: ChannelRepository, parent: QWidget | None = None):
        super().__init__(parent)
        self.repository = repository
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        add_card, add_layout = card_layout(object_name="Toolbar")
        row = QHBoxLayout()
        self.channel_input = QLineEdit()
        self.channel_input.setPlaceholderText("Channel URL, @handle, channel ID, or a video URL")
        self.channel_input.returnPressed.connect(self._add)
        self.add_button = QPushButton("Add channel")
        self.add_button.setObjectName("PrimaryButton")
        self.add_button.clicked.connect(self._add)
        row.addWidget(self.channel_input, 1)
        row.addWidget(self.add_button)
        add_layout.addLayout(row)
        root.addWidget(add_card)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.cards = QVBoxLayout(self.container)
        self.cards.setContentsMargins(0, 0, 4, 0)
        self.cards.setSpacing(12)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)

    def _add(self) -> None:
        value = self.channel_input.text().strip()
        if value:
            self.add_button.setDisabled(True)
            self.add_button.setText("Resolving…")
            self.add_requested.emit(value)

    def finish_add(self, success: bool) -> None:
        self.add_button.setEnabled(True)
        self.add_button.setText("Add channel")
        if success:
            self.channel_input.clear()

    def refresh(self) -> None:
        clear_layout(self.cards)
        channels = self.repository.list_all()
        if not channels:
            empty, layout = card_layout()
            empty.setMinimumHeight(260)
            layout.addStretch(1)
            label = title_label("No channels yet")
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(label)
            description = muted_label("Add a YouTube channel above. ClipRadar records the current latest upload as a safe baseline.", wrap=True)
            description.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(description)
            layout.addStretch(1)
            self.cards.addWidget(empty)
        for channel in channels:
            card = ChannelCard(channel)
            card.toggle_requested.connect(self.toggle_requested)
            card.latest_requested.connect(self.latest_requested)
            card.specific_requested.connect(self.specific_requested)
            card.remove_requested.connect(self.remove_requested)
            card.update_requested.connect(self.update_requested)
            self.cards.addWidget(card)
        self.cards.addStretch(1)
