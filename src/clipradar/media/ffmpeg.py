from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from clipradar.app.paths import bundled_binary


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def _startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return info


def run_process(arguments: Sequence[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            list(arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            startupinfo=_startupinfo(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"Media command failed to start: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip().splitlines()
        detail = message[-1] if message else f"exit code {result.returncode}"
        raise MediaError(f"FFmpeg failed: {detail}")
    return result


def probe_media(path: str | Path) -> MediaInfo:
    result = run_process([
        bundled_binary("ffprobe"), "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ], timeout=30)
    try:
        payload = json.loads(result.stdout)
        video = next(stream for stream in payload["streams"] if stream.get("codec_type") == "video")
        frame_rate = video.get("avg_frame_rate") or "0/1"
        numerator, denominator = (float(value) for value in frame_rate.split("/"))
        duration = float(video.get("duration") or payload.get("format", {}).get("duration") or 0)
        return MediaInfo(
            duration=duration,
            width=int(video.get("width") or 0),
            height=int(video.get("height") or 0),
            fps=numerator / denominator if denominator else 0,
            has_audio=any(stream.get("codec_type") == "audio" for stream in payload["streams"]),
        )
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaError("FFprobe returned incomplete media metadata.") from exc


def create_candidate_preview(
    source: str | Path,
    output: str | Path,
    start: float,
    end: float,
) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = max(1.0, end - start)
    run_process([
        bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0, start):.3f}", "-i", str(source), "-t", f"{duration:.3f}",
        "-vf", "scale='min(640,iw)':-2:flags=lanczos",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "31",
        "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(output),
    ], timeout=max(120, duration * 5))
    return output


def create_framing_preview(
    source: str | Path,
    output: str | Path,
    start: float,
    end: float,
) -> Path:
    """Create a small visual-only preview for comparing an existing vertical render."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = max(1.0, end - start)
    run_process([
        bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0, start):.3f}", "-i", str(source), "-t", f"{duration:.3f}",
        "-vf", "fps=4,scale='min(480,iw)':-2:flags=lanczos",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ], timeout=max(120, duration * 5))
    return output
