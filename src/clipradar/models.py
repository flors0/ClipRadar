from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStatus(StrEnum):
    WAITING = "Waiting"
    SCHEDULED = "Scheduled"
    DOWNLOADING = "Downloading"
    ANALYZING = "Analyzing"
    RENDERING = "Rendering"
    READY = "Ready"
    FAILED = "Failed"


class ClipStatus(StrEnum):
    READY = "Ready"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    REGENERATING = "Regenerating"
    FAILED = "Failed"


@dataclass(slots=True)
class Channel:
    id: int | None
    channel_id: str
    name: str
    avatar_url: str
    url: str
    monitoring_enabled: bool = True
    last_checked_at: str | None = None
    last_video_id: str | None = None
    analysis_delay_minutes: int = 90
    max_clips_per_video: int = 3
    min_duration_seconds: int = 20
    target_duration_seconds: int = 38
    max_duration_seconds: int = 60
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class SourceVideo:
    id: int | None
    channel_id: int
    youtube_video_id: str
    title: str
    url: str
    published_at: str | None = None
    duration_seconds: float | None = None
    thumbnail_url: str = ""
    local_path: str | None = None
    transcript_path: str | None = None
    discovered_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class AnalysisJob:
    id: str
    source_video_id: int
    status: JobStatus
    stage: str
    scheduled_at: str
    manual: bool = False
    attempts: int = 0
    progress: float = 0.0
    error: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class ClipCandidate:
    id: int | None
    source_video_id: int
    start_seconds: float
    end_seconds: float
    local_score: float
    signals: dict[str, Any]
    ai_score: float | None = None
    ai_reason: str = ""
    refined_start_seconds: float | None = None
    refined_end_seconds: float | None = None
    status: str = "Detected"

    @property
    def render_start(self) -> float:
        return self.refined_start_seconds if self.refined_start_seconds is not None else self.start_seconds

    @property
    def render_end(self) -> float:
        return self.refined_end_seconds if self.refined_end_seconds is not None else self.end_seconds


@dataclass(slots=True)
class RenderedClip:
    id: int | None
    candidate_id: int
    source_video_id: int
    file_path: str
    duration_seconds: float
    format: str
    status: ClipStatus = ClipStatus.READY
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @property
    def path(self) -> Path:
        return Path(self.file_path)

