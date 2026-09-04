from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QVBoxLayout, QWidget


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def card_layout(parent: QWidget | None = None, *, object_name: str = "Card") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(parent)
    frame.setObjectName(object_name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    return frame, layout


def title_label(text: str, object_name: str = "SectionTitle") -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    return label


def muted_label(text: str, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(wrap)
    return label


def status_pill(text: str, color: str = "#caff00") -> QLabel:
    label = QLabel(f"  ●  {text}  ")
    label.setStyleSheet(
        f"color:{color}; background:#111612; border:1px solid #2a322d; border-radius:11px; padding:3px 7px;"
    )
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def format_time(seconds: float | int | None) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def format_timestamp(value: str | None) -> str:
    if not value:
        return "Not checked yet"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%d %b · %H:%M")
    except ValueError:
        return value


def row_widget(left: QWidget, right: QWidget) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(left)
    layout.addStretch(1)
    layout.addWidget(right)
    return container

