from __future__ import annotations

from pathlib import Path

import pytest

from clipradar.ai.gemini import EvaluationResult, FramingResult, GeminiError
from clipradar.analysis.candidates import CandidateDetector
from clipradar.media.ffmpeg import probe_media
from clipradar.models import Channel, ClipCandidate, ClipStatus, SourceVideo, utc_now
from clipradar.rendering.renderer import ClipRenderer
from clipradar.settings.models import AISettings, ClipSettings


class FakeGemini:
    def __init__(self, score: int = 91):
        self.score = score

    def analyze_candidate(self, *, candidate, **_kwargs):
        return EvaluationResult(
            score=self.score,
            reason="Strong reaction followed by a clear payoff.",
            refined_start=candidate.start_seconds,
            refined_end=candidate.end_seconds,
            input_tokens=100,
            output_tokens=20,
            estimated_cost_eur=0.0002,
            request_count=2,
            title="That Minecraft Save Was Impossible",
            description="A last-second reaction turns an impossible Minecraft moment into a perfect short.",
            tags=("Minecraft", "gaming reaction", "clutch"),
            reframe_mode="gaming_split",
            focus_x=0.62,
            focus_y=0.56,
            facecam_x=0.02,
            facecam_y=0.04,
            facecam_width=0.19,
            facecam_height=0.24,
        )


class FakeFramingGemini:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.call = None

    def analyze_framing(self, **kwargs):
        self.call = kwargs
        assert Path(kwargs["original_preview_path"]).is_file()
        assert Path(kwargs["current_preview_path"]).is_file()
        if self.fail:
            raise GeminiError("Framing repair failed in test")
        return FramingResult(
            reason="The actual important subject is left of the previous center crop.",
            reframe_mode=kwargs["requested_mode"],
            focus_x=0.24,
            focus_y=0.46,
            facecam_x=None,
            facecam_y=None,
            facecam_width=None,
            facecam_height=None,
            input_tokens=80,
            output_tokens=20,
            estimated_cost_eur=0.0002,
        )


def test_candidate_detection_uses_local_signals(synthetic_video: Path, transcript_file: Path):
    detector = CandidateDetector()
    candidates = detector.detect(
        synthetic_video,
        transcript_file,
        [],
        source_video_id=1,
        minimum_duration=5,
        target_duration=8,
        maximum_duration=12,
        max_candidates=4,
    )
    assert 1 <= len(candidates) <= 4
    assert candidates[0].local_score > 0
    assert any(item.start_seconds <= 10 <= item.end_seconds for item in candidates)


def test_renderer_produces_vertical_captioned_video(services, synthetic_video: Path, transcript_file: Path):
    channel = services.repositories.channels.add(Channel(None, "UC_RENDER", "Render", "", "https://youtube.test/render"))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None, int(channel.id), "render-video", "Render Test", "https://youtube.test/watch?v=render",
        duration_seconds=18, local_path=str(synthetic_video), transcript_path=str(transcript_file),
    ))
    candidate = services.repositories.candidates.replace_for_video(int(source.id), [
        ClipCandidate(None, int(source.id), 5, 13, 90, {"transcript_excerpt": "No way!"}, ai_score=90,
                      ai_reason="Clear reaction", refined_start_seconds=5, refined_end_seconds=13)
    ])[0]
    settings = ClipSettings(
        minimum_duration=5, target_duration=8, maximum_duration=12,
        render_width=360, render_height=640, captions_enabled=True,
    )
    rendered = services.pipeline.renderer.render(source, candidate, settings)
    info = probe_media(rendered.file_path)
    assert Path(rendered.file_path).exists()
    assert (info.width, info.height) == (360, 640)
    assert 7.5 <= info.duration <= 8.5


