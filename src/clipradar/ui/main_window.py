from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QProgressBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from clipradar.app.coordinator import BackgroundCoordinator
from clipradar.app.paths import resource_path
from clipradar.app.services import AppServices
from clipradar.ui.common import muted_label
from clipradar.ui.pages.channels import ChannelsPage
from clipradar.ui.pages.dashboard import DashboardPage
from clipradar.ui.pages.publishing import PublishingPage
from clipradar.ui.pages.review import ReviewPage
from clipradar.ui.pages.settings import SettingsPage


PAGE_INFO = [
    ("Dashboard", "A quiet overview of monitoring, processing, and review."),
    ("Channels", "Choose sources and trigger focused analysis."),
    ("Review", "Watch each finished clip and make one clear decision."),
    ("Publishing", "Track uploads and scheduled YouTube releases."),
    ("Settings", "Control defaults, AI access, publishing, budget, and storage."),
]


class MainWindow(QMainWindow):
    def __init__(self, services: AppServices, *, start_background: bool = True):
        super().__init__()
        self.services = services
        self.coordinator = BackgroundCoordinator(services, self)
        self.setWindowTitle("ClipRadar")
        self.setWindowIcon(QIcon(str(resource_path("resources/logo.svg"))))
        self.resize(1440, 900)
        self.setMinimumSize(1100, 720)
        self._build_ui()
        self._connect()
        self.refresh_all()
        if start_background:
            self.coordinator.start()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(214)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 24, 20, 20)
        side.setSpacing(8)
        brand_row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QIcon(str(resource_path("resources/logo.svg"))).pixmap(36, 36))
        brand = QLabel("ClipRadar")
        brand.setObjectName("Brand")
        brand_row.addWidget(logo)
        brand_row.addSpacing(4)
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        side.addLayout(brand_row)
        tagline = muted_label("Find the moments worth keeping.", wrap=True)
        side.addWidget(tagline)
        side.addSpacing(24)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        icons = ["⌂", "◉", "▶", "↑", "⚙"]
        for index, ((title, _), icon) in enumerate(zip(PAGE_INFO, icons, strict=True)):
            button = QPushButton(f"{icon}    {title}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, page=index: self._set_page(page))
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            side.addWidget(button)
        side.addStretch(1)
        self.sidebar_status = muted_label("●  Ready")
        side.addWidget(self.sidebar_status)
        version = QLabel("v0.3.0")
        version.setObjectName("Tiny")
        side.addWidget(version)
        shell.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(30, 24, 30, 28)
        content_layout.setSpacing(18)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(3)
        self.page_title = QLabel()
        self.page_title.setObjectName("PageTitle")
        self.page_subtitle = muted_label("")
        heading.addWidget(self.page_title)
        heading.addWidget(self.page_subtitle)
        header.addLayout(heading)
        header.addStretch(1)
        self.pipeline_text = muted_label("No active job")
        header.addWidget(self.pipeline_text)
        content_layout.addLayout(header)
        self.pipeline_progress = QProgressBar()
        self.pipeline_progress.setRange(0, 1000)
        self.pipeline_progress.setValue(0)
        self.pipeline_progress.hide()
        content_layout.addWidget(self.pipeline_progress)

        self.pages = QStackedWidget()
        self.dashboard = DashboardPage(self.services.repositories)
        self.channels = ChannelsPage(self.services.repositories.channels)
        self.review = ReviewPage(
            self.services.repositories.clips,
            self.services.repositories.channels,
            self.services.settings,
            self.services.publishing,
        )
        self.publishing = PublishingPage(self.services.repositories.publish)
        self.settings = SettingsPage(self.services.settings, self.services.paths, self.services.publishing)
        for page in (self.dashboard, self.channels, self.review, self.publishing, self.settings):
            self.pages.addWidget(page)
        content_layout.addWidget(self.pages, 1)

        self.toast = QFrame(content)
        self.toast.setObjectName("Toast")
        toast_layout = QHBoxLayout(self.toast)
        toast_layout.setContentsMargins(14, 9, 14, 9)
        self.toast_label = QLabel()
        toast_layout.addWidget(self.toast_label)
        self.toast.hide()
        content_layout.addWidget(self.toast)
        shell.addWidget(content, 1)
        self.nav_buttons[0].setChecked(True)
        self._set_page(0)

    def _connect(self) -> None:
        self.dashboard.check_requested.connect(self._check_channels)
        self.channels.add_requested.connect(self._add_channel)
        self.channels.toggle_requested.connect(
            lambda channel_id, enabled: self._background(
                f"toggle:{channel_id}", lambda: self.services.channels.set_monitoring(channel_id, enabled)
            )
        )
        self.channels.latest_requested.connect(
            lambda channel_id: self._background(
                f"latest:{channel_id}", lambda: self.services.channels.analyze_latest(channel_id)
            )
        )
        self.channels.specific_requested.connect(
            lambda channel_id, url: self._background(
                f"specific:{channel_id}", lambda: self.services.channels.analyze_specific(channel_id, url)
            )
        )
        self.channels.remove_requested.connect(
            lambda channel_id: self._background(
                f"remove:{channel_id}", lambda: self.services.channels.remove_channel(channel_id)
            )
        )
        self.channels.update_requested.connect(self._update_channel)
        self.review.approve_requested.connect(self._approve)
        self.review.reject_requested.connect(self._reject)
        self.review.delete_requested.connect(self._delete_clip)
        self.review.regenerate_requested.connect(self._regenerate)
        self.review.publish_requested.connect(self._publish)
        self.publishing.retry_requested.connect(self._retry_publish)
        self.publishing.cancel_requested.connect(self._cancel_publish)
        self.settings.saved.connect(self._toast)
        self.settings.failed.connect(lambda message: self._toast(message, error=True))
        self.settings.test_ai_requested.connect(self._test_ai)
        self.settings.youtube_client_import_requested.connect(
            lambda path: self._background(
                "youtube_import", lambda: self.services.publishing.import_client_file(path)
            )
        )
        self.settings.youtube_connect_requested.connect(
            lambda: self._background("youtube_connect", self.services.publishing.connect_account)
        )
        self.settings.youtube_verify_requested.connect(
            lambda account_id: self._background(
                "youtube_verify", lambda: self.services.publishing.verify_account(account_id)
            )
        )
        self.settings.youtube_disconnect_requested.connect(self._disconnect_youtube)
        self.coordinator.task_succeeded.connect(self._task_succeeded)
        self.coordinator.task_failed.connect(self._task_failed)
        self.coordinator.data_changed.connect(self.refresh_all)
        self.coordinator.job_progress.connect(self._job_progress)

    def refresh_all(self) -> None:
        self.dashboard.refresh()
        self.channels.refresh()
        self.review.refresh()
        self.publishing.refresh()

    def _set_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        title, subtitle = PAGE_INFO[index]
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)
        self.nav_buttons[index].setChecked(True)
        if index == 2:
            self.review.refresh()
        elif index == 3:
            self.publishing.refresh()
        elif index == 4:
            self.settings.refresh_publishing()

    def _background(self, key: str, operation: Callable[[], object]) -> None:
        if not self.coordinator.execute(key, operation):
            self._toast("That task is already running.", error=True)

    def _check_channels(self) -> None:
        self.dashboard.set_monitoring_busy(True)
        self.coordinator.check_channels()

    def _add_channel(self, identifier: str) -> None:
        self._background("add_channel", lambda: self.services.channels.add_channel(identifier))

    def _update_channel(self, channel_id: int, changes: dict) -> None:
        try:
            self.services.repositories.channels.update(channel_id, **changes)
            self.refresh_all()
            self._toast("Channel settings saved")
        except Exception as exc:
            self._toast(str(exc), error=True)

    def _approve(self, clip_id: int) -> None:
        try:
            self.services.review.approve(clip_id)
            self.refresh_all()
            self._toast("Clip approved")
        except Exception as exc:
            self._toast(str(exc), error=True)

    def _reject(self, clip_id: int) -> None:
        try:
            self.services.review.reject(clip_id)
            self.refresh_all()
            self._toast("Clip rejected")
        except Exception as exc:
            self._toast(str(exc), error=True)

    def _delete_clip(self, clip_id: int) -> None:
        try:
            self.services.review.delete_permanently(clip_id)
            self.refresh_all()
            self._toast("Clip permanently deleted")
        except Exception as exc:
            self._toast(str(exc), error=True)

    def _regenerate(self, clip_id: int, reframe_mode: str) -> None:
        if self.coordinator.regenerate(clip_id, reframe_mode):
            self.refresh_all()
            self._toast("Gemini is re-analyzing the framing…")

    def _publish(self, clip_id: int, metadata: dict) -> None:
        try:
            self.services.publishing.queue_clip(clip_id=clip_id, **metadata)
        except Exception as exc:
            self._toast(str(exc), error=True)
            return
        self.refresh_all()
        self._set_page(3)
        self._toast("Upload queued" if not metadata.get("scheduled_for") else "Scheduled upload queued")

    def _retry_publish(self, job_id: str) -> None:
        try:
            self.services.publishing.retry(job_id)
        except Exception as exc:
            self._toast(str(exc), error=True)
        else:
            self.refresh_all()
            self._toast("Upload queued again")

    def _cancel_publish(self, job_id: str) -> None:
        try:
            self.services.publishing.cancel(job_id)
        except Exception as exc:
            self._toast(str(exc), error=True)
        else:
            self.refresh_all()
            self._toast("Publishing job cancelled")

    def _disconnect_youtube(self, account_id: int) -> None:
        try:
            self.services.publishing.disconnect_account(account_id)
        except Exception as exc:
            self.settings.finish_youtube_action(str(exc), False)
        else:
            self.settings.finish_youtube_action("YouTube channel disconnected", True)
            self.refresh_all()

    def _test_ai(self, key: str, model: str) -> None:
        self._background("ai_test", lambda: self.services.gemini.test_connection(key, model))

    def _task_succeeded(self, key: str, result: object) -> None:
        if key == "monitoring":
            self.dashboard.set_monitoring_busy(False)
            discovered = getattr(result, "videos_discovered", 0)
            self._toast(f"Channel check complete · {discovered} new video{'s' if discovered != 1 else ''}")
        elif key == "add_channel":
            self.channels.finish_add(True)
            self._toast(f"Added {getattr(result, 'name', 'channel')}")
        elif key == "ai_test":
            self.settings.set_connection_status(str(result), True)
        elif key in {"youtube_import", "youtube_verify"}:
            self.settings.finish_youtube_action(str(result), True)
            self._toast(str(result))
        elif key == "youtube_connect":
            name = getattr(result, "channel_name", "YouTube channel")
            self.settings.finish_youtube_action(f"Connected · {name}", True)
            self._toast(f"Connected {name}")
        elif key == "pipeline":
            self.pipeline_progress.hide()
            self.pipeline_text.setText("No active job")
            self.sidebar_status.setText("●  Ready")
            self._toast(f"Analysis complete · {result} clip{'s' if result != 1 else ''} ready")
        elif key.startswith("regenerate:"):
            self._toast("New render ready for review")
        elif key.startswith(("latest:", "specific:")):
            self._toast("Analysis queued")
        elif key == "publishing":
            self.pipeline_progress.hide()
            self.pipeline_text.setText("No active job")
            self.sidebar_status.setText("●  Ready")
            self._toast("YouTube upload complete")
        else:
            self._toast("Saved")
        self.refresh_all()

    def _task_failed(self, key: str, message: str) -> None:
        if key == "monitoring":
            self.dashboard.set_monitoring_busy(False)
        elif key == "add_channel":
            self.channels.finish_add(False)
        elif key == "ai_test":
            self.settings.set_connection_status(message, False)
            return
        elif key in {"youtube_import", "youtube_connect", "youtube_verify"}:
            self.settings.finish_youtube_action(message, False)
            self._toast(message, error=True)
            return
        elif key == "pipeline":
            self.pipeline_progress.hide()
            self.pipeline_text.setText("Last job failed")
            self.sidebar_status.setText("●  Attention needed")
        elif key == "publishing":
            self.pipeline_progress.hide()
            self.pipeline_text.setText("Last upload failed")
            self.sidebar_status.setText("●  Attention needed")
        self._toast(message, error=True)
        self.refresh_all()

    def _job_progress(self, stage: str, value: float) -> None:
        self.pipeline_progress.show()
        self.pipeline_progress.setValue(round(value * 1000))
        self.pipeline_text.setText(stage)
        self.sidebar_status.setText("●  Processing")

    def _toast(self, message: str, error: bool = False) -> None:
        self.toast_label.setText(message)
        self.toast_label.setStyleSheet("color:#ff8080" if error else "color:#f2f5f3")
        self.toast.show()
        QTimer.singleShot(5000, self.toast.hide)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.coordinator.stop()
        super().closeEvent(event)
