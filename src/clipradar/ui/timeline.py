from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider, QWidget


class ClickableSlider(QSlider):
    """A normal Qt slider whose empty track can also be clicked to seek."""

    seek_requested = Signal(int)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self,
        )
        if handle.contains(event.position().toPoint()):
            super().mousePressEvent(event)
            return
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderGroove,
            self,
        )
        span = max(1, groove.width())
        position = max(0, min(span, round(event.position().x() - groove.left())))
        value = QStyle.sliderValueFromPosition(
            self.minimum(),
            self.maximum(),
            position,
            span,
            option.upsideDown,
        )
        self.setValue(value)
        self.seek_requested.emit(value)
        event.accept()


class TrimRangeSlider(QWidget):
    """Two-handle review range with visible editable padding on both sides."""

    range_changing = Signal(int, int)
    range_changed = Signal(int, int)
    preview_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 0
        self._start = 0
        self._end = 0
        self._active: str | None = None
        self.setMinimumHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Drag the lime handles to adjust the final clip. The grey areas are the 30-second buffer.")

    def set_range(self, minimum: int, maximum: int) -> None:
        self._minimum = max(0, int(minimum))
        self._maximum = max(self._minimum, int(maximum))
        self.set_selection(self._minimum, self._maximum)

    def set_selection(self, start: int, end: int) -> None:
        self._start = max(self._minimum, min(int(start), self._maximum))
        self._end = max(self._start, min(int(end), self._maximum))
        self.update()

    def selection(self) -> tuple[int, int]:
        return self._start, self._end

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(11, self.height() / 2 - 3, max(2, self.width() - 22), 6)
        path = QPainterPath()
        path.addRoundedRect(track, 3, 3)
        painter.fillPath(path, QColor("#252c28"))
        start_x = self._x_for_value(self._start)
        end_x = self._x_for_value(self._end)
        selected = QRectF(start_x, track.top(), max(2, end_x - start_x), track.height())
        selected_path = QPainterPath()
        selected_path.addRoundedRect(selected, 3, 3)
        painter.fillPath(selected_path, QColor("#9fc900"))
        painter.setPen(QPen(QColor("#caff00"), 2))
        for x in (start_x, end_x):
            painter.drawLine(QPointF(x, 6), QPointF(x, self.height() - 6))
            painter.setBrush(QColor("#caff00"))
            painter.drawEllipse(QPointF(x, self.height() / 2), 4.5, 4.5)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._maximum <= self._minimum:
            return
        start_distance = abs(event.position().x() - self._x_for_value(self._start))
        end_distance = abs(event.position().x() - self._x_for_value(self._end))
        self._active = "start" if start_distance <= end_distance else "end"
        self._move_active(event.position().x())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._active:
            self._move_active(event.position().x())
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._active:
            self._move_active(event.position().x())
            self._active = None
            self.range_changed.emit(self._start, self._end)
            event.accept()

    def _move_active(self, x: float) -> None:
        value = self._value_for_x(x)
        minimum_span = min(1000, max(0, self._maximum - self._minimum))
        if self._active == "start":
            self._start = max(self._minimum, min(value, self._end - minimum_span))
            preview = self._start
        else:
            self._end = min(self._maximum, max(value, self._start + minimum_span))
            preview = self._end
        self.update()
        self.range_changing.emit(self._start, self._end)
        self.preview_requested.emit(preview)

    def _x_for_value(self, value: int) -> float:
        span = max(1, self._maximum - self._minimum)
        return 11 + (self.width() - 22) * (value - self._minimum) / span

    def _value_for_x(self, x: float) -> int:
        width = max(1, self.width() - 22)
        ratio = max(0.0, min(1.0, (x - 11) / width))
        return round(self._minimum + ratio * (self._maximum - self._minimum))
