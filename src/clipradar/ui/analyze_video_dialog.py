from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout, QWidget

from clipradar.settings.models import YOUTUBE_VIDEO_CATEGORIES
from clipradar.ui.common import muted_label, title_label


class AnalyzeVideoDialog(QDialog):
    def __init__(
        self,
        default_category_id: str,
        *,
        video_url: str = "",
        video_title: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Analyze specific video")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        if video_title:
            heading = QLabel(video_title)
            heading.setObjectName("SectionTitle")
            heading.setWordWrap(True)
            layout.addWidget(heading)
            layout.addWidget(muted_label("This video is already selected from the channel feed."))
        else:
            layout.addWidget(title_label("YouTube video URL"))
        self.url_field = QLineEdit(video_url)
        self.url_field.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self.url_field.setReadOnly(bool(video_url))
        layout.addWidget(self.url_field)
        layout.addWidget(title_label("Genre"))
        self.genre = QComboBox()
        for name, category_id in YOUTUBE_VIDEO_CATEGORIES:
            self.genre.addItem(name, category_id)
        default_index = self.genre.findData(default_category_id)
        self.genre.setCurrentIndex(default_index if default_index >= 0 else 0)
        layout.addWidget(self.genre)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Analyze")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("PrimaryButton")
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self._accept_if_valid)
        layout.addWidget(self.buttons)

    def _accept_if_valid(self) -> None:
        if self.url_field.text().strip():
            self.accept()

    def payload(self) -> tuple[str, str]:
        return self.url_field.text().strip(), str(self.genre.currentData())
