from __future__ import annotations

from pathlib import Path

from clipradar.jobs.pipeline import AnalysisPipeline
from clipradar.models import ClipStatus, ReframeMode
from clipradar.storage.repositories import Repositories


class ReviewService:
    def __init__(self, repositories: Repositories, pipeline: AnalysisPipeline):
        self.repos = repositories
        self.pipeline = pipeline

    def approve(self, clip_id: int) -> None:
        clip = self._require(clip_id)
        if not Path(clip.file_path).exists():
            raise FileNotFoundError("The rendered clip file is missing.")
        self.pipeline.finalize_review_clip(clip_id)
        self.repos.clips.update_status(clip_id, ClipStatus.APPROVED)
        self.repos.activity.add(
            "Clip approved", "success", self.repos.jobs.job_id_for_clip(clip_id)
        )

    def finalize_for_publish(self, clip_id: int) -> None:
        clip = self._require(clip_id)
        if not Path(clip.file_path).exists():
            raise FileNotFoundError("The rendered clip file is missing.")
        self.pipeline.finalize_review_clip(clip_id)

    def update_trim(self, clip_id: int, start_seconds: float, end_seconds: float) -> None:
        clip = self._require(clip_id)
        if clip.status != ClipStatus.READY:
            raise RuntimeError("Wait for the current clip operation to finish.")
        candidate = self.repos.candidates.get(clip.candidate_id)
        if not candidate:
            raise ValueError("The clip candidate no longer exists.")
        lower = clip.buffer_start_seconds
        upper = clip.buffer_end_seconds
        if lower is None or upper is None:
            raise RuntimeError("This older clip has no editable review buffer.")
        if start_seconds < lower - 0.05 or end_seconds > upper + 0.05:
            raise ValueError("The selected range must stay inside the available review buffer.")
        self.repos.candidates.update_boundaries(
            int(candidate.id),
            max(lower, start_seconds),
            min(upper, end_seconds),
        )

    def reject(self, clip_id: int) -> None:
        self._require(clip_id)
        self.repos.clips.update_status(clip_id, ClipStatus.REJECTED)
        self.repos.activity.add("Clip rejected", job_id=self.repos.jobs.job_id_for_clip(clip_id))

    def delete_permanently(self, clip_id: int) -> None:
        clip = self._require(clip_id)
        job_id = self.repos.jobs.job_id_for_clip(clip_id)
        if clip.status == ClipStatus.REGENERATING:
            raise RuntimeError("Wait for the active regeneration to finish before deleting this clip.")
        path = Path(clip.file_path)
        file_existed = path.is_file() or path.is_symlink()
        sidecar = path.with_suffix(".ass")
        if sidecar.is_file() or sidecar.is_symlink():
            try:
                sidecar.unlink()
            except OSError as exc:
                raise OSError(f"The caption sidecar could not be deleted: {exc}") from exc
        if file_existed:
            try:
                path.unlink()
            except OSError as exc:
                raise OSError(f"The clip file could not be deleted: {exc}") from exc
        if not self.repos.clips.delete(clip_id):
            raise ValueError("The rendered clip no longer exists.")
        message = (
            f"Clip #{clip_id} permanently deleted from the review queue and disk"
            if file_existed
            else f"Clip #{clip_id} removed from the review queue · file was already missing"
        )
        self.repos.activity.add(message, "warning", job_id)

    def prepare_regeneration(self, clip_id: int, reframe_mode: str | None = None) -> tuple[int, str]:
        clip = self._require(clip_id)
        if clip.status == ClipStatus.REGENERATING:
            raise RuntimeError("This clip is already being regenerated.")
        if not Path(clip.file_path).is_file():
            raise FileNotFoundError("The rendered clip file is missing.")
        candidate = self.repos.candidates.get(clip.candidate_id)
        if not candidate:
            raise ValueError("The clip candidate no longer exists.")
        try:
            mode = ReframeMode(reframe_mode or candidate.reframe_mode).value
        except ValueError as exc:
            raise ValueError("Select a valid reframe mode.") from exc
        self.repos.clips.update_status(clip_id, ClipStatus.REGENERATING)
        return clip.candidate_id, mode

    def finish_regeneration(self, original_clip_id: int, candidate_id: int, reframe_mode: str) -> int:
        original = self._require(original_clip_id)
        job_id = self.repos.jobs.job_id_for_clip(original_clip_id)
        try:
            new_id = self.pipeline.regenerate_framing(
                candidate_id,
                original.file_path,
                reframe_mode,
                original.buffer_start_seconds,
                original.buffer_end_seconds,
                original.trim_origin_seconds,
                activity_job_id=job_id,
            )
        except Exception as exc:
            self.repos.clips.update_status(original_clip_id, ClipStatus.READY)
            self.repos.activity.add(
                f"Framing regeneration failed · {str(exc)[:500]}", "error", job_id
            )
            raise
        self.repos.clips.update_status(original_clip_id, ClipStatus.REJECTED)
        self.repos.activity.add("Regenerated framing is ready for review", "success", job_id)
        return new_id

    def _require(self, clip_id: int):
        clip = self.repos.clips.get(clip_id)
        if not clip:
            raise ValueError("The rendered clip no longer exists.")
        return clip
