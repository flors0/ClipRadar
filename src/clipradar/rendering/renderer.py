from __future__ import annotations

import re
from pathlib import Path

from clipradar.app.paths import AppPaths, bundled_binary
from clipradar.media.ffmpeg import MediaError, probe_media, run_process
from clipradar.models import ClipCandidate, RenderedClip, SourceVideo
from clipradar.rendering.captions import create_ass_captions
from clipradar.rendering.reframe import detect_face_focus
from clipradar.settings.models import ClipSettings


class ClipRenderer:
    def __init__(self, paths: AppPaths):
        self.paths = paths

    def render(
        self,
        source: SourceVideo,
        candidate: ClipCandidate,
        settings: ClipSettings,
        *,
        output_override: str = "",
    ) -> RenderedClip:
        if not source.local_path or not Path(source.local_path).exists():
            raise MediaError("The downloaded source video is missing.")
        start, end = candidate.render_start, candidate.render_end
        duration = end - start
        if duration <= 0:
            raise MediaError("Candidate boundaries are invalid.")
        media = probe_media(source.local_path)
        output_root = Path(output_override).expanduser() if output_override else self.paths.output
        output_dir = output_root / _safe_name(source.youtube_video_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        output = _versioned_path(output_dir / f"clip_{candidate.id}_{round(start * 1000)}.mp4")
        ass_path: Path | None = None
        if settings.captions_enabled:
            ass_path = create_ass_captions(
                source.transcript_path,
                output.with_suffix(".ass"),
                start,
                end,
                settings.render_width,
                settings.render_height,
                settings.word_highlighting,
            )
        video_filter = self._video_filter(media.width, media.height, start, end, settings, source.local_path)
        if ass_path:
            video_filter += f",subtitles=filename='{_escape_filter_path(ass_path)}'"
        command = [
            bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", source.local_path, "-t", f"{duration:.3f}",
            "-vf", video_filter,
        ]
        if media.has_audio:
            audio_filter = "loudnorm=I=-14:LRA=11:TP=-1.5" if settings.audio_normalization else "anull"
            command += ["-af", audio_filter, "-c:a", "aac", "-b:a", "160k"]
        else:
            command += ["-an"]
        command += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(output),
        ]
        run_process(command, timeout=max(180, duration * 12))
        actual = probe_media(output)
        return RenderedClip(
            id=None,
            candidate_id=int(candidate.id),
            source_video_id=int(source.id),
            file_path=str(output),
            duration_seconds=actual.duration,
            format=settings.output_format,
        )

    @staticmethod
    def _video_filter(
        source_width: int,
        source_height: int,
        start: float,
        end: float,
        settings: ClipSettings,
        source_path: str,
    ) -> str:
        if settings.output_format != "Vertical 9:16":
            out_w, out_h = 1920, 1080
            return f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        out_w, out_h = _even(settings.render_width), _even(settings.render_height)
        source_aspect = source_width / max(1, source_height)
        target_aspect = out_w / out_h
        if source_aspect > target_aspect:
            scaled_width = _even(source_width * out_h / max(1, source_height))
            focus = detect_face_focus(source_path, start, end, source_width)
            crop_x = round(max(0, min(scaled_width - out_w, focus * scaled_width - out_w / 2)))
            return f"scale={scaled_width}:{out_h}:flags=lanczos,crop={out_w}:{out_h}:{crop_x}:0,setsar=1"
        scaled_height = _even(source_height * out_w / max(1, source_width))
        if scaled_height >= out_h:
            y = max(0, round((scaled_height - out_h) / 2))
            return f"scale={out_w}:{scaled_height}:flags=lanczos,crop={out_w}:{out_h}:0:{y},setsar=1"
        return f"scale={out_w}:{scaled_height}:flags=lanczos,pad={out_w}:{out_h}:0:(oh-ih)/2:color=black,setsar=1"


def _even(value: float | int) -> int:
    integer = max(2, round(value))
    return integer if integer % 2 == 0 else integer - 1


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "video"


def _versioned_path(path: Path) -> Path:
    if not path.exists():
        return path
    for version in range(2, 1000):
        candidate = path.with_stem(f"{path.stem}_v{version}")
        if not candidate.exists():
            return candidate
    raise MediaError("Too many render versions exist for this candidate.")


def _escape_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    return value.replace(":", r"\:").replace("'", r"\'")
