"""UiSettings persistence tests."""

from __future__ import annotations

import pytest

from app.core.settings import UiSettings


def test_defaults(ui_settings: UiSettings) -> None:
    assert ui_settings.theme == "dark"
    assert ui_settings.language == "en"
    assert ui_settings.sidebar_collapsed is False


def test_roundtrip(settings_path: str) -> None:
    settings = UiSettings.load(settings_path)
    settings.theme = "light"
    settings.language = "fa"
    settings.sidebar_collapsed = True

    reloaded = UiSettings.load(settings_path)
    assert reloaded.theme == "light"
    assert reloaded.language == "fa"
    assert reloaded.sidebar_collapsed is True


def test_invalid_theme_raises(ui_settings: UiSettings) -> None:
    with pytest.raises(ValueError, match="invalid theme"):
        ui_settings.theme = "neon"


def test_invalid_language_raises(ui_settings: UiSettings) -> None:
    with pytest.raises(ValueError, match="invalid language"):
        ui_settings.language = "de"


def test_corrupted_values_fall_back_to_defaults(settings_path: str) -> None:
    settings = UiSettings.load(settings_path)
    settings.theme = "dark"
    settings.sync()
    # simulate corruption by writing directly into the QSettings store
    settings._qs.setValue("ui/theme", "bogus")
    settings.sync()
    reloaded = UiSettings.load(settings_path)
    assert reloaded.theme == "dark"
