from __future__ import annotations

from pathlib import Path
from statistics import median


def detect_face_focus(source: str | Path, start: float, end: float, source_width: int) -> float:
    """Returns a stable normalized horizontal focus. Center is the safe fallback."""
    region = detect_face_region(source, start, end)
    if region:
        x, _y, width, _height = region
        return max(0.12, min(0.88, x + width / 2))
    return 0.5 if source_width else 0.5


def detect_face_region(source: str | Path, start: float, end: float) -> tuple[float, float, float, float] | None:
    """Returns a stable normalized face box sampled across the clip."""
    try:
        import cv2

        capture = cv2.VideoCapture(str(source))
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        boxes: list[tuple[float, float, float, float]] = []
        samples = 12
        for index in range(samples):
            moment = start + (end - start) * (index + 0.5) / samples
            capture.set(cv2.CAP_PROP_POS_MSEC, moment * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(36, 36))
            if len(faces):
                x, y, width, height = max(faces, key=lambda box: box[2] * box[3])
                boxes.append((
                    x / max(1, frame.shape[1]),
                    y / max(1, frame.shape[0]),
                    width / max(1, frame.shape[1]),
                    height / max(1, frame.shape[0]),
                ))
        capture.release()
        if boxes:
            return tuple(float(median(values)) for values in zip(*boxes, strict=True))
    except Exception:
        pass
    return None
