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

    def prepare_regeneration(self, clip_id: int) -> int:
        clip = self._require(clip_id)
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
