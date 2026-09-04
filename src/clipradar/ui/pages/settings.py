from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from clipradar.ai.gemini import GEMINI_MODELS
from clipradar.app.paths import AppPaths
from clipradar.settings.models import (
    AISettings,
    BudgetSettings,
    ClipSettings,
    GeneralSettings,
    MonitoringSettings,
    StorageSettings,
)
from clipradar.settings.service import SettingsService
from clipradar.ui.common import card_layout, muted_label, title_label


class SettingsPage(QWidget):
    saved = Signal(str)
    failed = Signal(str)
    test_ai_requested = Signal(str, str)

    def __init__(self, settings: SettingsService, paths: AppPaths, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.paths = paths
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        nav_frame, nav_layout = card_layout()
        nav_frame.setFixedWidth(205)
        nav_layout.addWidget(title_label("Settings"))
        nav_layout.addWidget(muted_label("Simple defaults first."))
        self.nav = QListWidget()
        self.nav.addItems(["General", "Monitoring", "Clips", "AI", "Budget", "Storage"])
        nav_layout.addWidget(self.nav, 1)
        root.addWidget(nav_frame)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self._build_general()
        self._build_monitoring()
        self._build_clips()
        self._build_ai()
        self._build_budget()
        self._build_storage()
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.load()

    def _page(self, title: str, description: str) -> tuple[QWidget, QVBoxLayout, QFormLayout]:
        page, layout = card_layout()
        layout.setContentsMargins(28, 25, 28, 25)
        layout.addWidget(title_label(title))
        layout.addWidget(muted_label(description, wrap=True))
        layout.addSpacing(8)
        form = QFormLayout()
        form.setHorizontalSpacing(28)
        form.setVerticalSpacing(16)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        layout.addLayout(form)
        layout.addStretch(1)
        self.pages.addWidget(page)
        return page, layout, form

    @staticmethod
    def _save_button(layout: QVBoxLayout, callback) -> QPushButton:
        row = QHBoxLayout()
        row.addStretch(1)
        button = QPushButton("Save")
        button.setObjectName("PrimaryButton")
        button.clicked.connect(callback)
        row.addWidget(button)
        layout.addLayout(row)
        return button

    def _build_general(self) -> None:
        _, layout, form = self._page("General", "How ClipRadar behaves when the desktop application starts.")
        self.start_monitoring = QCheckBox("Start monitoring while ClipRadar is open")
        form.addRow("Monitoring", self.start_monitoring)
        self._save_button(layout, self._save_general)

    def _build_monitoring(self) -> None:
        _, layout, form = self._page("Monitoring", "Lightweight upload checks. No AI is used during channel monitoring.")
        self.monitor_interval = QSpinBox(); self.monitor_interval.setRange(1, 1440); self.monitor_interval.setSuffix(" min")
        self.scan_items = QSpinBox(); self.scan_items.setRange(3, 50)
        self.max_new = QSpinBox(); self.max_new.setRange(1, 10)
        self.default_delay = QSpinBox(); self.default_delay.setRange(0, 1440); self.default_delay.setSuffix(" min")
        form.addRow("Check interval", self.monitor_interval)
        form.addRow("Recent items checked", self.scan_items)
        form.addRow("Maximum new videos/check", self.max_new)
        form.addRow("Default analysis delay", self.default_delay)
        self._save_button(layout, self._save_monitoring)

    def _build_clips(self) -> None:
        _, layout, form = self._page("Clips", "Global defaults for newly added channels and rendered short-form clips.")
        self.minimum = QSpinBox(); self.minimum.setRange(5, 180); self.minimum.setSuffix(" sec")
        self.target = QSpinBox(); self.target.setRange(5, 180); self.target.setSuffix(" sec")
        self.maximum = QSpinBox(); self.maximum.setRange(5, 180); self.maximum.setSuffix(" sec")
        self.max_clips = QSpinBox(); self.max_clips.setRange(1, 10)
        self.max_candidates = QSpinBox(); self.max_candidates.setRange(3, 30)
        self.output_format = QComboBox(); self.output_format.addItems(["Vertical 9:16", "Original / 16:9"])
        self.captions = QCheckBox("Burn modern captions into rendered clips")
        self.word_highlighting = QCheckBox("Highlight words as they are spoken")
        self.normalize_audio = QCheckBox("Normalize output loudness")
        form.addRow("Minimum duration", self.minimum)
        form.addRow("Target duration", self.target)
        form.addRow("Maximum duration", self.maximum)
        form.addRow("Maximum clips/video", self.max_clips)
        form.addRow("Local candidates/video", self.max_candidates)
        form.addRow("Output", self.output_format)
        form.addRow("Captions", self.captions)
        form.addRow("Word highlighting", self.word_highlighting)
        form.addRow("Audio", self.normalize_audio)
        self._save_button(layout, self._save_clips)

    def _build_ai(self) -> None:
        _, layout, form = self._page("AI", "Gemini ranks only locally preselected candidate windows. The key stays in your OS credential vault.")
        self.provider = QComboBox(); self.provider.addItem("Gemini")
        self.model = QComboBox()
        for label, identifier in GEMINI_MODELS:
            self.model.addItem(label, identifier)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Enter Gemini API key")
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(8)
        key_layout.addWidget(self.api_key, 1)
        self.show_key = QCheckBox("Show")
        self.show_key.toggled.connect(
            lambda shown: self.api_key.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)
        )
        key_layout.addWidget(self.show_key)
        self.temperature = QDoubleSpinBox(); self.temperature.setRange(0, 1); self.temperature.setSingleStep(0.05); self.temperature.setDecimals(2)
        self.minimum_score = QSpinBox(); self.minimum_score.setRange(0, 100); self.minimum_score.setSuffix(" / 100")
        form.addRow("Provider", self.provider)
        form.addRow("Model", self.model)
        form.addRow("Gemini API key", key_row)
        form.addRow("Ranking temperature", self.temperature)
        form.addRow("Minimum clip score", self.minimum_score)
        self.ai_status = QLabel("Not tested")
        self.ai_status.setObjectName("Muted")
        form.addRow("Connection", self.ai_status)
        actions = QHBoxLayout()
        self.test_ai = QPushButton("Test connection")
        self.test_ai.clicked.connect(self._test_ai)
        self.save_ai = QPushButton("Save")
        self.save_ai.setObjectName("PrimaryButton")
        self.save_ai.clicked.connect(self._save_ai)
        actions.addStretch(1)
        actions.addWidget(self.test_ai)
        actions.addWidget(self.save_ai)
        layout.addLayout(actions)

    def _build_budget(self) -> None:
        _, layout, form = self._page("Budget", "Hard daily gates are checked before any paid Gemini analysis begins.")
        self.budget_videos = QSpinBox(); self.budget_videos.setRange(1, 1000); self.budget_videos.setSuffix(" videos")
        self.budget_minutes = QSpinBox(); self.budget_minutes.setRange(1, 100000); self.budget_minutes.setSuffix(" min")
        self.budget_clips = QSpinBox(); self.budget_clips.setRange(1, 20)
        self.budget_cost = QDoubleSpinBox(); self.budget_cost.setRange(0.01, 10000); self.budget_cost.setDecimals(2); self.budget_cost.setPrefix("€ ")
        form.addRow("Maximum videos/day", self.budget_videos)
        form.addRow("Maximum source minutes/day", self.budget_minutes)
        form.addRow("Maximum clips/video", self.budget_clips)
        form.addRow("Maximum AI cost/day", self.budget_cost)
        self._save_button(layout, self._save_budget)

    def _build_storage(self) -> None:
        _, layout, form = self._page("Storage", f"Application data is stored under {self.paths.root}.")
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText(str(self.paths.output))
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_output)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.output_dir, 1)
        row_layout.addWidget(browse)
        self.keep_sources = QCheckBox("Keep downloaded source videos")
        self.keep_previews = QCheckBox("Keep low-resolution Gemini candidate previews")
        form.addRow("Output folder", row)
        form.addRow("Sources", self.keep_sources)
        form.addRow("Debug previews", self.keep_previews)
        self._save_button(layout, self._save_storage)

    def load(self) -> None:
        general = self.settings.general()
        self.start_monitoring.setChecked(general.start_monitoring_on_launch)
        monitoring = self.settings.monitoring()
        self.monitor_interval.setValue(monitoring.interval_minutes)
        self.scan_items.setValue(monitoring.max_items_per_channel_check)
        self.max_new.setValue(monitoring.max_new_videos_per_check)
        self.default_delay.setValue(monitoring.default_analysis_delay_minutes)
        clips = self.settings.clips()
        self.minimum.setValue(clips.minimum_duration)
        self.target.setValue(clips.target_duration)
        self.maximum.setValue(clips.maximum_duration)
        self.max_clips.setValue(clips.max_clips_per_video)
        self.max_candidates.setValue(clips.max_candidates_per_video)
        self.output_format.setCurrentText(clips.output_format)
        self.captions.setChecked(clips.captions_enabled)
        self.word_highlighting.setChecked(clips.word_highlighting)
        self.normalize_audio.setChecked(clips.audio_normalization)
        ai = self.settings.ai()
        model_index = self.model.findData(ai.model)
        if model_index < 0:
            self.model.addItem(ai.model, ai.model)
            model_index = self.model.count() - 1
        self.model.setCurrentIndex(model_index)
        self.temperature.setValue(ai.temperature)
        self.minimum_score.setValue(ai.minimum_ai_score)
        try:
            self.api_key.setText(self.settings.secrets.get_gemini_key() or "")
        except RuntimeError as exc:
            self.ai_status.setObjectName("Error")
            self.ai_status.setText(str(exc))
        budget = self.settings.budget()
        self.budget_videos.setValue(budget.max_videos_per_day)
        self.budget_minutes.setValue(budget.max_source_minutes_per_day)
        self.budget_clips.setValue(budget.max_clips_per_video)
        self.budget_cost.setValue(budget.max_ai_cost_per_day_eur)
        storage = self.settings.storage()
        self.output_dir.setText(storage.output_directory)
        self.keep_sources.setChecked(storage.keep_source_videos)
        self.keep_previews.setChecked(storage.keep_candidate_previews)

    def set_connection_status(self, message: str, success: bool) -> None:
        self.test_ai.setEnabled(True)
        self.test_ai.setText("Test connection")
        self.ai_status.setObjectName("Success" if success else "Error")
        self.ai_status.setText(message)
        self.ai_status.style().unpolish(self.ai_status)
        self.ai_status.style().polish(self.ai_status)

    def _save_general(self) -> None:
        self._perform_save(
            lambda: self.settings.save_general(GeneralSettings(self.start_monitoring.isChecked(), False)),
            "General settings saved",
        )

    def _save_monitoring(self) -> None:
        self._perform_save(
            lambda: self.settings.save_monitoring(MonitoringSettings(
                self.monitor_interval.value(), self.scan_items.value(), self.max_new.value(), self.default_delay.value()
            )),
            "Monitoring settings saved",
        )

    def _save_clips(self) -> None:
        self._perform_save(
            lambda: self.settings.save_clips(ClipSettings(
                minimum_duration=self.minimum.value(),
                target_duration=self.target.value(),
                maximum_duration=self.maximum.value(),
                max_clips_per_video=self.max_clips.value(),
                max_candidates_per_video=self.max_candidates.value(),
                output_format=self.output_format.currentText(),
                captions_enabled=self.captions.isChecked(),
                word_highlighting=self.word_highlighting.isChecked(),
                audio_normalization=self.normalize_audio.isChecked(),
            )),
            "Clip defaults saved",
        )

    def _save_ai(self) -> None:
        def save() -> None:
            self.settings.save_ai(AISettings(
                provider="Gemini", model=str(self.model.currentData()), temperature=self.temperature.value(),
                minimum_ai_score=self.minimum_score.value(),
            ))
            self.settings.secrets.set_gemini_key(self.api_key.text())
        self._perform_save(save, "AI settings saved securely")

    def _test_ai(self) -> None:
        self.test_ai.setDisabled(True)
        self.test_ai.setText("Testing…")
        self.ai_status.setObjectName("Muted")
        self.ai_status.setText("Connecting to Gemini…")
        self.test_ai_requested.emit(self.api_key.text(), str(self.model.currentData()))

    def _save_budget(self) -> None:
        self._perform_save(
            lambda: self.settings.save_budget(BudgetSettings(
                self.budget_videos.value(), self.budget_minutes.value(), self.budget_clips.value(), self.budget_cost.value()
            )),
            "Daily budget limits saved",
        )

    def _save_storage(self) -> None:
        def save() -> None:
            value = self.output_dir.text().strip()
            if value:
                Path(value).expanduser().mkdir(parents=True, exist_ok=True)
            self.settings.save_storage(StorageSettings(value, self.keep_sources.isChecked(), self.keep_previews.isChecked()))
        self._perform_save(save, "Storage settings saved")

    def _perform_save(self, operation, message: str) -> None:
        try:
            operation()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.saved.emit(message)

    def _browse_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose ClipRadar output folder", self.output_dir.text() or str(self.paths.output))
        if selected:
            self.output_dir.setText(selected)
