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
    assert window.settings.model.currentData()

