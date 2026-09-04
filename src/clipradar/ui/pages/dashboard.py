from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from clipradar.models import JobStatus
from clipradar.storage.repositories import Repositories
from clipradar.ui.common import card_layout, clear_layout, format_timestamp, muted_label, status_pill, title_label


class MetricCard(QWidget):
    def __init__(self, label: str, value: str = "0", detail: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        frame, layout = card_layout(self, object_name="MetricCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        self.label = QLabel(label.upper())
        self.label.setObjectName("Tiny")
        self.value = QLabel(value)
        self.value.setObjectName("CardValue")
        self.detail = muted_label(detail)
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)

    def set_value(self, value: str, detail: str | None = None) -> None:
        self.value.setText(value)
        if detail is not None:
            self.detail.setText(detail)


class DashboardPage(QWidget):
    check_requested = Signal()

    def __init__(self, repositories: Repositories, parent: QWidget | None = None):
        super().__init__(parent)
        self.repos = repositories
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        toolbar, toolbar_layout = card_layout(object_name="Toolbar")
        toolbar_layout.setDirection(QVBoxLayout.Direction.TopToBottom)
        toolbar_row = QHBoxLayout()
        toolbar_row.setSpacing(12)
        heading = QVBoxLayout()
        heading.setSpacing(3)
        heading.addWidget(title_label("Monitoring is active"))
        heading.addWidget(muted_label("ClipRadar checks enabled channels while this app is open."))
        toolbar_row.addLayout(heading)
        toolbar_row.addStretch(1)
        self.monitoring_pill = status_pill("Running")
        toolbar_row.addWidget(self.monitoring_pill)
        self.check_button = QPushButton("Check now")
        self.check_button.clicked.connect(self.check_requested)
        toolbar_row.addWidget(self.check_button)
        toolbar_layout.addLayout(toolbar_row)
        root.addWidget(toolbar)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(14)
        metrics.setVerticalSpacing(14)
        self.metrics = {
            "channels": MetricCard("Channels", detail="monitored sources"),
            "waiting": MetricCard("Waiting videos", detail="scheduled or queued"),
            "analyzing": MetricCard("Analyzing", detail="active pipeline jobs"),
            "ready": MetricCard("Ready clips", detail="waiting for your decision"),
            "cost": MetricCard("AI cost today", detail="estimated, budget-controlled"),
            "minutes": MetricCard("Source minutes", detail="analyzed today"),
        }
        for index, metric in enumerate(self.metrics.values()):
            metrics.addWidget(metric, index // 3, index % 3)
        root.addLayout(metrics)

        activity_card, activity_layout = card_layout()
        header = QHBoxLayout()
        header.addWidget(title_label("Recent activity"))
        header.addStretch(1)
        self.activity_count = muted_label("")
        header.addWidget(self.activity_count)
        activity_layout.addLayout(header)
        self.activity_widget = QWidget()
        self.activity_layout = QVBoxLayout(self.activity_widget)
        self.activity_layout.setContentsMargins(0, 4, 0, 0)
        self.activity_layout.setSpacing(0)
        activity_scroll = QScrollArea()
        activity_scroll.setWidgetResizable(True)
        activity_scroll.setMinimumHeight(230)
        activity_scroll.setWidget(self.activity_widget)
        activity_layout.addWidget(activity_scroll)
        root.addWidget(activity_card, 1)

    def set_monitoring_busy(self, busy: bool) -> None:
        self.check_button.setDisabled(busy)
        self.check_button.setText("Checking…" if busy else "Check now")
        self.monitoring_pill.setText("  ●  Checking  " if busy else "  ●  Running  ")

    def refresh(self) -> None:
        channels = self.repos.channels.list_all()
        counts = self.repos.jobs.counts()
        usage = self.repos.usage.get_today()
        waiting = counts[JobStatus.WAITING.value] + counts[JobStatus.SCHEDULED.value]
        analyzing = counts[JobStatus.DOWNLOADING.value] + counts[JobStatus.ANALYZING.value] + counts[JobStatus.RENDERING.value]
        self.metrics["channels"].set_value(str(len(channels)), f"{sum(item.monitoring_enabled for item in channels)} enabled")
        self.metrics["waiting"].set_value(str(waiting))
        self.metrics["analyzing"].set_value(str(analyzing))
        self.metrics["ready"].set_value(str(self.repos.clips.ready_count()))
        self.metrics["cost"].set_value(f"€{usage['estimated_cost_eur']:.3f}")
        self.metrics["minutes"].set_value(f"{usage['source_minutes']:.1f}")

        activities = self.repos.activity.recent(12)
        self.activity_count.setText(f"{len(activities)} latest events")
        clear_layout(self.activity_layout)
        if not activities:
            empty = muted_label("Activity will appear here once channels are checked or videos are analyzed.", wrap=True)
            empty.setContentsMargins(0, 24, 0, 24)
            self.activity_layout.addWidget(empty)
        for item in activities:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 9, 0, 9)
            marker = QLabel("●")
            marker.setStyleSheet({"error": "color:#ff7070", "success": "color:#caff00", "warning": "color:#ffcf5a"}.get(item["level"], "color:#77827c"))
            message = QLabel(item["message"])
            message.setWordWrap(True)
            when = muted_label(format_timestamp(item["created_at"]))
            line.addWidget(marker)
            line.addWidget(message, 1)
            line.addWidget(when)
            self.activity_layout.addWidget(row)
        self.activity_layout.addStretch(1)