def test_renderer_uses_gemini_facecam_and_scene_focus(services, synthetic_video: Path):
    channel = services.repositories.channels.add(Channel(
        None, "UC_REFRAME", "Reframe", "", "https://youtube.test/reframe"
    ))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None, int(channel.id), "reframe-video", "Reframe Test", "https://youtube.test/watch?v=reframe",
        duration_seconds=18, local_path=str(synthetic_video),
    ))
    candidate = services.repositories.candidates.replace_for_video(int(source.id), [
        ClipCandidate(
            None,
            int(source.id),
            5,
            13,
            90,
            {},
            ai_score=90,
            reframe_mode="gaming_split",
            focus_x=0.72,
            focus_y=0.55,
            facecam_x=0.02,
            facecam_y=0.04,
            facecam_width=0.18,
            facecam_height=0.24,
        )
    ])[0]
    settings = ClipSettings(
        minimum_duration=5,
        target_duration=8,
        maximum_duration=12,
        render_width=360,
        render_height=640,
        captions_enabled=False,
    )
    plan = ClipRenderer._video_filter(640, 360, candidate, settings, str(synthetic_video))
    assert plan.complex
    assert "vstack" in plan.value
    assert "[face]" in plan.value and "[game]" in plan.value
    rendered = services.pipeline.renderer.render(source, candidate, settings)
    info = probe_media(rendered.file_path)
    assert (info.width, info.height) == (360, 640)
    assert not Path(rendered.file_path).with_suffix(".ass").exists()


def test_complete_local_pipeline_reaches_review_queue(services, synthetic_video: Path, transcript_file: Path):
    services.settings.save_clips(ClipSettings(
        minimum_duration=5,
        target_duration=8,
        maximum_duration=12,
        max_clips_per_video=2,
        max_candidates_per_video=3,
        render_width=360,
        render_height=640,
    ))
    services.settings.save_ai(AISettings(model="fake-gemini", minimum_ai_score=60))
    services.pipeline.gemini = FakeGemini()
    channel = services.repositories.channels.add(Channel(
        None, "UC_E2E", "E2E Channel", "", "https://youtube.test/e2e",
        max_clips_per_video=2, min_duration_seconds=5, target_duration_seconds=8, max_duration_seconds=12,
    ))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None, int(channel.id), "e2e-video", "E2E Source", "https://youtube.test/watch?v=e2e",
        duration_seconds=18, local_path=str(synthetic_video), transcript_path=str(transcript_file),
    ))
    job = services.repositories.jobs.create(int(source.id), utc_now(), manual=True)
    rendered_count = services.pipeline.run(job.id)
    finished = services.repositories.jobs.get(job.id)
    queue = services.repositories.clips.list_review()
    assert rendered_count == 2
    assert finished.status.value == "Ready"
    assert len(queue) == 2
    assert all(Path(item["file_path"]).exists() for item in queue)
    assert all(item["ai_title"] for item in queue)
    assert all("Minecraft" in item["ai_tags_json"] for item in queue)
    assert all(item["reframe_mode"] == "gaming_split" for item in queue)
    usage = services.repositories.usage.get_today()
    assert usage["requests"] == rendered_count * 2
    assert usage["estimated_cost_eur"] == pytest.approx(rendered_count * 0.0002)
    messages = [item["message"] for item in services.repositories.activity.recent(100)]
    assert any("Candidate summary" in message and "selected" in message for message in messages)


def test_valid_zero_clip_result_explains_why_nothing_reached_review(
    services,
    synthetic_video: Path,
    transcript_file: Path,
):
    services.settings.save_clips(ClipSettings(
        minimum_duration=5,
        target_duration=8,
        maximum_duration=12,
        max_clips_per_video=2,
        max_candidates_per_video=3,
        render_width=360,
        render_height=640,
    ))
    services.settings.save_ai(AISettings(model="fake-gemini", minimum_ai_score=60))
    services.pipeline.gemini = FakeGemini(score=20)
    channel = services.repositories.channels.add(Channel(
        None,
        "UC_NO_CLIPS",
        "No Clips Channel",
        "",
        "https://youtube.test/no-clips",
        max_clips_per_video=2,
        min_duration_seconds=5,
        target_duration_seconds=8,
        max_duration_seconds=12,
    ))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None,
        int(channel.id),
        "no-clips-video",
        "Valid video with no strong moments",
        "https://youtube.test/watch?v=no-clips",
        duration_seconds=18,
        local_path=str(synthetic_video),
        transcript_path=str(transcript_file),
    ))
    job = services.repositories.jobs.create(int(source.id), utc_now(), manual=True)

    assert services.pipeline.run(job.id) == 0
    finished = services.repositories.jobs.get(job.id)
    assert finished.status.value == "Ready"
    assert finished.stage == "No candidate passed the quality and overlap checks"
    assert services.repositories.clips.list_review() == []
    messages = [item["message"] for item in services.repositories.activity.recent(100)]
    assert any("Candidate summary" in message and "0 selected" in message for message in messages)


