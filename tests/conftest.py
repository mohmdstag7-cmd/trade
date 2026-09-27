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


@pytest.fixture(scope="session", autouse=True)
def _harden_pyqtgraph_plot_menu():
    """Defuse the flaky Windows CI crash inside pyqtgraph menu construction.

    pyqtgraph 0.13.7 builds ``PlotItem``'s "Plot Options" context menu
    unconditionally in ``__init__`` — even with ``enableMenu=False`` — and
    embeds native widgets into it via ``QWidgetAction``. That
    widget-inside-menu construction intermittently access-violates on the
    Windows CI runner (fatal, session-killing; it first hit ViewBoxMenu,
    then PlotItem's ctrl menu). The app never uses chart context menus, so
    for the test session only, ``QWidgetAction`` is replaced by a plain
    ``QAction`` subclass that holds (but never natively attaches) the
    widget. Nothing else in the repo uses ``QWidgetAction``.
    """
    try:
        import pyqtgraph.graphicsItems.PlotItem  # noqa: F401  (presence probe)
        from PySide6 import QtGui, QtWidgets
    except ImportError:  # pragma: no cover - pyqtgraph optional in some envs
        yield
        return

    original = QtWidgets.QWidgetAction

    class _PlainMenuAction(QtGui.QAction):
        def setDefaultWidget(self, widget: QtWidgets.QWidget) -> None:
            # Keep a Python reference so the widget is not GC'd mid-session;
            # never attach it to the native menu (the crash site).
            self._default_widget = widget

    QtWidgets.QWidgetAction = _PlainMenuAction
    try:
        yield
    finally:
        QtWidgets.QWidgetAction = original


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
