from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from clipradar.models import JobStatus
from clipradar.storage.repositories import Repositories
from clipradar.ui.common import card_layout, clear_layout, muted_label, status_pill, title_label


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


class MonitoringPage(QWidget):
    check_requested = Signal()
    start_detected_requested = Signal()
    start_job_requested = Signal(str)
    cancel_job_requested = Signal(str)

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
        self.start_detected = QPushButton("Start detected")
        self.start_detected.setObjectName("PrimaryButton")
        self.start_detected.clicked.connect(self.start_detected_requested)
        toolbar_row.addWidget(self.start_detected)
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
            metrics.addWidget(metric, 0, index)
        root.addLayout(metrics)

        tasks_card, tasks_layout = card_layout()
        task_header = QHBoxLayout()
        task_header.addWidget(title_label("Analysis tasks"))
        task_header.addStretch(1)
        self.current_task = muted_label("No active analysis")
        task_header.addWidget(self.current_task)
        tasks_layout.addLayout(task_header)
        self.task_scroll = QScrollArea()
        self.task_scroll.setWidgetResizable(True)
        self.task_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.task_scroll.setMaximumHeight(142)
        self.task_container = QWidget()
        self.task_layout = QVBoxLayout(self.task_container)
        self.task_layout.setContentsMargins(0, 0, 0, 0)
        self.task_layout.setSpacing(6)
        self.task_scroll.setWidget(self.task_container)
        tasks_layout.addWidget(self.task_scroll)
        root.addWidget(tasks_card)

        activity_card, activity_layout = card_layout()
        header = QHBoxLayout()
        header.addWidget(title_label("Activity log"))
        header.addStretch(1)
        self.activity_count = muted_label("")
        header.addWidget(self.activity_count)
        self.copy_logs = QPushButton("Copy logs")
        self.copy_logs.setFixedWidth(102)
        self.copy_logs.clicked.connect(self._copy_logs)
        header.addWidget(self.copy_logs)
        activity_layout.addLayout(header)
        self.activity_log = QPlainTextEdit()
        self.activity_log.setObjectName("ActivityLog")
        self.activity_log.setReadOnly(True)
        self.activity_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.activity_log.setMinimumHeight(180)
        self.activity_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.activity_log.setPlaceholderText(
            "Activity will appear here once channels are checked or videos are analyzed."
        )
        activity_layout.addWidget(self.activity_log)
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

        pending = self.repos.jobs.pending_approval_count()
        self.start_detected.setText(f"Start detected ({pending})")
        self.start_detected.setEnabled(pending > 0)
        self._refresh_tasks(self.repos.jobs.recent(12))

        activities = self.repos.activity.recent(200)
        runs = len({item["job_id"] for item in activities if item["job_id"]})
        count_text = f"{len(activities)} event{'s' if len(activities) != 1 else ''}"
        if runs:
            count_text += f" · {runs} run{'s' if runs != 1 else ''}"
        self.activity_count.setText(count_text if activities else "No events yet")
        self.activity_log.setPlainText(_format_activity_log(activities))
        cursor = self.activity_log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.activity_log.setTextCursor(cursor)

    def _refresh_tasks(self, jobs: list[dict]) -> None:
        clear_layout(self.task_layout)
        active_statuses = {
            JobStatus.DOWNLOADING.value,
            JobStatus.ANALYZING.value,
            JobStatus.RENDERING.value,
        }
        active = next((job for job in jobs if job["status"] in active_statuses), None)
        if active:
            self.current_task.setText(
                f"Active · {active['video_title']} · {round(float(active['progress']) * 100)}%"
            )
        else:
            self.current_task.setText("No active analysis")
        if not jobs:
            self.task_layout.addWidget(muted_label("No analysis tasks yet."))
            return
        for job in jobs:
            row = QWidget()
            row.setObjectName("TaskRowActive" if active and job["id"] == active["id"] else "TaskRow")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(9, 5, 9, 5)
            layout.setSpacing(9)
            title = QLabel(str(job["video_title"]))
            title.setToolTip(str(job["video_title"]))
            title.setMinimumWidth(190)
            layout.addWidget(title, 3)
            stage = muted_label(str(job["stage"]))
            stage.setMinimumWidth(110)
            layout.addWidget(stage, 2)
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(round(float(job["progress"]) * 100))
            progress.setTextVisible(False)
            progress.setFixedHeight(7)
            layout.addWidget(progress, 2)
            percent = muted_label(f"{round(float(job['progress']) * 100)}%")
            percent.setFixedWidth(36)
            layout.addWidget(percent)
            button = QPushButton()
            button.setFixedWidth(66)
            approved = bool(job.get("approved"))
            status = str(job["status"])
            can_start = not approved and status in {
                JobStatus.WAITING.value,
                JobStatus.SCHEDULED.value,
            }
            can_stop = status in {
                JobStatus.WAITING.value,
                JobStatus.SCHEDULED.value,
                *active_statuses,
            } and not bool(job.get("cancel_requested"))
            if can_start:
                button.setText("Start")
                button.clicked.connect(
                    lambda _checked=False, job_id=str(job["id"]): self.start_job_requested.emit(job_id)
                )
            elif can_stop:
                button.setText("Stop")
                button.setObjectName("DangerButton")
                button.clicked.connect(
                    lambda _checked=False, job_id=str(job["id"]): self.cancel_job_requested.emit(job_id)
                )
            elif bool(job.get("cancel_requested")) and status in active_statuses:
                button.setText("Stopping")
                button.setEnabled(False)
            else:
                button.setText(status)
                button.setEnabled(False)
            layout.addWidget(button)
            self.task_layout.addWidget(row)

    def _copy_logs(self) -> None:
        text = self.activity_log.toPlainText()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.copy_logs.setText("Copied")
        QTimer.singleShot(1500, lambda: self.copy_logs.setText("Copy logs"))


def _format_activity_log(activities: list[dict]) -> str:
    groups: dict[str, list[dict]] = {}
    for item in activities:
        key = str(item["job_id"] or "application")
        groups.setdefault(key, []).append(item)
    sections: list[str] = []
    for key, items in groups.items():
        newest = items[0]
        kind = str(newest.get("operation_type") or "Application").upper()
        title = str(newest.get("operation_title") or "").strip()
        header = f"{kind} · {title}" if title else kind
        metadata: list[str] = []
        channel = str(newest.get("channel_name") or "").strip()
        if channel:
            metadata.append(channel)
        if newest.get("job_id"):
            attempt = int(newest.get("attempt") or 0)
            if attempt:
                metadata.append(f"Attempt {attempt}")
            else:
                metadata.append("Not started")
            metadata.append(f"Job {str(newest['job_id'])[:8]}")
        status = str(newest.get("operation_status") or "").strip()
        if status:
            metadata.append(f"Status {status}")
        lines = ["─" * 88, header]
        if metadata:
            lines.append(" · ".join(metadata))
        lines.append("─" * 88)
        for item in reversed(items):
            level = str(item.get("level") or "info").upper().ljust(7)
            lines.append(f"{_log_timestamp(item.get('created_at'))}  {level}  {item.get('message', '')}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _log_timestamp(value: str | None) -> str:
    if not value:
        return "-- --- ---- --:--:--"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%d %b %Y %H:%M:%S")
    except ValueError:
        return value[:20].ljust(20)
