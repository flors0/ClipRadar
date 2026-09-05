from __future__ import annotations

from clipradar.ui.main_window import MainWindow


def test_main_window_opens_and_navigates(qtbot, services):
    window = MainWindow(services, start_background=False)
    qtbot.addWidget(window)
    window.show()
    assert window.page_title.text() == "Dashboard"
    window.nav_buttons[1].click()
    assert window.page_title.text() == "Channels"
    window.nav_buttons[2].click()
    assert window.page_title.text() == "Review"
    window.nav_buttons[3].click()
    assert window.page_title.text() == "Publishing"
    window.nav_buttons[4].click()
    assert window.page_title.text() == "Settings"
    assert window.settings.model.currentData()
    window.settings.nav.setCurrentRow(5)
    assert window.settings.description_style.currentText() in {"Auto", "Short", "Detailed"}
    assert "tags" in window.settings.default_tags.placeholderText().lower()
