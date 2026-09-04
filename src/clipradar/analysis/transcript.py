from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path


TIMESTAMP = re.compile(r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{3})")
TAG = re.compile(r"<[^>]+>")


def parse_timestamp(value: str) -> float:
    match = TIMESTAMP.search(value)
    if not match:
        raise ValueError(value)
    return (
        int(match["h"]) * 3600
        + int(match["m"]) * 60
        + int(match["s"])
        + int(match["ms"]) / 1000
    )


@dataclass(frozen=True, slots=True)
class TranscriptCue:
    start: float
    end: float
    text: str


class Transcript:
    def __init__(self, cues: list[TranscriptCue] | None = None):
        self.cues = cues or []

    @classmethod
    def from_vtt(cls, path: str | Path | None) -> "Transcript":
        if not path or not Path(path).exists():
            return cls()
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        cues: list[TranscriptCue] = []
        index = 0
        last_signature: tuple[float, float, str] | None = None
        while index < len(lines):
            line = lines[index].strip()
            if "-->" not in line:
                index += 1
                continue
            left, right = line.split("-->", 1)
            try:
                start, end = parse_timestamp(left), parse_timestamp(right)
            except ValueError:
                index += 1
                continue
            index += 1
            text_lines: list[str] = []
            while index < len(lines) and lines[index].strip():
                cleaned = html.unescape(TAG.sub("", lines[index])).strip()
                if cleaned:
                    text_lines.append(cleaned)
                index += 1
            text = re.sub(r"\s+", " ", " ".join(text_lines)).strip()
            signature = (round(start, 2), round(end, 2), text)
            if text and signature != last_signature:
                cues.append(TranscriptCue(start, end, text))
                last_signature = signature
        return cls(cues)

    def text_between(self, start: float, end: float) -> str:
        return " ".join(cue.text for cue in self.cues if cue.end >= start and cue.start <= end)

    def refine_bounds(self, start: float, end: float, duration: float, maximum: float) -> tuple[float, float]:
        overlapping = [cue for cue in self.cues if cue.end >= start - 3 and cue.start <= end + 3]
        if not overlapping:
            return max(0, start), min(duration, end)
        earlier = [cue.start for cue in overlapping if start - 4 <= cue.start <= start + 2]
        later = [cue.end for cue in overlapping if end - 2 <= cue.end <= end + 5]
        refined_start = min(earlier, key=lambda value: abs(value - start)) if earlier else start
        refined_end = min(later, key=lambda value: abs(value - end)) if later else end
        if refined_end - refined_start > maximum:
            refined_end = refined_start + maximum
        return max(0, refined_start), min(duration, refined_end)

