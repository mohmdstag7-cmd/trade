"""Theme manager: owns the active theme, applies QSS, persists the choice."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from app.core.settings import UiSettings
from app.ui.theme.qss import build_qss
from app.ui.theme.tokens import THEMES, ThemeTokens


class ThemeManager(QObject):
    """Applies and persists the visual theme."""

    #: Emitted after the theme changed; carries the new theme name.
    theme_changed = Signal(str)

    def __init__(self, settings: UiSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._name = settings.theme
        self._rtl = False

    @property
    def current(self) -> str:
        """Name of the active theme ("dark" / "light")."""
        return self._name

    @property
    def tokens(self) -> ThemeTokens:
        """Design tokens of the active theme."""
        return THEMES[self._name]

    def set_theme(self, name: str) -> None:
        """Switch to ``name`` ("dark"/"light"), persist and apply it."""
        if name not in THEMES:
            msg = f"unknown theme: {name!r}"
            raise ValueError(msg)
        if name == self._name:
            return
        self._name = name
        self._settings.theme = name
        self.apply()
        self.theme_changed.emit(name)

    def toggle(self) -> None:
        """Switch between dark and light."""
        other = "light" if self._name == "dark" else "dark"
        self.set_theme(other)

    def apply(self, *, rtl: bool | None = None) -> None:
        """(Re)apply the current theme's stylesheet to the application.

        ``rtl`` mirrors the physical border sides in the generated QSS
        (QSS borders do NOT mirror automatically under RTL layout). When
        omitted, the current default is kept.
        """
        if rtl is not None:
            self._rtl = rtl
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(build_qss(self.tokens, rtl=self._rtl))
