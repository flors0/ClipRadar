from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from clipradar.app.paths import bundled_binary
from clipradar.models import OperationCancelled


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


def run_process(
    arguments: Sequence[str],
    *,
    timeout: float | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        process = subprocess.Popen(
            list(arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=_startupinfo(),
        )
    except OSError as exc:
        raise MediaError(f"Media command failed to start: {exc}") from exc
    started = time.monotonic()
    stdout = b""
    stderr = b""
    while True:
        if cancel_requested and cancel_requested():
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise OperationCancelled("Analysis cancelled by user.")
        if timeout is not None and time.monotonic() - started > timeout:
            process.kill()
            process.communicate()
            raise MediaError(f"Media command timed out after {timeout:.0f} seconds.")
        try:
            stdout, stderr = process.communicate(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            time.sleep(0.01)
    result = subprocess.CompletedProcess(list(arguments), process.returncode, stdout, stderr)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip().splitlines()
        detail = message[-1] if message else f"exit code {result.returncode}"
        raise MediaError(f"FFmpeg failed: {detail}")
    return result


def probe_media(
    path: str | Path,
    cancel_requested: Callable[[], bool] | None = None,
) -> MediaInfo:
    result = run_process([
        bundled_binary("ffprobe"), "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ], timeout=30, cancel_requested=cancel_requested)
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
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = max(1.0, end - start)
    try:
        run_process([
            bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{max(0, start):.3f}", "-i", str(source), "-t", f"{duration:.3f}",
            "-vf", "scale='min(640,iw)':-2:flags=lanczos",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "31",
            "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(output),
        ], timeout=max(120, duration * 5), cancel_requested=cancel_requested)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return output


def create_framing_preview(
    source: str | Path,
    output: str | Path,
    start: float,
    end: float,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    """Create a small visual-only preview for comparing an existing vertical render."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = max(1.0, end - start)
    try:
        run_process([
            bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{max(0, start):.3f}", "-i", str(source), "-t", f"{duration:.3f}",
            "-vf", "fps=4,scale='min(480,iw)':-2:flags=lanczos",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ], timeout=max(120, duration * 5), cancel_requested=cancel_requested)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return output
