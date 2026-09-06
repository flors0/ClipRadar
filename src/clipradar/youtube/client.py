from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clipradar.app.paths import AppPaths, bundled_binary


logger = logging.getLogger(__name__)
YOUTUBE_VIDEO_RE = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?.*?v=|shorts/|live/))([\w-]{6,})", re.I)


class YouTubeError(RuntimeError):
    pass


@dataclass(slots=True)
class ResolvedChannel:
    channel_id: str
    name: str
    url: str
    avatar_url: str = ""
    latest_video_id: str | None = None


@dataclass(slots=True)
class RemoteVideo:
    video_id: str
    title: str
    url: str
    channel_id: str
    published_at: str | None = None
    duration_seconds: float | None = None
    thumbnail_url: str = ""
    heatmap: list[dict[str, float]] = field(default_factory=list)


@dataclass(slots=True)
class DownloadedMedia:
    video_path: Path
    transcript_path: Path | None
    info_path: Path | None
    duration_seconds: float


def normalize_youtube_input(value: str) -> str:
    value = value.strip()
    if not value:
        raise YouTubeError("Enter a YouTube channel, handle, channel ID, or video URL.")
    if value.startswith("UC") and len(value) >= 20 and "/" not in value:
        return f"https://www.youtube.com/channel/{value}"
    if value.startswith("@"):
        return f"https://www.youtube.com/{value}"
    if not value.startswith(("http://", "https://")):
        return f"https://www.youtube.com/@{value.lstrip('@')}"
    if "youtube.com" not in value and "youtu.be" not in value:
        raise YouTubeError("Only YouTube URLs are supported.")
    return value


