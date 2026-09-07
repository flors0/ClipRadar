from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from clipradar.app.services import AppServices
from clipradar.models import OperationCancelled


logger = logging.getLogger(__name__)


class BackgroundCoordinator(QObject):
    task_succeeded = Signal(str, object)
    task_failed = Signal(str, str)
    data_changed = Signal()
    job_progress = Signal(str, str, float)

    def __init__(self, services: AppServices, parent: QObject | None = None):
        super().__init__(parent)
        self.services = services
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="clipradar")
        self.running: set[str] = set()
        self._last_monitor_check = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self._tick)

    def start(self) -> None:
        self.timer.start()
        if self.services.settings.general().start_monitoring_on_launch:
            QTimer.singleShot(2500, self.check_channels)

    def stop(self) -> None:
        self.timer.stop()
        self.executor.shutdown(wait=False, cancel_futures=False)

    def execute(self, key: str, operation: Callable[[], Any]) -> bool:
        if key in self.running:
            return False
        self.running.add(key)
        future = self.executor.submit(operation)
        future.add_done_callback(lambda item: self._finish(key, item))
        return True

    def check_channels(self) -> None:
        if self.execute("monitoring", self.services.monitoring.check_now):
            self._last_monitor_check = time.monotonic()

    def regenerate(self, clip_id: int, reframe_mode: str | None = None) -> bool:
        try:
            candidate_id, resolved_mode = self.services.review.prepare_regeneration(clip_id, reframe_mode)
        except Exception as exc:
            self.task_failed.emit(f"regenerate:{clip_id}", str(exc))
            return False
        return self.execute(
            f"regenerate:{clip_id}",
            lambda: self.services.review.finish_regeneration(
                clip_id,
                candidate_id,
                resolved_mode,
            ),
        )

    def _tick(self) -> None:
        interval = max(1, self.services.settings.monitoring().interval_minutes) * 60
        if time.monotonic() - self._last_monitor_check >= interval:
            self.check_channels()
        if "pipeline" not in self.running:
            job = self.services.repositories.jobs.next_due()
            if job:
                self.execute(
                    "pipeline",
                    lambda job=job: self.services.pipeline.run(
                        job.id,
                        lambda stage, value: self._emit_progress(job.id, stage, value),
                    ),
                )
        if "publishing" not in self.running:
            publish_job = self.services.repositories.publish.next_queued()
            if publish_job:
                self.execute(
                    "publishing",
                    lambda publish_job=publish_job: self.services.publishing.upload(
                        publish_job.id,
                        lambda stage, value: self._emit_progress(
                            f"publish:{publish_job.id}", stage, value
                        ),
                    ),
                )

    def _emit_progress(self, job_id: str, stage: str, value: float) -> None:
        self.job_progress.emit(job_id, stage, value)

    def _finish(self, key: str, future: Future[Any]) -> None:
        self.running.discard(key)
        try:
            result = future.result()
        except OperationCancelled:
            self.task_succeeded.emit(key, None)
        except Exception as exc:
            logger.warning("Background task %s failed: %s", key, exc)
            self.task_failed.emit(key, str(exc))
        else:
            self.task_succeeded.emit(key, result)
        self.data_changed.emit()
