from __future__ import annotations

import tempfile
import os
import sys
from importlib import import_module
from pathlib import Path

from clipradar.app.paths import AppPaths, bundled_binary, resource_path
from clipradar.app.services import AppServices
from clipradar.settings.models import AISettings
from clipradar.settings.secrets import MemorySecretStore


def run_self_test() -> int:
    modules = [
        "cv2",
        "google.genai",
        "keyring",
        "yt_dlp",
    ]
    if os.name == "nt" or getattr(sys, "frozen", False):
        modules.extend(("PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"))
    for module in modules:
        import_module(module)
    with tempfile.TemporaryDirectory(prefix="clipradar-selftest-") as temporary:
        paths = AppPaths.create(temporary)
        secrets = MemorySecretStore("self-test-only")
        services = AppServices.create(paths, secrets)
        services.settings.save_ai(AISettings(model="gemini-3.5-flash-lite"))
        assert services.settings.ai().model == "gemini-3.5-flash-lite"
        assert services.settings.secrets.get_gemini_key() == "self-test-only"
        with services.database.connection() as connection:
            tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"channels", "source_videos", "analysis_jobs", "clip_candidates", "rendered_clips", "settings", "ai_usage"}
        assert required <= tables
        assert Path(bundled_binary("ffmpeg")).exists()
        assert Path(bundled_binary("ffprobe")).exists()
        assert resource_path("resources/theme.qss").exists()
    print("ClipRadar self-test passed")
    return 0
