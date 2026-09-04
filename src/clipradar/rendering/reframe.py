from __future__ import annotations

from pathlib import Path
from statistics import median


def detect_face_focus(source: str | Path, start: float, end: float, source_width: int) -> float:
    """Returns a stable normalized horizontal focus. Center is the safe fallback."""
    try:
        import cv2

        capture = cv2.VideoCapture(str(source))
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        centers: list[float] = []
        samples = 10
        for index in range(samples):
            moment = start + (end - start) * (index + 0.5) / samples
            capture.set(cv2.CAP_PROP_POS_MSEC, moment * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(36, 36))
            if len(faces):
                x, _, width, _ = max(faces, key=lambda box: box[2] * box[3])
                centers.append((x + width / 2) / max(1, frame.shape[1]))
        capture.release()
        if centers:
            return max(0.12, min(0.88, float(median(centers))))
    except Exception:
        pass
    return 0.5 if source_width else 0.5

