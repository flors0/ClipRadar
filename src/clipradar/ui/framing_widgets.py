from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


REGION_SPECS = {
    "facecam": ("Facecam", QColor("#55d8ff"), QRectF(0.03, 0.05, 0.20, 0.27)),
    "gameplay": ("Main content", QColor("#caff00"), QRectF(0.08, 0.05, 0.84, 0.90)),
    "hud": ("HUD / Stats", QColor("#ffba55"), QRectF(0.76, 0.07, 0.20, 0.24)),
}


class RegionCanvas(QWidget):
    regions_changed = Signal()
    active_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RegionCanvas")
        self.setMinimumSize(470, 280)
        self.setMouseTracking(True)
        self.pixmap: QPixmap | None = None
        self.regions: dict[str, QRectF] = {}
        self.active = "facecam"
        self._drag_mode = ""
        self._drag_start = QPointF()
        self._original = QRectF()

    def set_frame(self, pixmap: QPixmap | None) -> None:
        self.pixmap = pixmap
        self.update()

    def set_regions(self, regions: dict[str, QRectF]) -> None:
        self.regions = {key: QRectF(value) for key, value in regions.items()}
        self.update()

    def select_region(self, key: str, *, create: bool = True) -> None:
        if key not in REGION_SPECS:
            return
        self.active = key
        if create and key not in self.regions:
            self.regions[key] = QRectF(REGION_SPECS[key][2])
            self.regions_changed.emit()
        self.active_changed.emit(key)
        self.update()

    def remove_active(self) -> None:
        if self.active in self.regions:
            del self.regions[self.active]
            self.regions_changed.emit()
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#050706"))
        target = self._image_rect()
        if self.pixmap and not self.pixmap.isNull():
            painter.drawPixmap(target.toRect(), self.pixmap)
        else:
            painter.setPen(QColor("#7f8b84"))
            painter.drawText(target, Qt.AlignmentFlag.AlignCenter, "No downloaded source video available")
        for key in ("gameplay", "hud", "facecam"):
            region = self.regions.get(key)
            if region is None:
                continue
            label, color, _ = REGION_SPECS[key]
            rect = self._to_canvas(region)
            fill = QColor(color)
            fill.setAlpha(34 if key != self.active else 54)
            painter.fillRect(rect, fill)
            painter.setPen(QPen(color, 3 if key == self.active else 2))
            painter.drawRoundedRect(rect, 4, 4)
            label_rect = QRectF(rect.left(), max(target.top(), rect.top() - 23), min(130, rect.width()), 23)
            painter.fillRect(label_rect, QColor(5, 8, 7, 220))
            painter.setPen(color)
            painter.drawText(label_rect.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, label)
            if key == self.active:
                handle = QRectF(rect.right() - 7, rect.bottom() - 7, 14, 14)
                painter.fillRect(handle, color)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.pixmap:
            return super().mousePressEvent(event)
        point = event.position()
        order = [self.active, "facecam", "hud", "gameplay"]
        for key in dict.fromkeys(order):
            region = self.regions.get(key)
            if region is None:
                continue
            rect = self._to_canvas(region)
            if QRectF(rect.right() - 15, rect.bottom() - 15, 30, 30).contains(point):
                self.active = key
                self._drag_mode = "resize"
                break
            if rect.contains(point):
                self.active = key
                self._drag_mode = "move"
                break
        else:
            return super().mousePressEvent(event)
        self._drag_start = point
        self._original = QRectF(self.regions[self.active])
        self.active_changed.emit(self.active)
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._drag_mode or self.active not in self.regions:
            return super().mouseMoveEvent(event)
        target = self._image_rect()
        if target.width() <= 0 or target.height() <= 0:
            return
        dx = (event.position().x() - self._drag_start.x()) / target.width()
        dy = (event.position().y() - self._drag_start.y()) / target.height()
        original = self._original
        if self._drag_mode == "move":
            x = min(1.0 - original.width(), max(0.0, original.x() + dx))
            y = min(1.0 - original.height(), max(0.0, original.y() + dy))
            updated = QRectF(x, y, original.width(), original.height())
        else:
            minimum = 0.03 if self.active != "gameplay" else 0.06
            width = min(1.0 - original.x(), max(minimum, original.width() + dx))
            height = min(1.0 - original.y(), max(minimum, original.height() + dy))
            updated = QRectF(original.x(), original.y(), width, height)
        self.regions[self.active] = updated
        self.regions_changed.emit()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_mode:
            self._drag_mode = ""
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _image_rect(self) -> QRectF:
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if not self.pixmap or self.pixmap.isNull():
            return area
        ratio = self.pixmap.width() / max(1, self.pixmap.height())
        if area.width() / max(1.0, area.height()) > ratio:
            width = area.height() * ratio
            return QRectF(area.center().x() - width / 2, area.top(), width, area.height())
        height = area.width() / ratio
        return QRectF(area.left(), area.center().y() - height / 2, area.width(), height)

    def _to_canvas(self, region: QRectF) -> QRectF:
        target = self._image_rect()
        return QRectF(
            target.left() + region.x() * target.width(),
            target.top() + region.y() * target.height(),
            region.width() * target.width(),
            region.height() * target.height(),
        )


