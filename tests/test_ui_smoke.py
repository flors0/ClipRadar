from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLineEdit

from clipradar.app.coordinator import BackgroundCoordinator
from clipradar.models import Channel, ClipCandidate, JobStatus, RenderedClip, SourceVideo, utc_now
from clipradar.ui.main_window import MainWindow
from clipradar.ui.pages.channels import ChannelCard
from clipradar.ui.pages.video_dashboard import VideoCard, _relative_upload_time
from clipradar.ui.timeline import ClickableSlider, TrimRangeSlider
from clipradar.youtube.client import RemoteVideo


def test_main_window_opens_and_navigates(qtbot, services):
    window = MainWindow(services, start_background=False)
    qtbot.addWidget(window)
    window.show()
    assert window.page_title.text() == "Dashboard"
    window.nav_buttons[1].click()
    assert window.page_title.text() == "Monitoring"
    window.nav_buttons[2].click()
    assert window.page_title.text() == "Channels"
    window.nav_buttons[3].click()
    assert window.page_title.text() == "Review"
    window.nav_buttons[4].click()
    assert window.page_title.text() == "Publishing"
    window.nav_buttons[5].click()
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


def test_existing_seek_slider_is_clickable(qtbot):
    slider = ClickableSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 1000)
    slider.resize(400, 30)
    qtbot.addWidget(slider)
    slider.show()

    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(300, 15))

    assert 700 <= slider.value() <= 800


def test_trim_range_has_two_independent_handles(qtbot):
    timeline = TrimRangeSlider()
    timeline.resize(500, 34)
    timeline.set_range(0, 100_000)
    timeline.set_selection(30_000, 70_000)
    qtbot.addWidget(timeline)
    timeline.show()
    changed = []
    timeline.range_changed.connect(lambda start, end: changed.append((start, end)))

    QTest.mouseClick(
        timeline,
        Qt.MouseButton.LeftButton,
        pos=QPoint(round(timeline._x_for_value(20_000)), 17),
    )

    start, end = timeline.selection()
    assert 18_000 <= start <= 22_000
    assert end == 70_000
    assert changed[-1] == (start, end)


def test_dashboard_renders_metadata_cards_and_analyze_uses_genre_dialog(qtbot, services, monkeypatch):
    channel = services.repositories.channels.add(Channel(
        None, "UC_FEED", "Feed Channel", "", "https://youtube.test/feed"
    ))
    window = MainWindow(services, start_background=False)
    qtbot.addWidget(window)
    video = RemoteVideo(
        "feed-video",
        "A recent channel upload",
        "https://youtube.com/watch?v=feed-video",
        channel.channel_id,
        published_at="2026-09-06T12:00:00+00:00",
    )
    window.dashboard.set_videos(int(channel.id), [video])
    cards = window.dashboard.findChildren(VideoCard)
    assert len(cards) == 1
    assert cards[0].title.text() == video.title
    assert cards[0].maximumWidth() == 370
    assert window.dashboard.scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    emitted = []
    window.dashboard.analyze_requested.connect(lambda *values: emitted.append(values))

    def accept(dialog):
        genre = dialog.findChild(QComboBox)
        genre.setCurrentIndex(genre.findData("23"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    cards[0].analyze_requested.emit(video.url, video.title)
    assert emitted == [(int(channel.id), video.url, "23")]


def test_dashboard_upload_time_is_relative_for_24_hours_then_uses_date():
    recent = datetime.now(timezone.utc) - timedelta(hours=4, minutes=5)
    older = datetime.now(timezone.utc) - timedelta(hours=25)

    assert _relative_upload_time(recent.isoformat()) == "4 hours ago"
    assert _relative_upload_time(older.isoformat()) == older.astimezone().strftime("%d.%m.%Y")


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
    window.monitoring.refresh()

    assert window.monitoring.activity_log.isReadOnly()
    assert "Detailed copyable activity event" in window.monitoring.activity_log.toPlainText()
    assert "ANALYSIS · UI log source" in window.monitoring.activity_log.toPlainText()
    assert "WARNING" in window.monitoring.activity_log.toPlainText()
    window.monitoring.copy_logs.click()
    assert "Detailed copyable activity event" in QApplication.clipboard().text()


def test_activity_log_keeps_job_events_chronological_and_groups_application_events(qtbot, services):
    channel = services.repositories.channels.add(Channel(
        None, "UC_LOG_ORDER", "Log order", "", "https://youtube.test/log-order"
    ))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None,
        int(channel.id),
        "log-order-video",
        "Chronological log",
        "https://youtube.test/watch?v=log-order",
    ))
    job = services.repositories.jobs.create(int(source.id), utc_now(), manual=True)
    services.repositories.activity.add("Video selected · Chronological log · 3:20", job_id=job.id)
    services.repositories.activity.add("Queued Chronological log", job_id=job.id)
    services.repositories.activity.add("Application event one")
    services.repositories.activity.add("Application event two")

    window = MainWindow(services, start_background=False)
    qtbot.addWidget(window)
    text = window.monitoring.activity_log.toPlainText()

    assert text.index("Video selected") < text.index("Queued Chronological log")
    assert text.splitlines().count("APPLICATION") == 1
    assert "Not started" in text


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


