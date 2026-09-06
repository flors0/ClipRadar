from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from clipradar.app.paths import AppPaths, bundled_binary
from clipradar.media.ffmpeg import MediaError, probe_media, run_process
from clipradar.models import ClipCandidate, ReframeMode, RenderedClip, SourceVideo
from clipradar.rendering.captions import create_ass_captions
from clipradar.rendering.reframe import detect_face_focus, detect_face_region
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
        return self._render_window(
            source,
            candidate,
            settings,
            candidate.render_start,
            candidate.render_end,
            output_override=output_override,
        )

    def render_review_buffer(
        self,
        source: SourceVideo,
        candidate: ClipCandidate,
        settings: ClipSettings,
        *,
        padding_seconds: float = 30.0,
        output_override: str = "",
    ) -> RenderedClip:
        if not source.local_path or not Path(source.local_path).exists():
            raise MediaError("The downloaded source video is missing.")
        source_duration = float(source.duration_seconds or probe_media(source.local_path).duration)
        buffer_start = max(0.0, candidate.render_start - max(0.0, padding_seconds))
        buffer_end = min(source_duration, candidate.render_end + max(0.0, padding_seconds))
        return self._render_window(
            source,
            candidate,
            settings,
            buffer_start,
            buffer_end,
            output_override=output_override,
            buffer_start_seconds=buffer_start,
            buffer_end_seconds=buffer_end,
        )

    def _render_window(
        self,
        source: SourceVideo,
        candidate: ClipCandidate,
        settings: ClipSettings,
        start: float,
        end: float,
        *,
        output_override: str = "",
        buffer_start_seconds: float | None = None,
        buffer_end_seconds: float | None = None,
    ) -> RenderedClip:
        if not source.local_path or not Path(source.local_path).exists():
            raise MediaError("The downloaded source video is missing.")
        duration = end - start
        if duration <= 0:
            raise MediaError("Candidate boundaries are invalid.")
        media = probe_media(source.local_path)
        output_root = Path(output_override).expanduser() if output_override else self.paths.output
        output_dir = output_root / _safe_name(source.youtube_video_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = "review" if buffer_start_seconds is not None else "clip"
        output = _versioned_path(output_dir / f"{prefix}_{candidate.id}_{round(start * 1000)}.mp4")
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
        video_filter = self._video_filter(media.width, media.height, candidate, settings, source.local_path)
        if ass_path and not video_filter.complex:
            video_filter.value += f",subtitles=filename='{_escape_filter_path(ass_path)}'"
        command = [
            bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", source.local_path, "-t", f"{duration:.3f}",
        ]
        if video_filter.complex:
            output_label = video_filter.output_label
            complex_value = video_filter.value
            if ass_path:
                complex_value += f";[{output_label}]subtitles=filename='{_escape_filter_path(ass_path)}'[captioned]"
                output_label = "captioned"
            command += ["-filter_complex", complex_value, "-map", f"[{output_label}]", "-map", "0:a?"]
        else:
            command += ["-vf", video_filter.value]
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
            buffer_start_seconds=buffer_start_seconds,
            buffer_end_seconds=buffer_end_seconds,
        )

    @staticmethod
    def _video_filter(
        source_width: int,
        source_height: int,
        candidate: ClipCandidate,
        settings: ClipSettings,
        source_path: str,
    ) -> "VideoFilterPlan":
        if settings.output_format != "Vertical 9:16":
            out_w, out_h = 1920, 1080
            return VideoFilterPlan(
                f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
            )
        out_w, out_h = _even(settings.render_width), _even(settings.render_height)
        try:
            mode = ReframeMode(candidate.reframe_mode)
        except ValueError:
            mode = ReframeMode.AUTO

        if mode == ReframeMode.GAMING_SPLIT:
            facecam = _candidate_facecam(candidate) or detect_face_region(
                source_path, candidate.render_start, candidate.render_end
            )
            if facecam:
                return _gaming_split_filter(
                    source_width, source_height, out_w, out_h, candidate, facecam
                )
        if mode == ReframeMode.CONTAIN:
            return _contain_filter(out_w, out_h)

        if mode == ReframeMode.CENTER:
            focus_x, focus_y = 0.5, 0.5
        elif mode == ReframeMode.AUTO:
            focus_x = detect_face_focus(
                source_path,
                candidate.render_start,
                candidate.render_end,
                source_width,
            )
            focus_y = 0.5
        else:
            focus_x = _unit(candidate.focus_x)
            focus_y = _unit(candidate.focus_y)
        width, height, x, y = _crop_for_aspect(
            source_width,
            source_height,
            out_w / out_h,
            focus_x,
            focus_y,
        )
        return VideoFilterPlan(
            f"crop={width}:{height}:{x}:{y},scale={out_w}:{out_h}:flags=lanczos,setsar=1"
        )


@dataclass(slots=True)
class VideoFilterPlan:
    value: str
    complex: bool = False
    output_label: str = "video_out"


def _crop_for_aspect(
    source_width: int,
    source_height: int,
    target_aspect: float,
    focus_x: float,
    focus_y: float,
) -> tuple[int, int, int, int]:
    source_width = _even(source_width)
    source_height = _even(source_height)
    if source_width / max(1, source_height) > target_aspect:
        crop_height = source_height
        crop_width = min(source_width, _even(crop_height * target_aspect))
        x = _even_position(max(0, min(source_width - crop_width, focus_x * source_width - crop_width / 2)))
        return crop_width, crop_height, x, 0
    crop_width = source_width
    crop_height = min(source_height, _even(crop_width / max(0.01, target_aspect)))
    y = _even_position(max(0, min(source_height - crop_height, focus_y * source_height - crop_height / 2)))
    return crop_width, crop_height, 0, y


