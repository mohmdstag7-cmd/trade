"""Shared pytest fixtures.

The offscreen Qt platform is selected *before* PySide6 is imported, so the
whole suite runs headless on Linux CI, Windows CI, and dev machines alike.
"""

from __future__ import annotations

import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.core.settings import UiSettings
from app.ui.i18n.translator import Translator
from app.ui.theme.manager import ThemeManager


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """Guarantee a QApplication for the whole session.

    Test isolation: several UI tests construct widgets without ``qtbot``;
    they previously depended on another test file creating the app first.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def settings_path(tmp_path: pathlib.Path) -> str:
    """Path to a per-test INI settings file."""
    return str(tmp_path / "settings.ini")


@pytest.fixture
def ui_settings(settings_path: str) -> UiSettings:
    return UiSettings.load(settings_path)


@pytest.fixture
def translator(ui_settings: UiSettings) -> Translator:
    return Translator(ui_settings)


@pytest.fixture
def theme_manager(ui_settings: UiSettings) -> ThemeManager:
    return ThemeManager(ui_settings)
