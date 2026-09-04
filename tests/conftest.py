from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clipradar.app.paths import AppPaths, bundled_binary
from clipradar.app.services import AppServices
from clipradar.settings.secrets import MemorySecretStore


@pytest.fixture()
def services(tmp_path: Path) -> AppServices:
    return AppServices.create(AppPaths.create(tmp_path / "data"), MemorySecretStore("test-key"))


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("media")
    output = root / "source.mp4"
    command = [
        bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=18",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=18",
        "-filter:a", "volume='if(between(t,7,11),1,0.07)':eval=frame",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(output),
    ]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output


@pytest.fixture()
def transcript_file(tmp_path: Path) -> Path:
    path = tmp_path / "captions.vtt"
    path.write_text(
        """WEBVTT

00:00:05.000 --> 00:00:08.000
Wait, what is happening here?

00:00:08.000 --> 00:00:11.500
No way! That was absolutely crazy!

00:00:11.500 --> 00:00:14.000
I cannot believe that worked.
""",
        encoding="utf-8",
    )
    return path
