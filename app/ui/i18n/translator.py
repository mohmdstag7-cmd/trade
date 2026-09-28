"""Translator: lookup, formatting, persistence, and layout direction."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal

from app.core.settings import VALID_LANGUAGES, UiSettings
from app.ui.i18n.strings import STRINGS


class Translator(QObject):
    """Translates UI string keys and owns the active language."""

    #: Emitted after the language changed; carries the new language code.
    language_changed = Signal(str)

    def __init__(self, settings: UiSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._language = settings.language

    @property
    def language(self) -> str:
        """Active language code ("en" / "fa")."""
        return self._language

    @property
    def is_rtl(self) -> bool:
        """Whether the active language is right-to-left."""
        return self._language == "fa"

    def layout_direction(self) -> Qt.LayoutDirection:
        """Qt layout direction for the active language."""
        return Qt.LayoutDirection.RightToLeft if self.is_rtl else Qt.LayoutDirection.LeftToRight

    def translate(self, key: str, **kwargs: object) -> str:
        """Translate ``key`` with optional ``{placeholder}`` formatting.

        Fallback order: active language → English → the key itself, so a
        missing translation can never raise in production.
        """
        template = STRINGS.get(self._language, {}).get(key)
        if template is None:
            template = STRINGS.get("en", {}).get(key)
        if template is None:
            return key
        if not kwargs:
            return template
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return template

    def set_language(self, language: str) -> None:
        """Switch to ``language`` ("en"/"fa"), persist and notify."""
        if language not in VALID_LANGUAGES:
            msg = f"unknown language: {language!r}"
            raise ValueError(msg)
        if language == self._language:
            return
        self._language = language
        self._settings.language = language
        self.language_changed.emit(language)