def _gaming_split_filter(
    source_width: int,
    source_height: int,
    out_w: int,
    out_h: int,
    candidate: ClipCandidate,
    facecam: tuple[float, float, float, float],
) -> VideoFilterPlan:
    # Keep the composition stable across creators: the context strip is always
    # 29% and gameplay receives the remaining 71%. Gemini locates source
    # regions; it never gets to invent output proportions per clip.
    face_height = _even(out_h * 0.29)
    game_height = out_h - face_height
    gameplay = _candidate_region(
        candidate.gameplay_x,
        candidate.gameplay_y,
        candidate.gameplay_width,
        candidate.gameplay_height,
        minimum_size=0.04,
    )
    game_focus_x = gameplay[0] + gameplay[2] / 2 if gameplay else _unit(candidate.focus_x)
    game_focus_y = gameplay[1] + gameplay[3] / 2 if gameplay else _unit(candidate.focus_y)
    game_w, game_h, game_x, game_y = _crop_for_aspect(
        source_width,
        source_height,
        out_w / game_height,
        game_focus_x,
        game_focus_y,
    )
    hud = _candidate_region(
        candidate.hud_x,
        candidate.hud_y,
        candidate.hud_width,
        candidate.hud_height,
        minimum_size=0.02,
    )
    context_region = _union_regions(facecam, hud) if hud else facecam
    face_x, face_y, face_w, face_h = _region_crop_for_aspect(
        source_width,
        source_height,
        context_region,
        out_w / face_height,
    )
    value = (
        "[0:v]split=2[game_source][face_source];"
        f"[game_source]crop={game_w}:{game_h}:{game_x}:{game_y},"
        f"scale={out_w}:{game_height}:flags=lanczos[game];"
        f"[face_source]crop={face_w}:{face_h}:{face_x}:{face_y},"
        f"scale={out_w}:{face_height}:flags=lanczos[face];"
        "[face][game]vstack=inputs=2,setsar=1[video_out]"
    )
    return VideoFilterPlan(value, complex=True)


def _contain_filter(out_w: int, out_h: int) -> VideoFilterPlan:
    value = (
        "[0:v]split=2[background_source][foreground_source];"
        f"[background_source]scale={out_w}:{out_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={out_w}:{out_h},gblur=sigma=28[background];"
        f"[foreground_source]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos[foreground];"
        "[background][foreground]overlay=(W-w)/2:(H-h)/2,setsar=1[video_out]"
    )
    return VideoFilterPlan(value, complex=True)


def _candidate_facecam(candidate: ClipCandidate) -> tuple[float, float, float, float] | None:
    return _candidate_region(
        candidate.facecam_x,
        candidate.facecam_y,
        candidate.facecam_width,
        candidate.facecam_height,
        minimum_size=0.06,
    )


def _candidate_region(
    x: float | None,
    y: float | None,
    width: float | None,
    height: float | None,
    *,
    minimum_size: float,
) -> tuple[float, float, float, float] | None:
    values = (x, y, width, height)
    if any(value is None for value in values):
        return None
    x, y, width, height = (float(value) for value in values)
    if (
        width >= minimum_size
        and height >= minimum_size
        and x >= 0
        and y >= 0
        and x + width <= 1.01
        and y + height <= 1.01
    ):
        return x, y, width, height
    return None


def _union_regions(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    left = min(first[0], second[0])
    top = min(first[1], second[1])
    right = max(first[0] + first[2], second[0] + second[2])
    bottom = max(first[1] + first[3], second[1] + second[3])
    return left, top, right - left, bottom - top


def _region_crop_for_aspect(
    source_width: int,
    source_height: int,
    region: tuple[float, float, float, float],
    target_aspect: float,
) -> tuple[int, int, int, int]:
    x, y, width, height = region
    pad_x = max(0.012, width * 0.10)
    pad_y = max(0.012, height * 0.10)
    left = max(0.0, x - pad_x) * source_width
    top = max(0.0, y - pad_y) * source_height
    right = min(1.0, x + width + pad_x) * source_width
    bottom = min(1.0, y + height + pad_y) * source_height
    region_w = max(2.0, right - left)
    region_h = max(2.0, bottom - top)
    if region_w / region_h > target_aspect:
        crop_w = region_w
        crop_h = crop_w / target_aspect
    else:
        crop_h = region_h
        crop_w = crop_h * target_aspect
    if crop_w > source_width or crop_h > source_height:
        center_x = (left + right) / 2 / max(1, source_width)
        center_y = (top + bottom) / 2 / max(1, source_height)
        width, height, crop_x, crop_y = _crop_for_aspect(
            source_width, source_height, target_aspect, center_x, center_y
        )
        return crop_x, crop_y, width, height
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    crop_x = max(0.0, min(source_width - crop_w, center_x - crop_w / 2))
    crop_y = max(0.0, min(source_height - crop_h, center_y - crop_h / 2))
    return _even_position(crop_x), _even_position(crop_y), _even(crop_w), _even(crop_h)


def _unit(value: float | int | None) -> float:
    return max(0.0, min(1.0, float(value if value is not None else 0.5)))


def _even_position(value: float | int) -> int:
    return max(0, round(value) // 2 * 2)


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
