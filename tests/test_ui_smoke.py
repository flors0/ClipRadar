from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from clipradar.models import Channel, ClipCandidate, RenderedClip, SourceVideo, utc_now
from clipradar.ui.main_window import MainWindow


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
