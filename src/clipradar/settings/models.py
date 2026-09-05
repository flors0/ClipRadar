from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, TypeVar


@dataclass(slots=True)
class GeneralSettings:
    start_monitoring_on_launch: bool = True
    minimize_to_tray: bool = False


@dataclass(slots=True)
class MonitoringSettings:
    interval_minutes: int = 10
    max_items_per_channel_check: int = 12
    max_new_videos_per_check: int = 3
    default_analysis_delay_minutes: int = 90


@dataclass(slots=True)
class ClipSettings:
    minimum_duration: int = 20
    target_duration: int = 38
    maximum_duration: int = 60
    max_clips_per_video: int = 3
    max_candidates_per_video: int = 14
    output_format: str = "Vertical 9:16"
    render_width: int = 1080
    render_height: int = 1920
    captions_enabled: bool = False
    word_highlighting: bool = False
    audio_normalization: bool = True


@dataclass(slots=True)
class AISettings:
    provider: str = "Gemini"
    model: str = "gemini-3.5-flash-lite"
    temperature: float = 0.2
    minimum_ai_score: int = 62


@dataclass(slots=True)
class BudgetSettings:
    max_videos_per_day: int = 5
    max_source_minutes_per_day: int = 300
    max_clips_per_video: int = 3
    max_ai_cost_per_day_eur: float = 2.0


@dataclass(slots=True)
class StorageSettings:
    output_directory: str = ""
    keep_source_videos: bool = True
    keep_candidate_previews: bool = False


@dataclass(slots=True)
class PublishingSettings:
    timezone: str = "Europe/Berlin"
    description_style: str = "Auto"
    metadata_language: str = "Auto"
    default_privacy: str = "Private"
    category_id: str = "20"
    made_for_kids: bool = False
    notify_subscribers: bool = False
    max_uploads_per_day: int = 10
    default_tags: str = ""


T = TypeVar("T")


def merge_dataclass(cls: type[T], value: dict[str, Any] | None) -> T:
    defaults = asdict(cls())
    if value:
        defaults.update({key: item for key, item in value.items() if key in defaults})
    return cls(**defaults)