def _existing_review_clip(services, synthetic_video: Path, tmp_path: Path):
    services.settings.save_clips(ClipSettings(
        minimum_duration=5,
        target_duration=8,
        maximum_duration=12,
        render_width=360,
        render_height=640,
        captions_enabled=False,
    ))
    services.settings.save_ai(AISettings(model="gemini-3.5-flash-lite"))
    channel = services.repositories.channels.add(Channel(
        None,
        "UC_REGENERATE",
        "Regenerate Channel",
        "",
        "https://youtube.test/regenerate",
        min_duration_seconds=5,
        target_duration_seconds=8,
        max_duration_seconds=12,
    ))
    source, _ = services.repositories.videos.upsert(SourceVideo(
        None,
        int(channel.id),
        "regenerate-video",
        "Regenerate Source",
        "https://youtube.test/watch?v=regenerate",
        duration_seconds=18,
        local_path=str(synthetic_video),
    ))
    candidate = services.repositories.candidates.replace_for_video(int(source.id), [
        ClipCandidate(
            None,
            int(source.id),
            5,
            13,
            90,
            {},
            ai_score=91,
            ai_title="Metadata must stay unchanged",
            reframe_mode="center",
            focus_x=0.5,
            focus_y=0.5,
        )
    ])[0]
    original = services.pipeline.renderer.render(
        source,
        candidate,
        services.settings.clips(),
        output_override=str(tmp_path),
    )
    original = services.repositories.clips.add(original)
    return source, candidate, original


def test_regenerate_framing_reanalyzes_important_subject_and_keeps_metadata(
    services,
    synthetic_video: Path,
    tmp_path: Path,
):
    _source, candidate, original = _existing_review_clip(services, synthetic_video, tmp_path)
    fake = FakeFramingGemini()
    services.pipeline.gemini = fake

    candidate_id, mode = services.review.prepare_regeneration(int(original.id), "focus")
    new_id = services.review.finish_regeneration(int(original.id), candidate_id, mode)

    updated = services.repositories.candidates.get(int(candidate.id))
    new_clip = services.repositories.clips.get(new_id)
    assert fake.call["requested_mode"] == "focus"
    assert updated.reframe_mode == "focus"
    assert updated.focus_x == pytest.approx(0.24)
    assert updated.ai_title == "Metadata must stay unchanged"
    assert services.repositories.clips.get(int(original.id)).status == ClipStatus.REJECTED
    assert new_clip.status == ClipStatus.READY
    assert Path(new_clip.file_path).is_file()
    assert new_clip.file_path != original.file_path
    assert not Path(fake.call["original_preview_path"]).exists()
    assert not Path(fake.call["current_preview_path"]).exists()


def test_failed_framing_analysis_restores_original_ready_clip_and_candidate(
    services,
    synthetic_video: Path,
    tmp_path: Path,
):
    _source, candidate, original = _existing_review_clip(services, synthetic_video, tmp_path)
    services.pipeline.gemini = FakeFramingGemini(fail=True)

    candidate_id, mode = services.review.prepare_regeneration(int(original.id), "focus")
    with pytest.raises(GeminiError, match="Framing repair failed"):
        services.review.finish_regeneration(int(original.id), candidate_id, mode)

    unchanged = services.repositories.candidates.get(int(candidate.id))
    assert services.repositories.clips.get(int(original.id)).status == ClipStatus.READY
    assert unchanged.reframe_mode == "center"
    assert unchanged.focus_x == pytest.approx(0.5)
    assert Path(original.file_path).is_file()