def test_review_trim_updates_candidate_without_rendering(qtbot, services, tmp_path: Path):
    channel = services.repositories.channels.add(Channel(
        None, "UC_UI_TRIM", "UI Trim", "", "https://youtube.test/ui-trim"
    ))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None,
        int(channel.id),
        "ui-trim-video",
        "UI Trim Source",
        "https://youtube.test/watch?v=ui-trim",
        duration_seconds=60,
    ))
    candidate = services.repositories.candidates.replace_for_video(int(source.id), [
        ClipCandidate(
            None,
            int(source.id),
            5,
            25,
            88,
            {},
            refined_start_seconds=5,
            refined_end_seconds=25,
            ai_score=90,
        )
    ])[0]
    clip_path = tmp_path / "trim-buffer.mp4"
    clip_path.write_bytes(b"review-buffer")
    services.repositories.clips.add(RenderedClip(
        None,
        int(candidate.id),
        int(source.id),
        str(clip_path),
        55,
        "Vertical 9:16",
        buffer_start_seconds=0,
        buffer_end_seconds=55,
        trim_origin_seconds=5,
    ))
    window = MainWindow(services, start_background=False)
    qtbot.addWidget(window)

    assert window.review.trim.isEnabled()
    assert window.review.trim_start.text() == "0:00.0"
    assert window.review.trim_end.text() == "0:20.0"
    assert window.review.slider.maximum() == 20_000
    window.review.slider.setValue(8_000)
    window.review._trim_changing(7_000, 28_000)
    assert window.review.slider.maximum() == 21_000
    assert window.review.slider.value() == 8_000
    assert window.review.trim_start.text() == "0:02.0"
    assert window.review.trim_end.text() == "0:23.0"
    window.review._trim_changed(7_000, 28_000)
    updated = services.repositories.candidates.get(int(candidate.id))
    assert updated.render_start == 7
    assert updated.render_end == 28
    assert clip_path.read_bytes() == b"review-buffer"


def test_monitoring_task_manager_starts_detected_and_stops_active_jobs(qtbot, services):
    channel = services.repositories.channels.add(Channel(
        None, "UC_TASK_UI", "Task UI", "", "https://youtube.test/task-ui"
    ))
    detected_source, _ = services.repositories.videos.upsert(SourceVideo(
        None,
        int(channel.id),
        "detected-ui",
        "Detected but not started",
        "https://youtube.test/watch?v=detected-ui",
    ))
    detected = services.repositories.jobs.create(
        int(detected_source.id), utc_now(), manual=False
    )
    active_source, _ = services.repositories.videos.upsert(SourceVideo(
        None,
        int(channel.id),
        "active-ui",
        "Currently analyzing",
        "https://youtube.test/watch?v=active-ui",
    ))
    active = services.repositories.jobs.create(int(active_source.id), utc_now(), manual=True)
    services.repositories.jobs.update(active.id, JobStatus.ANALYZING, "Gemini ranking", 0.46)

    window = MainWindow(services, start_background=False)
    qtbot.addWidget(window)
    window.monitoring.refresh()

    assert window.monitoring.start_detected.text() == "Start detected (1)"
    assert "Currently analyzing" in window.monitoring.current_task.text()
    assert "46%" in window.monitoring.current_task.text()
    window.monitoring.start_detected.click()
    assert services.repositories.jobs.get(detected.id).approved

    window._cancel_analysis_job(active.id)
    stopping = services.repositories.jobs.get(active.id)
    assert stopping.cancel_requested
    assert stopping.stage == "Stopping…"


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
