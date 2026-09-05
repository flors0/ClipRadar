from __future__ import annotations

from pathlib import Path

from clipradar.jobs.pipeline import AnalysisPipeline
from clipradar.models import ClipStatus
from clipradar.storage.repositories import Repositories


class ReviewService:
    def __init__(self, repositories: Repositories, pipeline: AnalysisPipeline):
        self.repos = repositories
        self.pipeline = pipeline

    def approve(self, clip_id: int) -> None:
        clip = self._require(clip_id)
        if not Path(clip.file_path).exists():
            raise FileNotFoundError("The rendered clip file is missing.")
        self.repos.clips.update_status(clip_id, ClipStatus.APPROVED)
        self.repos.activity.add("Clip approved", "success")

    def reject(self, clip_id: int) -> None:
        self._require(clip_id)
        self.repos.clips.update_status(clip_id, ClipStatus.REJECTED)
        self.repos.activity.add("Clip rejected")

    def delete_permanently(self, clip_id: int) -> None:
        clip = self._require(clip_id)
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
        self.repos.activity.add(message, "warning")

    def prepare_regeneration(self, clip_id: int, reframe_mode: str | None = None) -> int:
        clip = self._require(clip_id)
        if reframe_mode:
            self.repos.candidates.update_reframe_mode(clip.candidate_id, reframe_mode)
        self.repos.clips.update_status(clip_id, ClipStatus.REGENERATING)
        return clip.candidate_id

    def finish_regeneration(self, original_clip_id: int, candidate_id: int) -> int:
        try:
            new_id = self.pipeline.render_existing_candidate(candidate_id)
        except Exception:
            self.repos.clips.update_status(original_clip_id, ClipStatus.READY)
            raise
        self.repos.clips.update_status(original_clip_id, ClipStatus.REJECTED)
        self.repos.activity.add("Clip regenerated", "success")
        return new_id

    def _require(self, clip_id: int):
        clip = self.repos.clips.get(clip_id)
        if not clip:
            raise ValueError("The rendered clip no longer exists.")
        return clip