class LiveFramingPreview(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FramingPreview")
        self.setMinimumSize(245, 436)
        self.setMaximumWidth(390)
        self.pixmap: QPixmap | None = None
        self.regions: dict[str, QRectF] = {}
        self.mode = "gaming_split"

    def update_plan(self, pixmap: QPixmap | None, regions: dict[str, QRectF], mode: str) -> None:
        self.pixmap = pixmap
        self.regions = {key: QRectF(value) for key, value in regions.items()}
        self.mode = mode
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#000000"))
        output = _fit_rect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 9 / 16)
        painter.setPen(QPen(QColor("#2a322d"), 1))
        painter.drawRoundedRect(output, 8, 8)
        if not self.pixmap or self.pixmap.isNull():
            painter.setPen(QColor("#7f8b84"))
            painter.drawText(output, Qt.AlignmentFlag.AlignCenter, "Live 9:16 preview")
            return
        if self.mode == "gaming_split" and "facecam" in self.regions:
            top = QRectF(output.left(), output.top(), output.width(), output.height() * 0.29)
            bottom = QRectF(output.left(), top.bottom(), output.width(), output.height() - top.height())
            context = QRectF(self.regions["facecam"])
            if "hud" in self.regions:
                context = context.united(self.regions["hud"])
            self._draw_crop(painter, top, context)
            gameplay = self.regions.get("gameplay", QRectF(0, 0, 1, 1))
            self._draw_crop(painter, bottom, gameplay)
        else:
            focus = self.regions.get("gameplay", QRectF(0.35, 0, 0.30, 1))
            self._draw_crop(painter, output, focus)

    def _draw_crop(self, painter: QPainter, target: QRectF, region: QRectF) -> None:
        source = _region_crop(self.pixmap.size(), region, target.width() / max(1.0, target.height()))
        painter.drawPixmap(target, self.pixmap, source)


def _fit_rect(area: QRectF, aspect: float) -> QRectF:
    if area.width() / max(1.0, area.height()) > aspect:
        width = area.height() * aspect
        return QRectF(area.center().x() - width / 2, area.top(), width, area.height())
    height = area.width() / aspect
    return QRectF(area.left(), area.center().y() - height / 2, area.width(), height)


def _region_crop(size, region: QRectF, target_aspect: float) -> QRectF:
    source_width = float(size.width())
    source_height = float(size.height())
    left = max(0.0, region.left()) * source_width
    top = max(0.0, region.top()) * source_height
    right = min(1.0, region.right()) * source_width
    bottom = min(1.0, region.bottom()) * source_height
    width = max(2.0, right - left)
    height = max(2.0, bottom - top)
    if width / height > target_aspect:
        height = width / target_aspect
    else:
        width = height * target_aspect
    width = min(source_width, width)
    height = min(source_height, height)
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    x = min(source_width - width, max(0.0, center_x - width / 2))
    y = min(source_height - height, max(0.0, center_y - height / 2))
    return QRectF(x, y, width, height)

