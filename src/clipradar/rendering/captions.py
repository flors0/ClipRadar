from __future__ import annotations

import math
import re
import textwrap
from pathlib import Path

from clipradar.analysis.transcript import Transcript, TranscriptCue


def create_ass_captions(
    transcript_path: str | Path | None,
    output_path: str | Path,
    clip_start: float,
    clip_end: float,
    width: int,
    height: int,
    word_highlighting: bool = False,
) -> Path | None:
    transcript = Transcript.from_vtt(transcript_path)
    cues = [cue for cue in transcript.cues if cue.end > clip_start and cue.start < clip_end]
    if not cues:
        return None
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    font_size = max(42, round(height * 0.047))
    margin_v = max(160, round(height * 0.17))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H0000FFCC,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,5,1,2,70,70,{margin_v},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    dialogues = [
        _dialogue(cue, clip_start, clip_end, word_highlighting)
        for cue in _coalesce(cues, max_words=8)
    ]
    output_path.write_text(header + "\n".join(dialogues) + "\n", encoding="utf-8-sig")
    return output_path


def _coalesce(cues: list[TranscriptCue], max_words: int) -> list[TranscriptCue]:
    result: list[TranscriptCue] = []
    for cue in cues:
        cleaned = re.sub(r"\s+", " ", cue.text).strip()
        if not cleaned:
            continue
        words = cleaned.split()
        if len(words) <= max_words:
            result.append(TranscriptCue(cue.start, cue.end, cleaned))
            continue
        duration = max(0.1, cue.end - cue.start)
        chunks = [words[index:index + max_words] for index in range(0, len(words), max_words)]
        for index, chunk in enumerate(chunks):
            start = cue.start + duration * index / len(chunks)
            end = cue.start + duration * (index + 1) / len(chunks)
            result.append(TranscriptCue(start, end, " ".join(chunk)))
    return result


def _dialogue(cue: TranscriptCue, clip_start: float, clip_end: float, highlight: bool) -> str:
    start = max(0.0, cue.start - clip_start)
    end = min(clip_end - clip_start, cue.end - clip_start)
    text = _escape_ass(cue.text)
    if highlight:
        words = text.split()
        centiseconds = max(1, math.floor((end - start) * 100 / max(1, len(words))))
        text = " ".join(f"{{\\k{centiseconds}}}{word}" for word in words)
    else:
        wrapped = textwrap.wrap(text, width=27, break_long_words=False, max_lines=2, placeholder="…")
        text = r"\N".join(wrapped)
    return f"Dialogue: 0,{_ass_time(start)},{_ass_time(max(start + 0.15, end))},Default,,0,0,0,,{text}"


def _escape_ass(value: str) -> str:
    return value.replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remaining = seconds % 60
    return f"{hours}:{minutes:02d}:{remaining:05.2f}"

