from __future__ import annotations

from pathlib import Path

from clipradar.ai.gemini import EvaluationResult
from clipradar.analysis.candidates import CandidateDetector
from clipradar.media.ffmpeg import probe_media
from clipradar.models import Channel, ClipCandidate, SourceVideo, utc_now
from clipradar.settings.models import AISettings, ClipSettings


class FakeGemini:
    def analyze_candidate(self, *, candidate, **_kwargs):
        return EvaluationResult(
            score=91,
            reason="Strong reaction followed by a clear payoff.",
            refined_start=candidate.start_seconds,
            refined_end=candidate.end_seconds,
            input_tokens=100,
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

