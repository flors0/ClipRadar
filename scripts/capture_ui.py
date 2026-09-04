from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from clipradar.app.paths import AppPaths, resource_path
from clipradar.app.services import AppServices
from clipradar.models import Channel
from clipradar.settings.secrets import MemorySecretStore
from clipradar.ui.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(resource_path("resources/theme.qss").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="clipradar-ui-") as temporary:
        services = AppServices.create(AppPaths.create(temporary), MemorySecretStore())
        services.repositories.channels.add(Channel(
            None, "UC_DEMO", "Creator Channel", "", "https://youtube.com/@creator",
            last_checked_at="2026-09-04T14:30:00+00:00", last_video_id="demo",
        ))
        services.repositories.activity.add("Monitoring started")
        services.repositories.activity.add("Added channel Creator Channel", "success")
        window = MainWindow(services, start_background=False)
        window.resize(1440, 900)
        window.show()
        app.processEvents()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(args.output))
        window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
