from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from clipradar.models import ClipCandidate, FramingProfile, SourceVideo
from clipradar.rendering.layout import resolved_output_regions
from clipradar.storage.repositories import Repositories
from clipradar.ui.common import card_layout, format_time, muted_label, title_label
from clipradar.ui.framing_widgets import LiveFramingPreview, REGION_SPECS, RegionCanvas


class FramingSetupPage(QWidget):
    back_requested = Signal()
    auto_detect_requested = Signal(object)
    test_requested = Signal(object)
    saved = Signal(str)

    def __init__(self, repositories: Repositories, parent: QWidget | None = None):
        super().__init__(parent)
        self.repos = repositories
        self.channel_id: int | None = None
        self.source: SourceVideo | None = None
        self.candidate: ClipCandidate | None = None
        self.reference_seconds = 0.0
        self._frame: QPixmap | None = None
        self._drafts: dict[str, FramingProfile] = {}
        self._current_mode = "gaming_split"
        self._selection_instructions = ""
        self._test_path: Path | None = None
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.75)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        header = QHBoxLayout()
        self.back = QPushButton("←  Back")
        self.back.clicked.connect(self._back)
        header.addWidget(self.back)
        header.addSpacing(8)
        details = QVBoxLayout()
        details.setSpacing(2)
        self.channel_title = title_label("Framing profile")
        self.source_title = muted_label("Choose a channel or review clip")
        details.addWidget(self.channel_title)
        details.addWidget(self.source_title)
        header.addLayout(details)
        header.addStretch(1)
        header.addWidget(muted_label("Profile"))
        self.mode = QComboBox()
        self.mode.addItem("Facecam + gameplay", "gaming_split")
        self.mode.addItem("Important subject", "focus")
        self.mode.setMinimumWidth(210)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        header.addWidget(self.mode)
        root.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("FramingTabs")
        self.tabs.addTab(self._layout_tab(), "Layout")
        self.tabs.addTab(self._instructions_tab(), "Gemini instructions")
        root.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        self.state = muted_label("Open a reviewed clip to use its source as a reference.", wrap=True)
        footer.addWidget(self.state, 1)
        self.auto_detect = QPushButton("Auto-detect with Gemini")
        self.auto_detect.clicked.connect(self._auto_detect)
        footer.addWidget(self.auto_detect)
        self.test = QPushButton("Test current clip")
        self.test.clicked.connect(self._test)
        footer.addWidget(self.test)
        self.save = QPushButton("Save for channel")
        self.save.setObjectName("PrimaryButton")
        self.save.clicked.connect(self._save)
        footer.addWidget(self.save)
        root.addLayout(footer)

    def _layout_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(10)
        tools = QHBoxLayout()
        tools.addWidget(muted_label("Select a layer, then adjust its source box and portrait slot"))
        tools.addSpacing(8)
        self.region_group = QButtonGroup(self)
        self.region_group.setExclusive(True)
        self.region_buttons: dict[str, QPushButton] = {}
        for key, (label, _color, _default) in REGION_SPECS.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, region=key: self._select_region(region))
            self.region_group.addButton(button)
            self.region_buttons[key] = button
            tools.addWidget(button)
        self.region_buttons["facecam"].setChecked(True)
        self.remove_region = QPushButton("Remove selected")
        self.remove_region.clicked.connect(self._remove_active)
        tools.addWidget(self.remove_region)
        tools.addStretch(1)
        layout.addLayout(tools)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        source_card, source_layout = card_layout(object_name="InsetCard")
        source_header = QHBoxLayout()
        source_header.addWidget(title_label("Original source frame"))
        source_header.addStretch(1)
        self.time_label = muted_label("0:00")
        source_header.addWidget(self.time_label)
        source_layout.addLayout(source_header)
        self.canvas = RegionCanvas()
        self.canvas.regions_changed.connect(self._regions_changed)
        self.canvas.active_changed.connect(self._active_changed)
        source_layout.addWidget(self.canvas, 1)
        timeline = QHBoxLayout()
        timeline.addWidget(muted_label("Reference frame"))
        self.source_time = QSlider(Qt.Orientation.Horizontal)
        self.source_time.setRange(0, 0)
        self.source_time.sliderReleased.connect(self._load_reference_frame)
        self.source_time.valueChanged.connect(
            lambda value: self.time_label.setText(format_time(value / 1000))
        )
        timeline.addWidget(self.source_time, 1)
        source_layout.addLayout(timeline)
        splitter.addWidget(source_card)

        preview_card, preview_layout = card_layout(object_name="InsetCard")
        preview_header = QHBoxLayout()
        preview_header.addWidget(title_label("Live 9:16 preview"))
        preview_header.addStretch(1)
        self.preview_badge = muted_label("Layout preview")
        preview_header.addWidget(self.preview_badge)
        preview_layout.addLayout(preview_header)
        self.preview_stack = QStackedWidget()
        self.live_preview = LiveFramingPreview()
        self.live_preview.regions_changed.connect(self._output_regions_changed)
        self.live_preview.active_changed.connect(self._active_changed)
        self.test_video = QVideoWidget()
        self.test_video.setStyleSheet("background:#000;border:1px solid #242b27;border-radius:8px")
        self.player.setVideoOutput(self.test_video)
        self.preview_stack.addWidget(self.live_preview)
        self.preview_stack.addWidget(self.test_video)
        preview_layout.addWidget(self.preview_stack, 1, Qt.AlignmentFlag.AlignHCenter)
        splitter.addWidget(preview_card)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([820, 360])
        layout.addWidget(splitter, 1)
        return tab

    def _instructions_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(12)
        intro, intro_layout = card_layout(object_name="InsetCard")
        intro_layout.addWidget(title_label("Built-in safety and rendering rules"))
        intro_layout.addWidget(muted_label(
            "ClipRadar always keeps the selected mode, valid normalized coordinates, stable output proportions, "
            "and safe rendering constraints. These technical rules cannot be overridden.",
            wrap=True,
        ))
        layout.addWidget(intro)
        prompts = QSplitter(Qt.Orientation.Horizontal)
        selection_card, selection_layout = card_layout(object_name="InsetCard")
        selection_layout.addWidget(title_label("Clip selection instructions"))
        selection_layout.addWidget(muted_label(
            "Channel-wide. Describe the moments, scenes, reactions, topics, or pacing you want Gemini to "
            "prefer or avoid when scoring clip candidates.",
            wrap=True,
        ))
        self.selection_instructions = QPlainTextEdit()
        self.selection_instructions.setPlaceholderText(
            "Example: Prefer surprising outplays with a clear reaction and payoff. Avoid slow build-up, "
            "routine farming, and moments that require earlier context."
        )
        self.selection_instructions.setMinimumHeight(230)
        self.selection_instructions.textChanged.connect(self._selection_instructions_changed)
        selection_layout.addWidget(self.selection_instructions, 1)
        self.selection_character_count = muted_label("0 / 4000")
        selection_layout.addWidget(
            self.selection_character_count, 0, Qt.AlignmentFlag.AlignRight
        )
        prompts.addWidget(selection_card)

        prompt_card, prompt_layout = card_layout(object_name="InsetCard")
        prompt_layout.addWidget(title_label("Framing instructions"))
        prompt_layout.addWidget(muted_label(
            "Profile-specific. Describe recurring layout details, what must stay visible, and what Gemini "
            "should avoid for the selected framing mode.",
            wrap=True,
        ))
        self.instructions = QPlainTextEdit()
        self.instructions.setPlaceholderText(
            "Example: Keep the facecam compact at the top and preserve the nearby scoreboard. "
            "Gameplay should fill most of the vertical frame."
        )
        self.instructions.setMinimumHeight(230)
        self.instructions.textChanged.connect(self._instructions_changed)
        prompt_layout.addWidget(self.instructions, 1)
        self.character_count = muted_label("0 / 4000")
        prompt_layout.addWidget(self.character_count, 0, Qt.AlignmentFlag.AlignRight)
        prompts.addWidget(prompt_card)
        prompts.setSizes([600, 600])
        layout.addWidget(prompts, 1)
        return tab

    def open_from_clip(self, clip_id: int) -> None:
        clip = self.repos.clips.get(clip_id)
        if not clip:
            raise ValueError("The selected review clip no longer exists.")
        source = self.repos.videos.get(clip.source_video_id)
        candidate = self.repos.candidates.get(clip.candidate_id)
        if not source or not candidate:
            raise ValueError("The source data for this clip is incomplete.")
        initial_mode = candidate.reframe_mode if candidate.reframe_mode in {"gaming_split", "focus"} else "gaming_split"
        self._open(source.channel_id, source, candidate, initial_mode)

    def open_for_channel(self, channel_id: int) -> None:
        profiles = self.repos.framing_profiles.list_for_channel(channel_id)
        reference_id = next(
            (item.reference_source_video_id for item in profiles if item.reference_source_video_id),
            None,
        )
        source = self.repos.videos.get(int(reference_id)) if reference_id else None
        source = source or self.repos.videos.latest_local_for_channel(channel_id)
        candidate = None
        if source and source.id is not None:
            candidates = self.repos.candidates.list_for_video(int(source.id))
            candidate = candidates[0] if candidates else None
        self._open(channel_id, source, candidate, "gaming_split")

    def _open(
        self,
        channel_id: int,
        source: SourceVideo | None,
        candidate: ClipCandidate | None,
        initial_mode: str,
    ) -> None:
        self.player.stop()
        self.preview_stack.setCurrentWidget(self.live_preview)
        self.channel_id = channel_id
        self.source = source
        self.candidate = candidate
        self._drafts.clear()
        channel = self.repos.channels.get(channel_id)
        self._selection_instructions = channel.clip_selection_instructions if channel else ""
        self.selection_instructions.blockSignals(True)
        self.selection_instructions.setPlainText(self._selection_instructions)
        self.selection_instructions.blockSignals(False)
        self.selection_character_count.setText(f"{len(self._selection_instructions)} / 4000")
        self.channel_title.setText(f"{channel.name if channel else 'Channel'} framing profile")
        self.source_title.setText(source.title if source else "No downloaded source yet · instructions can still be saved")
        self._current_mode = initial_mode
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(max(0, self.mode.findData(initial_mode)))
        self.mode.blockSignals(False)
        self._prepare_drafts()
        self._load_mode(initial_mode)
        self._configure_source()
        self.state.setText(
            "Adjust the detected regions, verify the portrait preview, then save the profile."
            if self._has_source()
            else "Analyze a video from this channel first to edit regions. Gemini instructions can be saved now."
        )
        self.auto_detect.setEnabled(self._has_source())
        self.test.setEnabled(self._has_source())
        self.save.setEnabled(channel is not None)

    def current_profile(self) -> FramingProfile:
        self._capture_draft(self._current_mode)
        return replace(self._drafts[self._current_mode])

    def finish_auto_detect(self, profile: FramingProfile) -> None:
        self.auto_detect.setEnabled(self._has_source())
        self._drafts[profile.mode] = profile
        if profile.mode == self._current_mode:
            self._load_mode(profile.mode)
        self.state.setText("Gemini proposal applied · adjust it if needed, then save for this channel.")

    def finish_test(self, path: str) -> None:
        self.test.setEnabled(self._has_source())
        self._clear_test_preview()
        self._test_path = Path(path)
        self.player.setSource(QUrl.fromLocalFile(str(self._test_path.resolve())))
        self.preview_stack.setCurrentWidget(self.test_video)
        self.preview_badge.setText("Rendered test")
        self.player.play()
        self.state.setText("Test render ready · the saved profile is unchanged until you click Save for channel.")

    def finish_action_error(self, message: str) -> None:
        self.auto_detect.setEnabled(self._has_source())
        self.test.setEnabled(self._has_source())
        self.state.setText(message)

    def _prepare_drafts(self) -> None:
        assert self.channel_id is not None
        for mode in ("gaming_split", "focus"):
            stored = self.repos.framing_profiles.get(self.channel_id, mode)
            if stored:
                self._drafts[mode] = stored
                continue
            profile = FramingProfile(
                None,
                self.channel_id,
                mode,
                reference_source_video_id=self.source.id if self.source else None,
                reference_seconds=self._candidate_time(),
            )
            if self.candidate:
                for field in (
                    "facecam_x", "facecam_y", "facecam_width", "facecam_height",
                    "gameplay_x", "gameplay_y", "gameplay_width", "gameplay_height",
                    "hud_x", "hud_y", "hud_width", "hud_height",
                ):
                    setattr(profile, field, getattr(self.candidate, field))
                profile.output_regions = dict(self.candidate.output_regions)
            self._drafts[mode] = profile

    def _load_mode(self, mode: str) -> None:
        profile = self._drafts[mode]
        self.instructions.blockSignals(True)
        self.instructions.setPlainText(profile.instructions)
        self.instructions.blockSignals(False)
        self.character_count.setText(f"{len(profile.instructions)} / 4000")
        self.canvas.set_regions(_profile_regions(profile))
        outputs = resolved_output_regions(
            mode,
            profile.output_regions,
            self.canvas.regions,
        )
        self.live_preview.set_output_regions(
            {key: QRectF(*value) for key, value in outputs.items()}
        )
        self._show_live()

    def _mode_changed(self, index: int) -> None:
        mode = str(self.mode.itemData(index)) if index >= 0 else "gaming_split"
        if not mode or self.channel_id is None:
            return
        self._capture_draft(self._current_mode)
        self._current_mode = mode
        self._load_mode(mode)

    def _capture_draft(self, mode: str) -> None:
        if mode not in self._drafts:
            return
        profile = self._drafts[mode]
        profile.instructions = self.instructions.toPlainText().strip()[:4000]
        profile.reference_source_video_id = self.source.id if self.source else profile.reference_source_video_id
        profile.reference_seconds = self.reference_seconds
        _set_profile_regions(profile, self.canvas.regions)
        profile.output_regions = _canvas_regions(self.live_preview.output_regions)

    def _configure_source(self) -> None:
        duration = float(self.source.duration_seconds or 0) if self.source else 0
        if duration <= 0 and self._has_source():
            duration = _video_duration(Path(self.source.local_path))
        reference = self._drafts[self._current_mode].reference_seconds
        self.reference_seconds = min(duration, max(0.0, float(reference if reference is not None else self._candidate_time())))
        self.source_time.setRange(0, max(0, round(duration * 1000)))
        self.source_time.setValue(round(self.reference_seconds * 1000))
        self._load_reference_frame()

    def _load_reference_frame(self) -> None:
        self.reference_seconds = self.source_time.value() / 1000
        self.time_label.setText(format_time(self.reference_seconds))
        self._frame = _read_frame(Path(self.source.local_path), self.reference_seconds) if self._has_source() else None
        self.canvas.set_frame(self._frame)
        self._show_live()

    def _regions_changed(self) -> None:
        self._show_live()

    def _output_regions_changed(self) -> None:
        self._show_live()

    def _active_changed(self, key: str) -> None:
        button = self.region_buttons.get(key)
        if button:
            button.setChecked(True)

    def _remove_active(self) -> None:
        self.canvas.remove_active()
        self.live_preview.remove_active()

    def _select_region(self, key: str) -> None:
        self.canvas.select_region(key)
        self.live_preview.select_region(key)

    def _show_live(self) -> None:
        self.player.stop()
        self.preview_stack.setCurrentWidget(self.live_preview)
        self.preview_badge.setText("Layout preview")
        self.live_preview.update_plan(
            self._frame,
            self.canvas.regions,
            self._current_mode,
            self.live_preview.output_regions,
        )

    def _instructions_changed(self) -> None:
        self._limit_editor(self.instructions, self.character_count)

    def _selection_instructions_changed(self) -> None:
        self._limit_editor(self.selection_instructions, self.selection_character_count)

    @staticmethod
    def _limit_editor(editor: QPlainTextEdit, counter: QLabel) -> None:
        text = editor.toPlainText()
        if len(text) > 4000:
            cursor = editor.textCursor()
            position = min(cursor.position(), 4000)
            editor.blockSignals(True)
            editor.setPlainText(text[:4000])
            cursor = editor.textCursor()
            cursor.setPosition(position)
            editor.setTextCursor(cursor)
            editor.blockSignals(False)
            text = text[:4000]
        counter.setText(f"{len(text)} / 4000")

    def _save(self) -> None:
        try:
            profile = self.repos.framing_profiles.save(self.current_profile())
            self.repos.channels.update(
                int(self.channel_id),
                clip_selection_instructions=self.selection_instructions.toPlainText().strip()[:4000],
            )
        except Exception as exc:
            self.state.setText(str(exc))
            return
        self._drafts[profile.mode] = profile
        self.state.setText("Saved · new analyses and framing regenerations will use this channel profile.")
        self.saved.emit("Framing profile saved")

    def _auto_detect(self) -> None:
        self.auto_detect.setEnabled(False)
        self.state.setText("Gemini is detecting stable source regions…")
        self.auto_detect_requested.emit(self.current_profile())

    def _test(self) -> None:
        self.test.setEnabled(False)
        self.state.setText("Rendering a short local preview…")
        self.test_requested.emit(self.current_profile())

    def _back(self) -> None:
        self._clear_test_preview()
        self.back_requested.emit()

    def _clear_test_preview(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        if self._test_path:
            self._test_path.unlink(missing_ok=True)
            self._test_path.with_suffix(".ass").unlink(missing_ok=True)
            self._test_path = None

    def _has_source(self) -> bool:
        return bool(self.source and self.source.local_path and Path(self.source.local_path).is_file())

    def _candidate_time(self) -> float:
        if not self.candidate:
            return 0.0
        return (self.candidate.render_start + self.candidate.render_end) / 2


def _profile_regions(profile: FramingProfile) -> dict[str, QRectF]:
    result: dict[str, QRectF] = {}
    for key in REGION_SPECS:
        values = tuple(getattr(profile, f"{key}_{suffix}") for suffix in ("x", "y", "width", "height"))
        if all(value is not None for value in values):
            result[key] = QRectF(*(float(value) for value in values))
    return result


def _set_profile_regions(profile: FramingProfile, regions: dict[str, QRectF]) -> None:
    for key in REGION_SPECS:
        region = regions.get(key)
        values = (
            (region.x(), region.y(), region.width(), region.height())
            if region is not None
            else (None, None, None, None)
        )
        for suffix, value in zip(("x", "y", "width", "height"), values, strict=True):
            setattr(profile, f"{key}_{suffix}", round(float(value), 5) if value is not None else None)


def _canvas_regions(regions: dict[str, QRectF]) -> dict[str, tuple[float, float, float, float]]:
    return {
        key: (
            round(region.x(), 5),
            round(region.y(), 5),
            round(region.width(), 5),
            round(region.height(), 5),
        )
        for key, region in regions.items()
    }


def _read_frame(path: Path, seconds: float) -> QPixmap | None:
    try:
        import cv2

        capture = cv2.VideoCapture(str(path))
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000)
        ok, frame = capture.read()
        capture.release()
        if not ok:
            return None
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = frame.shape
        image = QImage(frame.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(image)
    except Exception:
        return None


def _video_duration(path: Path) -> float:
    try:
        import cv2

        capture = cv2.VideoCapture(str(path))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        capture.release()
        return frames / fps if fps > 0 else 0.0
    except Exception:
        return 0.0


