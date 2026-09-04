from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ClipRadar")
    parser.add_argument("--data-dir", type=Path, help="Override application data directory (testing only).")
    parser.add_argument("--self-test", action="store_true", help="Run packaged dependency and database checks.")
    parser.add_argument("--no-background", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        from clipradar.app.self_test import run_self_test

        return run_self_test()

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from clipradar.app.logging_setup import configure_logging
    from clipradar.app.paths import AppPaths, resource_path
    from clipradar.app.services import AppServices
    from clipradar.ui.main_window import MainWindow

    QCoreApplication.setApplicationName("ClipRadar")
    QCoreApplication.setOrganizationName("ClipRadar")
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    theme = resource_path("resources/theme.qss")
    if theme.exists():
        app.setStyleSheet(theme.read_text(encoding="utf-8"))
    paths = AppPaths.create(args.data_dir)
    configure_logging(paths.root)
    try:
        services = AppServices.create(paths)
        window = MainWindow(services, start_background=not args.no_background)
        window.show()
        return app.exec()
    except Exception as exc:
        QMessageBox.critical(None, "ClipRadar could not start", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