class YouTubeClient:
    def __init__(self, paths: AppPaths):
        self.paths = paths

    @staticmethod
    def _ydl(options: dict[str, Any]):
        try:
            import yt_dlp
        except ImportError as exc:
            raise YouTubeError("yt-dlp is not installed.") from exc
        return yt_dlp.YoutubeDL(options)

    @staticmethod
    def _quiet_options() -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "socket_timeout": 20,
            "retries": 2,
            "extractor_retries": 2,
        }

    def resolve_channel(self, identifier: str) -> ResolvedChannel:
        url = normalize_youtube_input(identifier)
        video_match = YOUTUBE_VIDEO_RE.search(url)
        options = self._quiet_options()
        if not video_match:
            options.update({"extract_flat": "in_playlist", "playlistend": 1, "lazy_playlist": False})
        else:
            options["noplaylist"] = True
        try:
            with self._ydl(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise YouTubeError(f"YouTube could not resolve that address: {exc}") from exc

        first = next(iter(info.get("entries") or []), None) or info
        channel_id = info.get("channel_id") or first.get("channel_id") or info.get("uploader_id") or first.get("uploader_id")
        name = info.get("channel") or first.get("channel") or info.get("uploader") or first.get("uploader")
        channel_url = info.get("channel_url") or first.get("channel_url")
        if not channel_id:
            raise YouTubeError("The YouTube channel ID could not be determined.")
        if not channel_url:
            channel_url = f"https://www.youtube.com/channel/{channel_id}"
        thumbnails = info.get("thumbnails") or first.get("thumbnails") or []
        avatar = thumbnails[-1].get("url", "") if thumbnails else ""
        latest_id = first.get("id") if info.get("entries") else (info.get("id") if video_match else None)
        return ResolvedChannel(str(channel_id), str(name or channel_id), str(channel_url), str(avatar), latest_id)

    def list_recent_videos(self, channel_url: str, limit: int = 12) -> list[RemoteVideo]:
        url = channel_url.rstrip("/")
        if not url.endswith(("/videos", "/streams", "/shorts")):
            url += "/videos"
        options = self._quiet_options()
        options.update({"extract_flat": "in_playlist", "playlistend": max(1, min(limit, 50)), "lazy_playlist": False})
        try:
            with self._ydl(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise YouTubeError(f"Recent uploads could not be checked: {exc}") from exc
        channel_id = str(info.get("channel_id") or info.get("uploader_id") or "")
        videos: list[RemoteVideo] = []
        for entry in info.get("entries") or []:
            if not entry or entry.get("live_status") in {"is_live", "is_upcoming"}:
                continue
            video_id = entry.get("id")
            if not video_id:
                continue
            published = _published_at(entry)
            thumbnails = entry.get("thumbnails") or []
            thumbnail = thumbnails[-1].get("url", "") if thumbnails else entry.get("thumbnail", "")
            videos.append(RemoteVideo(
                video_id=str(video_id),
                title=str(entry.get("title") or video_id),
                url=str(entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"),
                channel_id=str(entry.get("channel_id") or channel_id),
                published_at=published,
                duration_seconds=float(entry["duration"]) if entry.get("duration") else None,
                thumbnail_url=str(thumbnail or ""),
            ))
        return videos

    def resolve_video(self, url_or_id: str) -> RemoteVideo:
        value = url_or_id.strip()
        if re.fullmatch(r"[\w-]{6,}", value):
            value = f"https://www.youtube.com/watch?v={value}"
        options = self._quiet_options() | {"noplaylist": True}
        try:
            with self._ydl(options) as ydl:
                info = ydl.extract_info(value, download=False)
        except Exception as exc:
            raise YouTubeError(f"The video could not be resolved: {exc}") from exc
        if info.get("live_status") in {"is_live", "is_upcoming"}:
            raise YouTubeError("Live and upcoming videos cannot be analyzed yet.")
        timestamp = info.get("timestamp") or info.get("release_timestamp")
        published = datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp else None
        thumbnails = info.get("thumbnails") or []
        heatmap = [
            {
                "start_time": float(item.get("start_time", 0)),
                "end_time": float(item.get("end_time", 0)),
                "value": float(item.get("value", 0)),
            }
            for item in (info.get("heatmap") or [])
        ]
        return RemoteVideo(
            video_id=str(info["id"]),
            title=str(info.get("title") or info["id"]),
            url=str(info.get("webpage_url") or value),
            channel_id=str(info.get("channel_id") or info.get("uploader_id") or ""),
            published_at=published,
            duration_seconds=float(info["duration"]) if info.get("duration") else None,
            thumbnail_url=str(thumbnails[-1].get("url", "") if thumbnails else info.get("thumbnail", "")),
            heatmap=heatmap,
        )

    def download(self, video: RemoteVideo) -> DownloadedMedia:
        target_dir = self.paths.downloads / video.video_id
        target_dir.mkdir(parents=True, exist_ok=True)
        out_template = str(target_dir / f"{video.video_id}.%(ext)s")
        options = self._quiet_options() | {
            "format": "bv*[height<=1080]+ba/b[height<=1080]/best",
            "merge_output_format": "mp4",
            "outtmpl": out_template,
            "noplaylist": True,
            "continuedl": True,
            "writeinfojson": True,
            "ffmpeg_location": str(Path(bundled_binary("ffmpeg")).parent),
        }
        download_url = f"https://www.youtube.com/watch?v={video.video_id}"
        try:
            with self._ydl(options) as ydl:
                info = ydl.extract_info(download_url, download=True)
        except Exception as exc:
            if "403" not in str(exc):
                raise YouTubeError(f"The source video could not be downloaded: {exc}") from exc
            for partial in (*target_dir.glob("*.part"), *target_dir.glob("*.ytdl")):
                partial.unlink(missing_ok=True)
            direct_options = options | {
                "format": (
                    "bv*[height<=1080][protocol^=http]+ba[protocol^=http]/"
                    "b[height<=1080][protocol^=http]/best[height<=1080]"
                ),
            }
            try:
                with self._ydl(direct_options) as ydl:
                    info = ydl.extract_info(download_url, download=True)
            except Exception as retry_exc:
                raise YouTubeError(
                    f"The source video could not be downloaded after a direct-stream retry: {retry_exc}"
                ) from retry_exc

        video_files = [path for path in target_dir.glob(f"{video.video_id}.*") if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
        if not video_files:
            raise YouTubeError("yt-dlp finished without a usable video file.")
        video_path = max(video_files, key=lambda path: path.stat().st_size)
        transcripts = self._english_transcripts(target_dir, video.video_id)
        if not transcripts:
            self._download_captions_best_effort(video, info, out_template)
            transcripts = self._english_transcripts(target_dir, video.video_id)
        info_path = next(iter(target_dir.glob(f"{video.video_id}*.info.json")), None)
        return DownloadedMedia(
            video_path=video_path,
            transcript_path=transcripts[0] if transcripts else None,
            info_path=info_path,
            duration_seconds=float(info.get("duration") or video.duration_seconds or 0),
        )

    def _download_captions_best_effort(
        self,
        video: RemoteVideo,
        info: dict[str, Any],
        out_template: str,
    ) -> None:
        languages = self._preferred_caption_languages(info)
        if not languages:
            return
        options = self._quiet_options() | {
            "skip_download": True,
            "outtmpl": out_template,
            "noplaylist": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": languages,
            "subtitlesformat": "vtt",
            "ffmpeg_location": str(Path(bundled_binary("ffmpeg")).parent),
        }
        try:
            with self._ydl(options) as ydl:
                ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video.video_id}", download=True
                )
        except Exception:
            # Captions improve local candidate selection, but a rate-limited or
            # unavailable subtitle track must never discard a valid source video.
            logger.warning("Captions for %s are unavailable; continuing with audio and visual signals.", video.video_id)

    @staticmethod
    def _preferred_caption_languages(info: dict[str, Any]) -> list[str]:
        available: list[str] = []
        for source in (info.get("subtitles") or {}, info.get("automatic_captions") or {}):
            for language in source:
                if language not in available:
                    available.append(str(language))
        for candidate in ("en", "en-orig"):
            if candidate in available:
                return [candidate]
        regional = next((language for language in available if language.startswith("en-")), None)
        if regional:
            return [regional]
        return []

    @staticmethod
    def _english_transcripts(target_dir: Path, video_id: str) -> list[Path]:
        pattern = re.compile(r"\.en(?:[-_][^.]*)*\.vtt$", re.IGNORECASE)
        return sorted(path for path in target_dir.glob(f"{video_id}*.vtt") if pattern.search(path.name))

    @staticmethod
    def load_heatmap(info_path: Path | None) -> list[dict[str, float]]:
        if not info_path or not info_path.exists():
            return []
        try:
            payload = json.loads(info_path.read_text(encoding="utf-8"))
            return [item for item in payload.get("heatmap") or [] if "start_time" in item and "value" in item]
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read cached YouTube metadata from %s", info_path.name)
            return []


def _published_at(entry: dict[str, Any]) -> str | None:
    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
    if timestamp:
        return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat()
    upload_date = str(entry.get("upload_date") or "")
    if len(upload_date) == 8 and upload_date.isdigit():
        try:
            return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return None
