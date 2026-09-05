from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from clipradar.ai.budget import BudgetGuard
from clipradar.ai.gemini import GEMINI_MAX_ATTEMPTS, GeminiClient, GeminiError
from clipradar.analysis.candidates import CandidateDetector
from clipradar.app.paths import AppPaths
from clipradar.media.ffmpeg import create_candidate_preview, probe_media
from clipradar.models import AnalysisJob, ClipCandidate, JobStatus, SourceVideo
from clipradar.rendering.renderer import ClipRenderer
from clipradar.settings.service import SettingsService
from clipradar.storage.repositories import Repositories
from clipradar.youtube.client import YouTubeClient


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, float], None]


class AnalysisPipeline:
    def __init__(
        self,
        paths: AppPaths,
        repositories: Repositories,
        settings: SettingsService,
        youtube: YouTubeClient,
        detector: CandidateDetector,
        gemini: GeminiClient,
        budget: BudgetGuard,
        renderer: ClipRenderer,
    ):
        self.paths = paths
        self.repos = repositories
        self.settings = settings
        self.youtube = youtube
        self.detector = detector
        self.gemini = gemini
        self.budget = budget
        self.renderer = renderer

    def run(self, job_id: str, progress: ProgressCallback | None = None) -> int:
        progress = progress or (lambda _stage, _value: None)
        job = self._require_job(job_id)
        source = self._require_video(job.source_video_id)
        try:
            self.repos.activity.add(
                f"Analysis attempt {job.attempts + 1} started · {source.title}", "info", job.id
            )
            self._set(job, JobStatus.DOWNLOADING, "Preparing source video", 0.05, progress, increment=True)
            cached_source = bool(source.local_path and Path(source.local_path).is_file())
            source, info_path = self._ensure_download(source)
            self.repos.activity.add(
                "Source video ready · existing download reused"
                if cached_source else "Source video downloaded and verified",
                "success",
                job.id,
            )
            clip_settings = self._clip_settings_for(source)
            self._set(job, JobStatus.ANALYZING, "Detecting local candidates", 0.22, progress)
            heatmap = self.youtube.load_heatmap(info_path)
            candidates = self.detector.detect(
                source.local_path,
                source.transcript_path,
                heatmap,
                source_video_id=int(source.id),
                minimum_duration=clip_settings.minimum_duration,
                target_duration=clip_settings.target_duration,
                maximum_duration=clip_settings.maximum_duration,
                max_candidates=clip_settings.max_candidates_per_video,
            )
            candidates = self.repos.candidates.replace_for_video(int(source.id), candidates)
            if not candidates:
                raise RuntimeError("No usable clip candidates were found.")

            duration = float(source.duration_seconds or probe_media(source.local_path).duration)
            limits = self.settings.budget()
            decision = self.budget.can_start_video(duration, limits)
            if not decision.allowed:
                self.repos.activity.add(f"Budget check blocked analysis · {decision.reason}", "warning", job.id)
                raise RuntimeError(decision.reason)
            key = self.settings.secrets.get_gemini_key()
            if not key:
                raise GeminiError("No Gemini API key is configured. Open Settings → AI.")
            self.repos.usage.add(videos=1, source_minutes=duration / 60)
            ai_settings = self.settings.ai()
            publishing_settings = self.settings.publishing()
            ranked: list[ClipCandidate] = []
            max_outputs = min(clip_settings.max_clips_per_video, limits.max_clips_per_video)
            self.repos.activity.add(
                f"Local analysis found {len(candidates)} candidate{'s' if len(candidates) != 1 else ''} "
                f"across {duration / 60:.1f} source minutes · model {ai_settings.model} · "
                f"up to {max_outputs} clips · minimum score {ai_settings.minimum_ai_score}",
                "info",
                job.id,
            )
            for index, candidate in enumerate(candidates):
                if len(ranked) >= max_outputs:
                    break
                per_attempt_reserve = self._conservative_request_reserve(candidate, ai_settings.model)
                reserve = per_attempt_reserve * GEMINI_MAX_ATTEMPTS
                request_decision = self.budget.can_send_request(reserve, limits)
                if not request_decision.allowed:
                    self.repos.activity.add(request_decision.reason, "warning", job.id)
                    break
                fraction = 0.32 + 0.33 * (index / max(1, len(candidates)))
                self._set(job, JobStatus.ANALYZING, f"Gemini ranking candidate {index + 1}/{len(candidates)}", fraction, progress)
                preview = self.paths.candidates / f"{source.youtube_video_id}_{candidate.id}.mp4"
                create_candidate_preview(source.local_path, preview, candidate.start_seconds, candidate.end_seconds)
                # Reserve both possible structured-output attempts before the first paid call.
                # Unused reserve is reconciled immediately after the response.
                self.repos.usage.add(estimated_cost_eur=reserve)
                self.repos.activity.add(
                    f"Candidate {index + 1}/{len(candidates)} sent to {ai_settings.model} · "
                    f"source {candidate.start_seconds:.1f}s–{candidate.end_seconds:.1f}s · "
                    f"local score {candidate.local_score:.0f}/100",
                    "info",
                    job.id,
                )
                try:
                    try:
                        result = self.gemini.analyze_candidate(
                            api_key=key,
                            model=ai_settings.model,
                            preview_path=preview,
                            candidate=candidate,
                            source_duration=duration,
                            minimum_duration=clip_settings.minimum_duration,
                            maximum_duration=clip_settings.maximum_duration,
                            temperature=ai_settings.temperature,
                            description_style=publishing_settings.description_style,
                            metadata_language=publishing_settings.metadata_language,
                            event_callback=lambda message, level: self.repos.activity.add(
                                f"Candidate {index + 1}/{len(candidates)} · {message}", level, job.id
                            ),
                        )
                    except GeminiError as exc:
                        unknown_requests = max(0, exc.request_count - exc.responses_with_usage)
                        retained_cost = exc.estimated_cost_eur + per_attempt_reserve * unknown_requests
                        self.repos.usage.add(
                            requests=exc.request_count,
                            input_tokens=exc.input_tokens,
                            output_tokens=exc.output_tokens,
                            estimated_cost_eur=retained_cost - reserve,
                        )
                        raise
                finally:
                    if not self.settings.storage().keep_candidate_previews:
                        preview.unlink(missing_ok=True)
                self.repos.usage.add(
                    requests=result.request_count,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    estimated_cost_eur=result.estimated_cost_eur - reserve,
                )
                self.repos.candidates.apply_ai_result(
                    int(candidate.id), result.score, result.reason, result.refined_start, result.refined_end,
                    title=result.title,
                    description=result.description,
                    tags=list(result.tags),
                    reframe_mode=result.reframe_mode,
                    focus_x=result.focus_x,
                    focus_y=result.focus_y,
                    facecam_x=result.facecam_x,
                    facecam_y=result.facecam_y,
                    facecam_width=result.facecam_width,
                    facecam_height=result.facecam_height,
                )
                candidate.ai_score = result.score
                candidate.ai_reason = result.reason
                candidate.refined_start_seconds = result.refined_start
                candidate.refined_end_seconds = result.refined_end
                candidate.ai_title = result.title
                candidate.ai_description = result.description
                candidate.ai_tags = list(result.tags)
                candidate.reframe_mode = result.reframe_mode
                candidate.focus_x = result.focus_x
                candidate.focus_y = result.focus_y
                candidate.facecam_x = result.facecam_x
                candidate.facecam_y = result.facecam_y
                candidate.facecam_width = result.facecam_width
                candidate.facecam_height = result.facecam_height
                meets_threshold = result.score >= ai_settings.minimum_ai_score
                overlaps = meets_threshold and self._overlaps_selected(candidate, ranked)
                if meets_threshold and not overlaps:
                    ranked.append(candidate)
                outcome = (
                    "selected for rendering"
                    if meets_threshold and not overlaps
                    else "skipped because it overlaps a stronger selected moment"
                    if overlaps
                    else f"below the {ai_settings.minimum_ai_score}/100 threshold"
                )
                self.repos.activity.add(
                    f"Candidate {index + 1}/{len(candidates)} scored {result.score}/100 · {outcome} · "
                    f"{result.request_count} Gemini request{'s' if result.request_count != 1 else ''} · "
                    f"{result.input_tokens:,} input / {result.output_tokens:,} output tokens",
                    "success" if meets_threshold and not overlaps else "info",
                    job.id,
                )

            ranked.sort(key=lambda item: item.ai_score or 0, reverse=True)
            rendered = 0
            for index, candidate in enumerate(ranked[:max_outputs]):
                self.repos.activity.add(
                    f"Rendering clip {index + 1}/{len(ranked[:max_outputs])} · "
                    f"{candidate.render_start:.1f}s–{candidate.render_end:.1f}s · "
                    f"framing {candidate.reframe_mode}",
                    "info",
                    job.id,
                )
                self._set(
                    job, JobStatus.RENDERING, f"Rendering clip {index + 1}/{len(ranked[:max_outputs])}",
                    0.68 + 0.27 * (index / max(1, len(ranked))), progress,
                )
                clip = self.renderer.render(
                    source,
                    candidate,
                    clip_settings,
                    output_override=self.settings.storage().output_directory,
                )
                self.repos.clips.add(clip)
                self.repos.candidates.mark_rendered(int(candidate.id))
                rendered += 1
            stage = f"{rendered} clip{'s' if rendered != 1 else ''} ready for review" if rendered else "No candidate passed the quality threshold"
            self._set(job, JobStatus.READY, stage, 1.0, progress)
            self.repos.activity.add(stage, "success" if rendered else "info", job.id)
            return rendered
        except Exception as exc:
            safe_error = str(exc)[:700]
            self.repos.jobs.update(job.id, JobStatus.FAILED, "Failed", job.progress, safe_error)
            self.repos.activity.add(f"Analysis failed · {safe_error}", "error", job.id)
            logger.exception("Analysis job %s failed", job.id)
            raise

    def render_existing_candidate(self, candidate_id: int) -> int:
        candidate = self.repos.candidates.get(candidate_id)
        if not candidate:
            raise ValueError("The clip candidate no longer exists.")
        source = self._require_video(candidate.source_video_id)
        source, _ = self._ensure_download(source)
        clip = self.renderer.render(
            source,
            candidate,
            self._clip_settings_for(source),
            output_override=self.settings.storage().output_directory,
        )
        saved = self.repos.clips.add(clip)
        return int(saved.id)

    def _ensure_download(self, source: SourceVideo) -> tuple[SourceVideo, Path | None]:
        if source.local_path and Path(source.local_path).exists():
            probe_media(source.local_path)
            info_files = list(Path(source.local_path).parent.glob("*.info.json"))
            return source, info_files[0] if info_files else None
        remote = self.youtube.resolve_video(source.url)
        downloaded = self.youtube.download(remote)
        self.repos.videos.set_media(
            int(source.id), str(downloaded.video_path),
            str(downloaded.transcript_path) if downloaded.transcript_path else None,
            downloaded.duration_seconds,
        )
        return self._require_video(int(source.id)), downloaded.info_path

    def _clip_settings_for(self, source: SourceVideo):
        clip_settings = self.settings.clips()
        channel = self.repos.channels.get(source.channel_id)
        if channel:
            clip_settings.minimum_duration = channel.min_duration_seconds
            clip_settings.target_duration = channel.target_duration_seconds
            clip_settings.maximum_duration = channel.max_duration_seconds
            clip_settings.max_clips_per_video = channel.max_clips_per_video
        return clip_settings

    def _set(
        self,
        job: AnalysisJob,
        status: JobStatus,
        stage: str,
        value: float,
        progress: ProgressCallback,
        increment: bool = False,
    ) -> None:
        job.status, job.stage, job.progress = status, stage, value
        self.repos.jobs.update(job.id, status, stage, value, increment_attempts=increment)
        progress(stage, value)

    def _require_job(self, job_id: str) -> AnalysisJob:
        job = self.repos.jobs.get(job_id)
        if not job:
            raise ValueError("The analysis job no longer exists.")
        return job

    def _require_video(self, video_id: int) -> SourceVideo:
        video = self.repos.videos.get(video_id)
        if not video:
            raise ValueError("The source video no longer exists.")
        return video

    @staticmethod
    def _overlaps_selected(candidate: ClipCandidate, selected: list[ClipCandidate]) -> bool:
        for existing in selected:
            overlap = max(0, min(candidate.render_end, existing.render_end) - max(candidate.render_start, existing.render_start))
            shorter = min(candidate.render_end - candidate.render_start, existing.render_end - existing.render_start)
            if shorter > 0 and overlap / shorter >= 0.55:
                return True
        return False

    @staticmethod
    def _conservative_request_reserve(candidate: ClipCandidate, model: str = "") -> float:
        duration_reserve = (candidate.end_seconds - candidate.start_seconds) * 0.0008
        high_quality_floor = 0.06 if any(value in model.lower() for value in ("3.8", "3.7")) else 0.025
        return max(high_quality_floor, duration_reserve)
