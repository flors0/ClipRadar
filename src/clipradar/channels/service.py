from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from clipradar.models import Channel, SourceVideo, utc_now
from clipradar.settings.models import YOUTUBE_CATEGORY_NAMES
from clipradar.settings.service import SettingsService
from clipradar.storage.repositories import Repositories
from clipradar.youtube.client import RemoteVideo, YouTubeClient, YouTubeError


@dataclass(slots=True)
class ScanResult:
    channels_checked: int = 0
    videos_discovered: int = 0
    jobs_scheduled: int = 0
    errors: list[str] | None = None


class ChannelService:
    def __init__(self, repositories: Repositories, settings: SettingsService, youtube: YouTubeClient):
        self.repos = repositories
        self.settings = settings
        self.youtube = youtube

    def add_channel(self, identifier: str) -> Channel:
        remote = self.youtube.resolve_channel(identifier)
        existing = self.repos.channels.get_by_youtube_id(remote.channel_id)
        if existing:
            raise ValueError(f"{existing.name} is already being monitored.")
        defaults = self.settings.monitoring()
        clips = self.settings.clips()
        try:
            latest = self.youtube.list_recent_videos(remote.url, 1)
            baseline_video_id = latest[0].video_id if latest else remote.latest_video_id
        except Exception:
            baseline_video_id = remote.latest_video_id
        channel = self.repos.channels.add(Channel(
            id=None,
            channel_id=remote.channel_id,
            name=remote.name,
            avatar_url=remote.avatar_url,
            url=remote.url,
            monitoring_enabled=True,
            last_video_id=baseline_video_id,
            analysis_delay_minutes=defaults.default_analysis_delay_minutes,
            max_clips_per_video=clips.max_clips_per_video,
            min_duration_seconds=clips.minimum_duration,
            target_duration_seconds=clips.target_duration,
            max_duration_seconds=clips.maximum_duration,
        ))
        self.repos.activity.add(f"Added channel {channel.name}")
        return channel

    def set_monitoring(self, channel_id: int, enabled: bool) -> None:
        channel = self._require_channel(channel_id)
        self.repos.channels.update(channel_id, monitoring_enabled=enabled)
        self.repos.activity.add(f"{'Resumed' if enabled else 'Paused'} monitoring for {channel.name}")

    def remove_channel(self, channel_id: int) -> None:
        channel = self._require_channel(channel_id)
        self.repos.channels.delete(channel_id)
        self.repos.activity.add(f"Removed channel {channel.name}")

    def analyze_latest(self, channel_id: int) -> str:
        channel = self._require_channel(channel_id)
        videos = self.youtube.list_recent_videos(channel.url, 1)
        if not videos:
            raise YouTubeError("No finished upload was found for this channel.")
        source = self._store_remote(channel, videos[0])
        return self._queue(source, manual=True, scheduled_at=utc_now())

    def analyze_specific(self, channel_id: int, video_url: str, category_id: str) -> str:
        channel = self._require_channel(channel_id)
        if category_id not in YOUTUBE_CATEGORY_NAMES:
            raise ValueError("Select a valid video genre.")
        remote = self.youtube.resolve_video(video_url)
        if remote.channel_id and remote.channel_id != channel.channel_id:
            raise YouTubeError(f"That video belongs to a different channel, not {channel.name}.")
        source = self._store_remote(channel, remote, category_id=category_id)
        return self._queue(source, manual=True, scheduled_at=utc_now())

    def dashboard_videos(self, channel_id: int, limit: int = 18) -> tuple[int, list[RemoteVideo]]:
        channel = self._require_channel(channel_id)
        return int(channel.id), self.youtube.list_recent_videos(channel.url, limit)

    def scan_all(self) -> ScanResult:
        config = self.settings.monitoring()
        result = ScanResult(errors=[])
        for channel in self.repos.channels.list_all():
            if not channel.monitoring_enabled:
                continue
            try:
                discovered = self._scan_channel(channel, config.max_items_per_channel_check, config.max_new_videos_per_check)
                result.channels_checked += 1
                result.videos_discovered += len(discovered)
                result.jobs_scheduled += len(discovered)
            except Exception as exc:
                result.errors.append(f"{channel.name}: {exc}")
                self.repos.activity.add(f"Monitoring failed for {channel.name}: {exc}", "error")
        return result

    def _scan_channel(self, channel: Channel, scan_limit: int, max_new: int) -> list[SourceVideo]:
        videos = self.youtube.list_recent_videos(channel.url, scan_limit)
        checked_at = utc_now()
        if not videos:
            self.repos.channels.update(channel.id, last_checked_at=checked_at)
            return []
        if channel.last_video_id:
            new_items: list[RemoteVideo] = []
            for item in videos:
                if item.video_id == channel.last_video_id:
                    break
                new_items.append(item)
            new_items = new_items[:max_new]
        else:
            new_items = []
        self.repos.channels.update(channel.id, last_checked_at=checked_at, last_video_id=videos[0].video_id)
        stored: list[SourceVideo] = []
        for remote in reversed(new_items):
            source = self._store_remote(channel, remote)
            published = self._parse_datetime(remote.published_at) or datetime.now(timezone.utc)
            due = max(datetime.now(timezone.utc), published + timedelta(minutes=channel.analysis_delay_minutes))
            self._queue(source, manual=False, scheduled_at=due.isoformat(timespec="seconds"))
            stored.append(source)
        if stored:
            self.repos.activity.add(f"Detected {len(stored)} new upload{'s' if len(stored) != 1 else ''} from {channel.name}")
        return stored

    def _store_remote(
        self,
        channel: Channel,
        remote: RemoteVideo,
        *,
        category_id: str | None = None,
    ) -> SourceVideo:
        source, _ = self.repos.videos.upsert(SourceVideo(
            id=None,
            channel_id=int(channel.id),
            youtube_video_id=remote.video_id,
            title=remote.title,
            url=remote.url,
            published_at=remote.published_at,
            duration_seconds=remote.duration_seconds,
            thumbnail_url=remote.thumbnail_url,
            category_id=category_id,
        ))
        return source

    def _queue(self, source: SourceVideo, manual: bool, scheduled_at: str) -> str:
        if self.repos.jobs.has_active_for_video(int(source.id)):
            raise ValueError("This video already has an active analysis job.")
        job = self.repos.jobs.create(int(source.id), scheduled_at=scheduled_at, manual=manual)
        position = self.repos.jobs.queue_position(job.id)
        position_suffix = f" · sequential queue position {position}" if position is not None else ""
        category_name = YOUTUBE_CATEGORY_NAMES.get(source.category_id or "")
        category_suffix = f" · genre {category_name}" if category_name else ""
        self.repos.activity.add(
            f"Queued {source.title}{position_suffix}{category_suffix}", job_id=job.id
        )
        return job.id

    def _require_channel(self, channel_id: int) -> Channel:
        channel = self.repos.channels.get(channel_id)
        if not channel:
            raise ValueError("The channel no longer exists.")
        return channel

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
