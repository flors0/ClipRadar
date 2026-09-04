from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    database: Path
    downloads: Path
    candidates: Path
    output: Path
    cache: Path

    @classmethod
    def create(cls, override: str | Path | None = None) -> "AppPaths":
        root = Path(override).expanduser().resolve() if override else user_data_path("ClipRadar", "ClipRadar", roaming=False)
        paths = cls(
            root=root,
            database=root / "clipradar.db",
            downloads=root / "downloads",
            candidates=root / "candidate_previews",
            output=root / "output",
            cache=root / "cache",
        )
        for directory in (paths.root, paths.downloads, paths.candidates, paths.output, paths.cache):
            directory.mkdir(parents=True, exist_ok=True)
        return paths


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / relative


def bundled_binary(name: str) -> str:
    suffix = ".exe" if os.name == "nt" else ""
    candidates = [
        resource_path(f"bin/{name}{suffix}"),
        Path(sys.executable).resolve().parent / "bin" / f"{name}{suffix}",
        Path(sys.executable).resolve().parent / f"{name}{suffix}",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"{name} was not found. Reinstall ClipRadar or configure FFmpeg in Settings → Storage.")

