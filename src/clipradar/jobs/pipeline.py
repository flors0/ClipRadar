from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Callable

from clipradar.ai.budget import BudgetGuard
from clipradar.ai.gemini import GEMINI_MAX_ATTEMPTS, GeminiClient, GeminiError
from clipradar.analysis.candidates import CandidateDetector
from clipradar.app.paths import AppPaths
from clipradar.media.ffmpeg import create_candidate_preview, create_framing_preview, probe_media
from clipradar.models import AnalysisJob, ClipCandidate, JobStatus, OperationCancelled, SourceVideo
from clipradar.rendering.renderer import ClipRenderer
from clipradar.settings.models import YOUTUBE_CATEGORY_NAMES
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
        created_clips: list[tuple[int, Path]] = []
        cancelled = lambda: self.repos.jobs.is_cancel_requested(job.id)
        try:
            self._check_cancel(job.id)
            self.repos.activity.add(
                f"Analysis attempt {job.attempts + 1} started · {source.title}", "info", job.id
            )
            self._set(job, JobStatus.DOWNLOADING, "Preparing source video", 0.05, progress, increment=True)
            cached_source = bool(source.local_path and Path(source.local_path).is_file())
            source, info_path = self._ensure_download(
                source,
                cancel_requested=cancelled,
                download_progress=lambda value: self._set(
                    job,
                    JobStatus.DOWNLOADING,
                    f"Downloading source video · {round(value * 100)}%",
                    0.05 + value * 0.15,
                    progress,
                ),
            )
            self._check_cancel(job.id)
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
            self._check_cancel(job.id)
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
            evaluated_count = 0
            below_threshold_count = 0
            overlap_count = 0
            budget_stopped = False
            max_outputs = min(clip_settings.max_clips_per_video, limits.max_clips_per_video)
            self.repos.activity.add(
                f"Local analysis found {len(candidates)} candidate{'s' if len(candidates) != 1 else ''} "
                f"across {duration / 60:.1f} source minutes · model {ai_settings.model} · "
                f"up to {max_outputs} clips · minimum score {ai_settings.minimum_ai_score}",
                "info",
                job.id,
            )
            for index, candidate in enumerate(candidates):
                self._check_cancel(job.id)
                if len(ranked) >= max_outputs:
                    break
                per_attempt_reserve = self._conservative_request_reserve(candidate, ai_settings.model)
                reserve = per_attempt_reserve * GEMINI_MAX_ATTEMPTS
                request_decision = self.budget.can_send_request(reserve, limits)
                if not request_decision.allowed:
                    self.repos.activity.add(request_decision.reason, "warning", job.id)
                    budget_stopped = True
                    break
                fraction = 0.32 + 0.33 * (index / max(1, len(candidates)))
                self._set(job, JobStatus.ANALYZING, f"Gemini ranking candidate {index + 1}/{len(candidates)}", fraction, progress)
                preview = self.paths.candidates / f"{source.youtube_video_id}_{candidate.id}.mp4"
                create_candidate_preview(
                    source.local_path,
                    preview,
                    candidate.start_seconds,
                    candidate.end_seconds,
                    cancel_requested=cancelled,
                )
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
                            content_category=YOUTUBE_CATEGORY_NAMES.get(source.category_id or ""),
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
                self._check_cancel(job.id)
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
                    gameplay_x=result.gameplay_x,
                    gameplay_y=result.gameplay_y,
                    gameplay_width=result.gameplay_width,
                    gameplay_height=result.gameplay_height,
                    hud_x=result.hud_x,
                    hud_y=result.hud_y,
                    hud_width=result.hud_width,
                    hud_height=result.hud_height,
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
                candidate.gameplay_x = result.gameplay_x
                candidate.gameplay_y = result.gameplay_y
                candidate.gameplay_width = result.gameplay_width
                candidate.gameplay_height = result.gameplay_height
                candidate.hud_x = result.hud_x
                candidate.hud_y = result.hud_y
                candidate.hud_width = result.hud_width
                candidate.hud_height = result.hud_height
                evaluated_count += 1
                meets_threshold = result.score >= ai_settings.minimum_ai_score
                overlaps = meets_threshold and self._overlaps_selected(candidate, ranked)
                if meets_threshold and not overlaps:
                    ranked.append(candidate)
                elif overlaps:
                    overlap_count += 1
                else:
                    below_threshold_count += 1
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
            self.repos.activity.add(
                f"Candidate summary · {len(candidates)} detected · {evaluated_count} Gemini-evaluated · "
                f"{below_threshold_count} below threshold · {overlap_count} overlapping · "
                f"{len(ranked)} selected"
                + (" · stopped by budget" if budget_stopped else ""),
                "info",
                job.id,
            )
            rendered = 0
            for index, candidate in enumerate(ranked[:max_outputs]):
                self._check_cancel(job.id)
                candidate = self._verify_initial_framing(
                    source,
                    candidate,
                    clip_settings,
                    job.id,
                    key,
                    ai_settings,
                    cancelled,
                )
                self._check_cancel(job.id)
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
                clip = self.renderer.render_review_buffer(
                    source,
                    candidate,
                    clip_settings,
                    output_override=self.settings.storage().output_directory,
                    cancel_requested=cancelled,
                )
                try:
                    saved_clip = self.repos.clips.add(clip)
                except Exception:
                    Path(clip.file_path).unlink(missing_ok=True)
                    Path(clip.file_path).with_suffix(".ass").unlink(missing_ok=True)
                    raise
                created_clips.append((int(saved_clip.id), Path(saved_clip.file_path)))
                self.repos.candidates.mark_rendered(int(candidate.id))
                rendered += 1
            if rendered:
                stage = f"{rendered} clip{'s' if rendered != 1 else ''} ready for review"
            elif budget_stopped and not evaluated_count:
                stage = "No clips rendered because the AI budget blocked candidate evaluation"
            elif not evaluated_count:
                stage = "No clips rendered because no candidate was evaluated"
            else:
                stage = "No candidate passed the quality and overlap checks"
            self._set(job, JobStatus.READY, stage, 1.0, progress)
            self.repos.activity.add(stage, "success" if rendered else "info", job.id)
            return rendered
        except OperationCancelled:
            self._cleanup_created_clips(created_clips)
            self.repos.jobs.mark_cancelled(job.id)
            self.repos.activity.add("Analysis cancelled by user", "warning", job.id)
            progress("Cancelled", job.progress)
            raise
        except Exception as exc:
            self._cleanup_created_clips(created_clips)
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

    def regenerate_framing(
        self,
        candidate_id: int,
        current_render_path: str | Path,
        requested_mode: str,
        buffer_start_seconds: float | None = None,
        buffer_end_seconds: float | None = None,
        trim_origin_seconds: float | None = None,
        *,
        activity_job_id: str | None = None,
    ) -> int:
        """Use Gemini to repair framing while leaving clip selection and metadata untouched."""
        candidate = self.repos.candidates.get(candidate_id)
        if not candidate:
            raise ValueError("The clip candidate no longer exists.")
        current_render = Path(current_render_path)
        if not current_render.is_file():
            raise FileNotFoundError("The rendered clip file is missing.")
        current_info = probe_media(current_render)
        source = self._require_video(candidate.source_video_id)
        source, _ = self._ensure_download(source)
        clip_settings = self._clip_settings_for(source)
        ai_settings = self.settings.ai()
        key = self.settings.secrets.get_gemini_key()
        if not key:
            raise GeminiError("No Gemini API key is configured. Open Settings → AI.")

        per_attempt_reserve = self._conservative_request_reserve(candidate, ai_settings.model)
        reserve = per_attempt_reserve * GEMINI_MAX_ATTEMPTS
        request_decision = self.budget.can_send_request(reserve, self.settings.budget())
        if not request_decision.allowed:
            raise RuntimeError(request_decision.reason.replace("next candidate", "framing regeneration"))

        original_preview = self.paths.candidates / f"reframe_{candidate_id}_original.mp4"
        current_preview = self.paths.candidates / f"reframe_{candidate_id}_current.mp4"
        self.repos.activity.add(
            f"Framing regeneration started · mode {requested_mode} · model {ai_settings.model}",
            job_id=activity_job_id,
        )
        try:
            create_candidate_preview(
                source.local_path,
                original_preview,
                candidate.render_start,
                candidate.render_end,
            )
            create_framing_preview(
                current_render,
                current_preview,
                max(
                    0.0,
                    candidate.render_start
                    - (buffer_start_seconds if buffer_start_seconds is not None else candidate.render_start),
                ),
                min(
                    current_info.duration,
                    candidate.render_end
                    - (buffer_start_seconds if buffer_start_seconds is not None else candidate.render_start),
                ),
            )
            self.repos.usage.add(estimated_cost_eur=reserve)
            try:
                result = self.gemini.analyze_framing(
                    api_key=key,
                    model=ai_settings.model,
                    original_preview_path=original_preview,
                    current_preview_path=current_preview,
                    requested_mode=requested_mode,
                    candidate=candidate,
                    temperature=ai_settings.temperature,
                    event_callback=lambda message, level: self.repos.activity.add(
                        message, level, activity_job_id
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
            original_preview.unlink(missing_ok=True)
            current_preview.unlink(missing_ok=True)
        self.repos.usage.add(
            requests=result.request_count,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost_eur=result.estimated_cost_eur - reserve,
        )

        proposal = replace(
            candidate,
            reframe_mode=result.reframe_mode,
            focus_x=result.focus_x,
            focus_y=result.focus_y,
            facecam_x=result.facecam_x,
            facecam_y=result.facecam_y,
            facecam_width=result.facecam_width,
            facecam_height=result.facecam_height,
            gameplay_x=result.gameplay_x,
            gameplay_y=result.gameplay_y,
            gameplay_width=result.gameplay_width,
            gameplay_height=result.gameplay_height,
            hud_x=result.hud_x,
            hud_y=result.hud_y,
            hud_width=result.hud_width,
            hud_height=result.hud_height,
        )
        rendered = None
        try:
            if buffer_start_seconds is not None and buffer_end_seconds is not None:
                rendered = self.renderer.render_review_buffer(
                    source,
                    proposal,
                    clip_settings,
                    output_override=self.settings.storage().output_directory,
                    trim_origin_seconds=trim_origin_seconds,
                    buffer_start_override=buffer_start_seconds,
                    buffer_end_override=buffer_end_seconds,
                )
            else:
                rendered = self.renderer.render(
                    source,
                    proposal,
                    clip_settings,
                    output_override=self.settings.storage().output_directory,
                )
            self._validate_regenerated_clip(
                rendered.file_path,
                proposal,
                clip_settings,
                rendered.buffer_start_seconds,
                rendered.buffer_end_seconds,
            )
            self._store_framing(candidate_id, proposal)
            try:
                saved = self.repos.clips.add(rendered)
            except Exception:
                self._store_framing(candidate_id, candidate)
                raise
        except Exception:
            if rendered:
                Path(rendered.file_path).unlink(missing_ok=True)
                Path(rendered.file_path).with_suffix(".ass").unlink(missing_ok=True)
            raise
        self.repos.activity.add(
            f"Framing regeneration completed · mode {result.reframe_mode} · "
            f"focus {result.focus_x:.3f},{result.focus_y:.3f} · {result.request_count} Gemini "
            f"request{'s' if result.request_count != 1 else ''} · {result.reason}",
            "success",
            activity_job_id,
        )
        return int(saved.id)

    def finalize_review_clip(self, clip_id: int) -> None:
        """Replace a padded review asset with the final selected time range."""
        clip = self.repos.clips.get(clip_id)
        if not clip:
            raise ValueError("The rendered clip no longer exists.")
        if clip.buffer_start_seconds is None or clip.buffer_end_seconds is None:
            return
        candidate = self.repos.candidates.get(clip.candidate_id)
        if not candidate:
            raise ValueError("The clip candidate no longer exists.")
        source = self._require_video(candidate.source_video_id)
        source, _ = self._ensure_download(source)
        rendered = self.renderer.render(
            source,
            candidate,
            self._clip_settings_for(source),
            output_override=self.settings.storage().output_directory,
        )
        self._validate_regenerated_clip(rendered.file_path, candidate, self._clip_settings_for(source))
        old_path = Path(clip.file_path)
        try:
            self.repos.clips.replace_media(clip_id, rendered)
        except Exception:
            Path(rendered.file_path).unlink(missing_ok=True)
            Path(rendered.file_path).with_suffix(".ass").unlink(missing_ok=True)
            raise
        if old_path != Path(rendered.file_path):
            old_path.unlink(missing_ok=True)
            old_path.with_suffix(".ass").unlink(missing_ok=True)
        self.repos.activity.add(
            f"Final trim rendered · {candidate.render_start:.1f}s–{candidate.render_end:.1f}s",
            "success",
            self.repos.jobs.job_id_for_clip(clip_id),
        )

    def _verify_initial_framing(
        self,
        source: SourceVideo,
        candidate: ClipCandidate,
        clip_settings,
        job_id: str,
        api_key: str,
        ai_settings,
        cancel_requested: Callable[[], bool],
    ) -> ClipCandidate:
        """Compare one draft render with the source before it reaches Review."""
        if candidate.reframe_mode not in {"gaming_split", "focus"}:
            return candidate
        per_attempt_reserve = self._conservative_request_reserve(candidate, ai_settings.model)
        reserve = per_attempt_reserve * GEMINI_MAX_ATTEMPTS
        decision = self.budget.can_send_request(reserve, self.settings.budget())
        if not decision.allowed:
            self.repos.activity.add(
                f"Initial framing check skipped · {decision.reason}", "warning", job_id
            )
            return candidate

        original_preview = self.paths.candidates / f"initial_{candidate.id}_original.mp4"
        current_preview = self.paths.candidates / f"initial_{candidate.id}_vertical.mp4"
        draft_path: Path | None = None
        reserve_recorded = False
        try:
            create_candidate_preview(
                source.local_path,
                original_preview,
                candidate.render_start,
                candidate.render_end,
                cancel_requested=cancel_requested,
            )
            preview_settings = replace(
                clip_settings,
                render_width=360,
                render_height=640,
                captions_enabled=False,
                audio_normalization=False,
            )
            draft = self.renderer.render(
                source,
                candidate,
                preview_settings,
                output_override=str(self.paths.candidates),
                cancel_requested=cancel_requested,
            )
            draft_path = Path(draft.file_path)
            create_framing_preview(
                draft_path,
                current_preview,
                0,
                draft.duration_seconds,
                cancel_requested=cancel_requested,
            )
            self.repos.usage.add(estimated_cost_eur=reserve)
            reserve_recorded = True
            try:
                result = self.gemini.analyze_framing(
                    api_key=api_key,
                    model=ai_settings.model,
                    original_preview_path=original_preview,
                    current_preview_path=current_preview,
                    requested_mode=candidate.reframe_mode,
                    candidate=candidate,
                    temperature=ai_settings.temperature,
                    event_callback=lambda message, level: self.repos.activity.add(
                        f"Initial framing check · {message}", level, job_id
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
                reserve_recorded = False
                raise
            self.repos.usage.add(
                requests=result.request_count,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                estimated_cost_eur=result.estimated_cost_eur - reserve,
            )
            reserve_recorded = False
            if cancel_requested():
                raise OperationCancelled("Analysis cancelled by user.")
            proposal = replace(
                candidate,
                reframe_mode=result.reframe_mode,
                focus_x=result.focus_x,
                focus_y=result.focus_y,
                facecam_x=result.facecam_x,
                facecam_y=result.facecam_y,
                facecam_width=result.facecam_width,
                facecam_height=result.facecam_height,
                gameplay_x=result.gameplay_x,
                gameplay_y=result.gameplay_y,
                gameplay_width=result.gameplay_width,
                gameplay_height=result.gameplay_height,
                hud_x=result.hud_x,
                hud_y=result.hud_y,
                hud_width=result.hud_width,
                hud_height=result.hud_height,
            )
            self._store_framing(int(candidate.id), proposal)
            self.repos.activity.add(
                f"Initial framing verified · mode {proposal.reframe_mode} · {result.reason}",
                "success",
                job_id,
            )
            return proposal
        except OperationCancelled:
            if reserve_recorded:
                self.repos.usage.add(estimated_cost_eur=-reserve)
            raise
        except Exception as exc:
            if reserve_recorded:
                self.repos.usage.add(estimated_cost_eur=-reserve)
            self.repos.activity.add(
                f"Initial framing check kept the original plan · {str(exc)[:300]}",
                "warning",
                job_id,
            )
            return candidate
        finally:
            original_preview.unlink(missing_ok=True)
            current_preview.unlink(missing_ok=True)
            if draft_path:
                draft_path.unlink(missing_ok=True)
                draft_path.with_suffix(".ass").unlink(missing_ok=True)

    def _store_framing(self, candidate_id: int, candidate: ClipCandidate) -> None:
        self.repos.candidates.update_framing(
            candidate_id,
            reframe_mode=candidate.reframe_mode,
            focus_x=candidate.focus_x,
            focus_y=candidate.focus_y,
            facecam_x=candidate.facecam_x,
            facecam_y=candidate.facecam_y,
            facecam_width=candidate.facecam_width,
            facecam_height=candidate.facecam_height,
            gameplay_x=candidate.gameplay_x,
            gameplay_y=candidate.gameplay_y,
            gameplay_width=candidate.gameplay_width,
            gameplay_height=candidate.gameplay_height,
            hud_x=candidate.hud_x,
            hud_y=candidate.hud_y,
            hud_width=candidate.hud_width,
            hud_height=candidate.hud_height,
        )

    @staticmethod
    def _validate_regenerated_clip(
        file_path: str,
        candidate: ClipCandidate,
        clip_settings,
        buffer_start_seconds: float | None = None,
        buffer_end_seconds: float | None = None,
    ) -> None:
        info = probe_media(file_path)
        expected_duration = (
            buffer_end_seconds - buffer_start_seconds
            if buffer_start_seconds is not None and buffer_end_seconds is not None
            else candidate.render_end - candidate.render_start
        )
        if info.duration <= 0 or abs(info.duration - expected_duration) > max(1.25, expected_duration * 0.12):
            raise RuntimeError("The regenerated clip failed duration validation. The existing clip was kept.")
        if clip_settings.output_format == "Vertical 9:16":
            expected = (clip_settings.render_width // 2 * 2, clip_settings.render_height // 2 * 2)
            if (info.width, info.height) != expected:
                raise RuntimeError("The regenerated clip failed resolution validation. The existing clip was kept.")

    def _ensure_download(
        self,
        source: SourceVideo,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        download_progress: Callable[[float], None] | None = None,
    ) -> tuple[SourceVideo, Path | None]:
        if cancel_requested and cancel_requested():
            raise OperationCancelled("Analysis cancelled by user.")
        if source.local_path and Path(source.local_path).exists():
            probe_media(source.local_path, cancel_requested=cancel_requested)
            info_files = list(Path(source.local_path).parent.glob("*.info.json"))
            return source, info_files[0] if info_files else None
        remote = self.youtube.resolve_video(source.url)
        if cancel_requested and cancel_requested():
            raise OperationCancelled("Analysis cancelled by user.")
        downloaded = self.youtube.download(
            remote,
            cancel_requested=cancel_requested,
            progress_callback=download_progress,
        )
        self.repos.videos.set_media(
            int(source.id), str(downloaded.video_path),
            str(downloaded.transcript_path) if downloaded.transcript_path else None,
            downloaded.duration_seconds,
        )
        return self._require_video(int(source.id)), downloaded.info_path

    def _check_cancel(self, job_id: str) -> None:
        if self.repos.jobs.is_cancel_requested(job_id):
            raise OperationCancelled("Analysis cancelled by user.")

    def _cleanup_created_clips(self, clips: list[tuple[int, Path]]) -> None:
        for clip_id, path in clips:
            path.unlink(missing_ok=True)
            path.with_suffix(".ass").unlink(missing_ok=True)
            self.repos.clips.delete(clip_id)

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
