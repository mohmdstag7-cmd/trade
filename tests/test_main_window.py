"""Main window smoke tests (pytest-qt, SPEC G1-4)."""

from __future__ import annotations

import pytest

from app.__version__ import __version__
from app.core.event_bus import EventBus
from app.core.settings import UiSettings
from app.ui.i18n.translator import Translator
from app.ui.main_window import MainWindow
from app.ui.pages.base import PAGES
from app.ui.theme.manager import ThemeManager


@pytest.fixture
def window(
    qtbot: object,
    ui_settings: UiSettings,
    translator: Translator,
    theme_manager: ThemeManager,
) -> MainWindow:
    del qtbot
    window = MainWindow(
        bus=EventBus(),
        settings=ui_settings,
        translator=translator,
        theme_manager=theme_manager,
    )
    return window


def test_window_has_all_pages(window: MainWindow) -> None:
    assert len(window.page_keys) == len(PAGES) == 14


def test_switch_page_changes_stack(window: MainWindow) -> None:
    window.switch_page("market")
    assert window._stack.currentWidget() is window._pages["market"]


def test_switch_page_validates_key(window: MainWindow) -> None:
    with pytest.raises(KeyError):
        window.switch_page("does-not-exist")


def test_palette_opens_with_ctrl_k(qtbot: object, window: MainWindow) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence

    window.show()
    palette = window._palette
    # simulate the shortcut directly (robust across window managers)
    shortcut = window._palette_shortcut
    assert shortcut.key() == QKeySequence("Ctrl+K")
    window.open_palette()
    assert palette.isVisible()
    assert palette._list.count() >= len(PAGES)
    del Qt


def test_status_bar_shows_version(window: MainWindow) -> None:
    assert f"v{__version__}" in window._status_bar._version_label.text()


def test_theme_toggle_from_status_bar(window: MainWindow) -> None:
    assert window._theme_manager.current == "dark"
    window._status_bar._theme_button.click()
    assert window._theme_manager.current == "light"


def test_language_toggle_retranslates_sidebar(window: MainWindow) -> None:
    window._toggle_language()
    assert window._translator.language == "fa"
    dashboard = window._sidebar._buttons["dashboard"]
    assert dashboard.text() == "داشبورد"


def test_navigate_requested_from_bus(window: MainWindow) -> None:
    bus = window._bus
    bus.navigate_requested.emit("risk")
    assert window._stack.currentWidget() is window._pages["risk"]
