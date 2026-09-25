"""Theme manager tests: switching, persistence, validation."""

from __future__ import annotations

import pytest

from app.core.settings import UiSettings
from app.ui.theme.manager import ThemeManager
from app.ui.theme.qss import build_qss
from app.ui.theme.tokens import DARK, LIGHT


def test_default_theme_is_dark(ui_settings: UiSettings) -> None:
    manager = ThemeManager(ui_settings)
    assert manager.current == "dark"


def test_toggle_switches_theme_and_persists(ui_settings: UiSettings, settings_path: str) -> None:
    manager = ThemeManager(ui_settings)
    manager.set_theme("light")
    assert manager.current == "light"

    reloaded = UiSettings.load(settings_path)
    assert reloaded.theme == "light"


def test_set_theme_emits_signal(ui_settings: UiSettings, qtbot: object) -> None:
    manager = ThemeManager(ui_settings)
    with qtbot.waitSignal(manager.theme_changed, timeout=2000):  # type: ignore[attr-defined]
        manager.set_theme("light")
    assert manager.current == "light"


def test_set_same_theme_is_noop(ui_settings: UiSettings) -> None:
    manager = ThemeManager(ui_settings)
    manager.set_theme("dark")  # already dark: no signal, no error
    assert manager.current == "dark"


def test_unknown_theme_raises(ui_settings: UiSettings) -> None:
    manager = ThemeManager(ui_settings)
    with pytest.raises(ValueError, match="unknown theme"):
        manager.set_theme("solarized")


def test_tokens_property_follows_current(ui_settings: UiSettings) -> None:
    manager = ThemeManager(ui_settings)
    assert manager.tokens is DARK
    manager.set_theme("light")
    assert manager.tokens is LIGHT
    assert build_qss(manager.tokens) != build_qss(DARK)
