from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLineEdit

from clipradar.app.coordinator import BackgroundCoordinator
from clipradar.models import Channel, ClipCandidate, JobStatus, RenderedClip, SourceVideo, utc_now
from clipradar.ui.main_window import MainWindow
from clipradar.ui.pages.channels import ChannelCard


def test_main_window_opens_and_navigates(qtbot, services):
    window = MainWindow(services, start_background=False)
    qtbot.addWidget(window)
    window.show()
    assert window.page_title.text() == "Dashboard"
    window.nav_buttons[1].click()
    assert window.page_title.text() == "Channels"
    window.nav_buttons[2].click()
    assert window.page_title.text() == "Review"
    window.nav_buttons[3].click()
    assert window.page_title.text() == "Publishing"
    window.nav_buttons[4].click()
    assert window.page_title.text() == "Settings"
    assert window.settings.model.currentData()
    window.settings.nav.setCurrentRow(5)
    assert window.settings.description_style.currentText() in {"Auto", "Short", "Detailed"}
    assert "tags" in window.settings.default_tags.placeholderText().lower()


def test_specific_video_dialog_emits_selected_genre(qtbot, monkeypatch):
    channel = Channel(None, "UC_DIALOG", "Dialog", "", "https://youtube.test/dialog")
    channel.id = 17
    card = ChannelCard(channel, "24")
    qtbot.addWidget(card)
    emitted = []
    card.specific_requested.connect(lambda *values: emitted.append(values))

    def accept(dialog):
        dialog.findChild(QLineEdit).setText("https://youtube.com/watch?v=genre-test")
        genre = dialog.findChild(QComboBox)
        assert genre.currentData() == "24"
        genre.setCurrentIndex(genre.findData("20"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    card._specific()

    assert emitted == [(17, "https://youtube.com/watch?v=genre-test", "20")]


def test_dashboard_activity_log_is_selectable_and_copyable(qtbot, services):
    channel = services.repositories.channels.add(Channel(
        None, "UC_UI_LOG", "Log channel", "", "https://youtube.test/ui-log"
    ))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None, int(channel.id), "ui-log-video", "UI log source", "https://youtube.test/watch?v=ui-log"
    ))
    job = services.repositories.jobs.create(int(source.id), utc_now(), manual=True)
    services.repositories.activity.add("Detailed copyable activity event", "warning", job.id)
    window = MainWindow(services, start_background=False)
    qtbot.addWidget(window)
    window.show()
    window.dashboard.refresh()

    assert window.dashboard.activity_log.isReadOnly()
    assert "Detailed copyable activity event" in window.dashboard.activity_log.toPlainText()
    assert "ANALYSIS · UI log source" in window.dashboard.activity_log.toPlainText()
    assert "WARNING" in window.dashboard.activity_log.toPlainText()
    window.dashboard.copy_logs.click()
    assert "Detailed copyable activity event" in QApplication.clipboard().text()


def test_review_missing_file_keeps_reject_and_permanent_delete_available(qtbot, services, tmp_path: Path):
    channel = services.repositories.channels.add(Channel(
        None, "UC_UI_MISSING", "Missing file source", "", "https://youtube.test/ui-missing"
    ))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None,
        int(channel.id),
        "ui-missing-video",
        "Missing file review",
        "https://youtube.test/watch?v=ui-missing",
    ))
    candidate = services.repositories.candidates.replace_for_video(int(source.id), [
        ClipCandidate(None, int(source.id), 5, 30, 88, {}, ai_score=90, ai_reason="Clear payoff")
    ])[0]
    clip = services.repositories.clips.add(RenderedClip(
        None,
        int(candidate.id),
        int(source.id),
        str(tmp_path / "already-deleted.mp4"),
        25,
        "Vertical 9:16",
    ))
    window = MainWindow(services, start_background=False)
    qtbot.addWidget(window)
    window.review.refresh()

    assert window.review.current["id"] == int(clip.id)
    assert window.review.reject.isEnabled()
    assert window.review.delete_clip.isEnabled()
    assert not window.review.approve.isEnabled()
    assert "File missing" in window.review.status.text()


def test_review_queue_filters_by_one_persisted_channel(qtbot, services, tmp_path: Path):
    channels = []
    for index, name in enumerate(("Alpha Channel", "Beta Channel"), start=1):
        channel = services.repositories.channels.add(Channel(
            None,
            f"UC_FILTER_{index}",
            name,
            "",
            f"https://youtube.test/filter-{index}",
        ))
        source, _ = services.repositories.videos.upsert(SourceVideo(
            None,
            int(channel.id),
            f"filter-video-{index}",
            f"{name} source",
            f"https://youtube.test/watch?v=filter-{index}",
        ))
        candidate = services.repositories.candidates.replace_for_video(int(source.id), [
            ClipCandidate(None, int(source.id), 5, 25, 88, {}, ai_score=90)
        ])[0]
        clip_path = tmp_path / f"filter-{index}.mp4"
        clip_path.write_bytes(b"review-filter-media")
        services.repositories.clips.add(RenderedClip(
            None,
            int(candidate.id),
            int(source.id),
            str(clip_path),
            20,
            "Vertical 9:16",
        ))
        channels.append(channel)

    window = MainWindow(services, start_background=False)
    qtbot.addWidget(window)
    window.review.refresh()

    assert window.review.channel_filter.count() == 2
    assert {record["channel_id"] for record in window.review.records} == {int(channels[0].id)}
    beta_index = window.review.channel_filter.findData(int(channels[1].id))
    window.review.channel_filter.setCurrentIndex(beta_index)
    assert {record["channel_id"] for record in window.review.records} == {int(channels[1].id)}
    assert services.settings.ui_state().review_channel_id == int(channels[1].id)


def test_background_coordinator_never_runs_two_analysis_jobs_at_once(qtbot, services):
    channel = services.repositories.channels.add(Channel(
        None, "UC_SERIAL", "Serial Channel", "", "https://youtube.test/serial"
    ))
    job_ids = []
    for index in range(2):
        source, _ = services.repositories.videos.upsert(SourceVideo(
            None,
            int(channel.id),
            f"serial-video-{index}",
            f"Serial source {index}",
            f"https://youtube.test/watch?v=serial-{index}",
        ))
        job_ids.append(services.repositories.jobs.create(int(source.id), utc_now(), manual=True).id)

    release_first = threading.Event()
    calls = []

    def fake_run(job_id, _progress):
        calls.append(job_id)
        if len(calls) == 1:
            assert release_first.wait(timeout=5)
        services.repositories.jobs.update(job_id, JobStatus.READY, "Test complete", 1.0)
        return 0

    services.pipeline.run = fake_run
    coordinator = BackgroundCoordinator(services)
    coordinator._last_monitor_check = time.monotonic()
    try:
        coordinator._tick()
        qtbot.waitUntil(lambda: len(calls) == 1, timeout=3000)
        coordinator._tick()
        assert calls == [job_ids[0]]
        release_first.set()
        qtbot.waitUntil(lambda: "pipeline" not in coordinator.running, timeout=3000)
        coordinator._tick()
        qtbot.waitUntil(lambda: len(calls) == 2, timeout=3000)
        assert calls == job_ids
        qtbot.waitUntil(lambda: "pipeline" not in coordinator.running, timeout=3000)
    finally:
        release_first.set()
        coordinator.stop()
