from __future__ import annotations

import math
import re
import subprocess
from array import array
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Iterable

from clipradar.analysis.transcript import Transcript
from clipradar.app.paths import bundled_binary
from clipradar.media.ffmpeg import MediaInfo, probe_media
from clipradar.models import ClipCandidate


REACTION_WORDS = re.compile(
    r"\b(?:wow|what|no way|oh my god|omg|crazy|insane|bro|alter|krass|niemals|was|scheiße|fuck|damn|haha|lol)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SignalPoint:
    second: int
    score: float
    energy: float
    transcript: float
    heatmap: float
    scene: float


class CandidateDetector:
    def detect(
        self,
        source_path: str | Path,
        transcript_path: str | Path | None,
        heatmap: Iterable[dict[str, float]],
        *,
        source_video_id: int,
        minimum_duration: int,
        target_duration: int,
        maximum_duration: int,
        max_candidates: int,
    ) -> list[ClipCandidate]:
        source_path = Path(source_path)
        media = probe_media(source_path)
        if media.duration < max(3, minimum_duration):
            raise ValueError("The video is shorter than the configured minimum clip duration.")
        transcript = Transcript.from_vtt(transcript_path)
        energy = self._audio_energy(source_path, media)
        scenes = self._scene_times(source_path, media.duration)
        points = self._combine(media.duration, energy, transcript, list(heatmap), scenes, target_duration)
        return self._select(
            points,
            transcript,
            media.duration,
            source_video_id,
            minimum_duration,
            target_duration,
            maximum_duration,
            max_candidates,
        )

    def _audio_energy(self, path: Path, media: MediaInfo) -> list[float]:
        seconds = max(1, math.ceil(media.duration))
        if not media.has_audio:
            return [0.0] * seconds
        sample_rate = 2000
        process = subprocess.Popen(
            [
                bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(path),
                "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        values: list[float] = []
        assert process.stdout is not None
        window_bytes = sample_rate * 2
        while True:
            chunk = process.stdout.read(window_bytes)
            if not chunk:
                break
            samples = array("h")
            samples.frombytes(chunk[: len(chunk) - len(chunk) % 2])
            if samples:
                rms = math.sqrt(sum(float(value) * value for value in samples) / len(samples)) / 32768.0
                peak = max(abs(value) for value in samples) / 32768.0
                values.append(rms * 0.72 + peak * 0.28)
        process.wait(timeout=max(60, media.duration * 0.5))
        if process.returncode != 0:
            return [0.0] * seconds
        values.extend([0.0] * max(0, seconds - len(values)))
        return values[:seconds]

    def _scene_times(self, path: Path, duration: float) -> list[float]:
        command = [
            bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "info", "-i", str(path),
            "-an", "-vf", "scale=256:-2,select='gt(scene,0.38)',showinfo", "-vsync", "vfr", "-f", "null", "-",
        ]
        try:
            result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=max(90, duration * 1.5))
        except (OSError, subprocess.TimeoutExpired):
            return []
        text = result.stderr.decode("utf-8", "replace")
        return [float(value) for value in re.findall(r"pts_time:([0-9.]+)", text)]

    def _combine(
        self,
        duration: float,
        energy_raw: list[float],
        transcript: Transcript,
        heatmap: list[dict[str, float]],
        scenes: list[float],
        target_duration: int,
    ) -> list[SignalPoint]:
        length = max(1, math.ceil(duration))
        energy = self._normalize(energy_raw, length)
        transcript_signal = [0.0] * length
        for cue in transcript.cues:
            text = cue.text
            words = len(text.split())
            reaction = min(1.0, 0.45 * len(REACTION_WORDS.findall(text)) + 0.2 * text.count("!"))
            density = min(1.0, words / max(1.0, cue.end - cue.start) / 4.0)
            score = min(1.0, reaction * 0.7 + density * 0.3)
            for second in range(max(0, int(cue.start)), min(length, math.ceil(cue.end))):
                transcript_signal[second] = max(transcript_signal[second], score)
        heatmap_signal = [0.0] * length
        for item in heatmap:
            start = max(0, int(float(item.get("start_time", 0))))
            end = min(length, math.ceil(float(item.get("end_time", start + 1))))
            value = max(0.0, min(1.0, float(item.get("value", 0))))
            for second in range(start, end):
                heatmap_signal[second] = max(heatmap_signal[second], value)
        scene_signal = [0.0] * length
        for moment in scenes:
            for second in range(max(0, int(moment) - 2), min(length, int(moment) + 3)):
                scene_signal[second] = max(scene_signal[second], 1.0 - abs(second - moment) / 4.0)

        radius = max(3, min(12, target_duration // 4))
        points: list[SignalPoint] = []
        for second in range(length):
            left, right = max(0, second - radius), min(length, second + radius + 1)
            local_energy = (
                energy[second] * 0.55
                + max(energy[left:right], default=0) * 0.20
                + fmean(energy[left:right] or [0]) * 0.25
            )
            local_transcript = transcript_signal[second] * 0.70 + max(transcript_signal[left:right], default=0) * 0.30
            local_heatmap = heatmap_signal[second] * 0.65 + max(heatmap_signal[left:right], default=0) * 0.35
            local_scene = scene_signal[second] * 0.75 + max(scene_signal[left:right], default=0) * 0.25
            if heatmap:
                score = local_energy * 0.28 + local_transcript * 0.24 + local_heatmap * 0.38 + local_scene * 0.10
            else:
                score = local_energy * 0.46 + local_transcript * 0.38 + local_scene * 0.16
            points.append(SignalPoint(second, score, local_energy, local_transcript, local_heatmap, local_scene))
        return points

    def _select(
        self,
        points: list[SignalPoint],
        transcript: Transcript,
        duration: float,
        source_video_id: int,
        minimum: int,
        target: int,
        maximum: int,
        limit: int,
    ) -> list[ClipCandidate]:
        ranked = sorted(points, key=lambda point: point.score, reverse=True)
        selected: list[ClipCandidate] = []
        min_separation = max(8, target * 0.65)
        for point in ranked:
            if any(abs(point.second - ((item.start_seconds + item.end_seconds) / 2)) < min_separation for item in selected):
                continue
            start = max(0.0, point.second - target * 0.42)
            end = min(duration, start + target)
            start = max(0.0, end - target)
            start, end = transcript.refine_bounds(start, end, duration, maximum)
            if end - start < minimum:
                needed = minimum - (end - start)
                start = max(0.0, start - needed * 0.5)
                end = min(duration, end + needed * 0.5)
            if end - start < minimum - 0.1:
                continue
            signals = {
                "audio": round(point.energy, 3),
                "transcript": round(point.transcript, 3),
                "heatmap": round(point.heatmap, 3),
                "scene": round(point.scene, 3),
                "transcript_excerpt": transcript.text_between(start, end)[:1200],
            }
            selected.append(ClipCandidate(
                id=None,
                source_video_id=source_video_id,
                start_seconds=round(start, 3),
                end_seconds=round(end, 3),
                local_score=round(point.score * 100, 2),
                signals=signals,
            ))
            if len(selected) >= max(1, limit):
                break
        return sorted(selected, key=lambda item: item.local_score, reverse=True)

    @staticmethod
    def _normalize(values: list[float], length: int) -> list[float]:
        values = (values + [0.0] * length)[:length]
        ordered = sorted(values)
        low = ordered[int(len(ordered) * 0.15)] if ordered else 0
        high = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 1
        spread = max(1e-6, high - low)
        return [max(0.0, min(1.0, (value - low) / spread)) for value in values]
