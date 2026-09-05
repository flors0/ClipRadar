from __future__ import annotations

from pathlib import Path

from clipradar.models import Channel, ClipCandidate, ClipStatus, RenderedClip, SourceVideo, utc_now


def _clip_record(services, path: Path) -> tuple[int, int]:
    channel = services.repositories.channels.add(Channel(
        None, "UC_REVIEW_DELETE", "Review source", "", "https://youtube.test/review"
    ))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None,
        int(channel.id),
        "review-delete-video",
        "Review deletion test",
        "https://youtube.test/watch?v=review-delete",
    ))
    candidate = services.repositories.candidates.replace_for_video(int(source.id), [
        ClipCandidate(None, int(source.id), 5, 30, 88, {}, ai_score=90, ai_reason="Clear payoff")
    ])[0]
    clip = services.repositories.clips.add(RenderedClip(
        None, int(candidate.id), int(source.id), str(path), 25, "Vertical 9:16"
    ))
    return int(clip.id), int(candidate.id)


def test_permanent_delete_removes_file_record_and_orphan_candidate(services, tmp_path):
    clip_path = tmp_path / "rendered.mp4"
    sidecar_path = clip_path.with_suffix(".ass")
    clip_path.write_bytes(b"rendered-media")
    sidecar_path.write_text("captions", encoding="utf-8")
    clip_id, candidate_id = _clip_record(services, clip_path)

    services.review.delete_permanently(clip_id)

    assert not clip_path.exists()
    assert not sidecar_path.exists()
    assert services.repositories.clips.get(clip_id) is None
    assert services.repositories.candidates.get(candidate_id) is None


def test_missing_file_can_be_rejected_or_permanently_removed(services, tmp_path):
    missing = tmp_path / "manually-removed.mp4"
    clip_id, _candidate_id = _clip_record(services, missing)
    services.review.reject(clip_id)
    services.repositories.clips.update_status(clip_id, ClipStatus.READY)
    services.review.delete_permanently(clip_id)
    assert services.repositories.clips.get(clip_id) is None
    assert "file was already missing" in services.repositories.activity.recent(1)[0]["message"]


def test_activity_records_are_joined_and_formatted_by_attempt(services):
    channel = services.repositories.channels.add(Channel(
        None, "UC_ACTIVITY", "Activity channel", "", "https://youtube.test/activity"
    ))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None,
        int(channel.id),
        "activity-video",
        "Structured activity test",
        "https://youtube.test/watch?v=activity",
    ))
    job = services.repositories.jobs.create(int(source.id), utc_now(), manual=True)
    services.repositories.activity.add("Analysis attempt 1 started", "info", job.id)
    services.repositories.activity.add("Gemini response was incomplete; retrying once", "warning", job.id)

    activities = services.repositories.activity.recent(10)
    assert activities[0]["operation_type"] == "Analysis"
    assert activities[0]["operation_title"] == "Structured activity test"
    assert activities[0]["channel_name"] == "Activity channel"
