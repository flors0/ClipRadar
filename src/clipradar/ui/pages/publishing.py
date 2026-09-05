from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from clipradar.models import PublishStatus
from clipradar.storage.repositories import PublishRepository
from clipradar.ui.common import card_layout, clear_layout, format_timestamp, muted_label, status_pill, title_label


STATUS_COLORS = {
    PublishStatus.QUEUED.value: "#ffcf5a",
    PublishStatus.UPLOADING.value: "#caff00",
    PublishStatus.PRIVATE.value: "#9cb7ff",
    PublishStatus.SCHEDULED.value: "#caff00",
    PublishStatus.PUBLISHED.value: "#caff00",
    PublishStatus.FAILED.value: "#ff7070",
    PublishStatus.CANCELLED.value: "#8e9993",
}


class PublishCard(QFrame):
    retry_requested = Signal(str)
    cancel_requested = Signal(str)

    def __init__(self, job: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.job = job
        self.setObjectName("ReviewCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 17, 20, 17)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel(job["title"])
        title.setObjectName("SectionTitle")
        title.setWordWrap(True)
        top.addWidget(title, 1)
        top.addWidget(status_pill(job["status"], STATUS_COLORS.get(job["status"], "#8e9993")))
        layout.addLayout(top)
        layout.addWidget(muted_label(
            f"{job['account_name']}  ·  Source: {job['source_channel']} / {job['source_title']}",
            wrap=True,
        ))

        if job["scheduled_for"]:
            layout.addWidget(muted_label(f"Public release {format_timestamp(job['scheduled_for'])}"))
        else:
            layout.addWidget(muted_label(
                f"{str(job['privacy_status']).title()} upload  ·  Queued {format_timestamp(job['created_at'])}"
            ))

        if job["status"] == PublishStatus.UPLOADING.value:
            progress = QProgressBar()
            progress.setRange(0, 1000)
            progress.setValue(round(float(job["progress"] or 0) * 1000))
            layout.addWidget(progress)
        if job["error"]:
            error = QLabel(str(job["error"]))
            error.setObjectName("Error")
            error.setWordWrap(True)
            layout.addWidget(error)

        actions = QHBoxLayout()
        actions.addWidget(muted_label(f"Attempt {job['attempts']}" if job["attempts"] else "Not started"))
        actions.addStretch(1)
        open_file = QPushButton("Open file")
        open_file.clicked.connect(self._open_file)
        actions.addWidget(open_file)
        if job["remote_video_id"]:
            open_youtube = QPushButton("Open YouTube")
            open_youtube.setObjectName("PrimaryButton")
            open_youtube.clicked.connect(self._open_youtube)
            actions.addWidget(open_youtube)
        elif job["status"] == PublishStatus.FAILED.value:
            cancel = QPushButton("Cancel")
            cancel.setObjectName("DangerButton")
            cancel.clicked.connect(lambda: self.cancel_requested.emit(str(job["id"])))
            retry = QPushButton("Retry")
            retry.setObjectName("PrimaryButton")
            retry.clicked.connect(lambda: self.retry_requested.emit(str(job["id"])))
            actions.addWidget(cancel)
            actions.addWidget(retry)
        elif job["status"] == PublishStatus.QUEUED.value:
            cancel = QPushButton("Cancel")
            cancel.setObjectName("DangerButton")
            cancel.clicked.connect(lambda: self.cancel_requested.emit(str(job["id"])))
            actions.addWidget(cancel)
        layout.addLayout(actions)

    def _open_file(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.job["file_path"]).resolve().parent)))

    def _open_youtube(self) -> None:
        QDesktopServices.openUrl(QUrl(f"https://youtu.be/{self.job['remote_video_id']}"))


class PublishingPage(QWidget):
    retry_requested = Signal(str)
    cancel_requested = Signal(str)

    def __init__(self, repository: PublishRepository, parent: QWidget | None = None):
        super().__init__(parent)
        self.repository = repository
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        header, header_layout = card_layout(object_name="Toolbar")
        row = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(title_label("Publish queue"))
        heading.addWidget(muted_label(
            "Approved uploads continue in the background. Scheduled clips are uploaded privately, then released by YouTube.",
            wrap=True,
        ))
        row.addLayout(heading, 1)
        self.count = status_pill("0 jobs", "#8e9993")
        row.addWidget(self.count)
        header_layout.addLayout(row)
        root.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.cards = QVBoxLayout(self.container)
        self.cards.setContentsMargins(0, 0, 4, 0)
        self.cards.setSpacing(12)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)

    def refresh(self) -> None:
        records = self.repository.list_all()
        self.count.setText(f"  ●  {len(records)} job{'s' if len(records) != 1 else ''}  ")
        clear_layout(self.cards)
        if not records:
            empty, layout = card_layout()
            empty.setMinimumHeight(250)
            layout.addStretch(1)
            heading = title_label("No publishing jobs yet")
            heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(heading)
            description = muted_label(
                "Connect YouTube under Settings → Publishing, then approve an upload from Review.",
                wrap=True,
            )
            description.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(description)
            layout.addStretch(1)
            self.cards.addWidget(empty)
        for record in records:
            card = PublishCard(record)
            card.retry_requested.connect(self.retry_requested)
            card.cancel_requested.connect(self.cancel_requested)
            self.cards.addWidget(card)
        self.cards.addStretch(1)
