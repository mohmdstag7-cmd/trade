"""Settings page — fully functional in Phase 1 (appearance + about)."""

from __future__ import annotations

import platform
import sys

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.__version__ import __version__
from app.ui.i18n.translator import Translator
from app.ui.theme.manager import ThemeManager


class SettingsPage(QWidget):
    """Appearance settings (theme, language) and build information."""

    def __init__(
        self,
        translator: Translator,
        theme_manager: ThemeManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._theme_manager = theme_manager

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.setSpacing(16)

        # -- appearance card -------------------------------------------------
        appearance = QFrame()
        appearance.setObjectName("Card")
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setContentsMargins(28, 24, 28, 24)
        appearance_layout.setSpacing(12)

        self._appearance_title = QLabel()
        self._appearance_title.setObjectName("CardTitle")

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        self._theme_label = QLabel()
        self._theme_combo = QComboBox()
        self._theme_combo.addItem("", "dark")
        self._theme_combo.addItem("", "light")
        self._theme_combo.setCurrentIndex(0 if theme_manager.current == "dark" else 1)
        self._theme_combo.activated.connect(self._on_theme_activated)

        self._language_label = QLabel()
        self._language_combo = QComboBox()
        self._language_combo.addItem("", "en")
        self._language_combo.addItem("", "fa")
        self._language_combo.setCurrentIndex(0 if translator.language == "en" else 1)
        self._language_combo.activated.connect(self._on_language_activated)

        form.addRow(self._theme_label, self._theme_combo)
        form.addRow(self._language_label, self._language_combo)
        appearance_layout.addWidget(self._appearance_title)
        appearance_layout.addLayout(form)
        outer.addWidget(appearance)

        # -- about card ------------------------------------------------------
        about = QFrame()
        about.setObjectName("Card")
        about_layout = QVBoxLayout(about)
        about_layout.setContentsMargins(28, 24, 28, 24)
        about_layout.setSpacing(12)

        self._about_title = QLabel()
        self._about_title.setObjectName("CardTitle")

        self._version_label = QLabel()
        self._python_label = QLabel()

        about_layout.addWidget(self._about_title)
        about_layout.addWidget(self._version_label)
        about_layout.addWidget(self._python_label)
        outer.addWidget(about)
        outer.addStretch(1)

        translator.language_changed.connect(lambda _lang: self.retranslate())
        self.retranslate()

    # -- wiring --------------------------------------------------------------
    def _on_theme_activated(self, index: int) -> None:
        value = self._theme_combo.itemData(index)
        if isinstance(value, str):
            self._theme_manager.set_theme(value)

    def _on_language_activated(self, index: int) -> None:
        value = self._language_combo.itemData(index)
        if isinstance(value, str):
            self._translator.set_language(value)

    # -- retranslation ---------------------------------------------------------
    def retranslate(self) -> None:
        """Refresh all texts for the current language."""
        tr = self._translator.translate
        self._appearance_title.setText(tr("settings.appearance"))
        self._theme_label.setText(tr("settings.theme"))
        self._language_label.setText(tr("settings.language"))
        self._about_title.setText(tr("settings.about"))

        self._theme_combo.setItemText(0, tr("settings.theme.dark"))
        self._theme_combo.setItemText(1, tr("settings.theme.light"))
        self._language_combo.setItemText(0, tr("settings.language.en"))
        self._language_combo.setItemText(1, tr("settings.language.fa"))
        # keep placeholder width stable with longest item
        self._theme_combo.setFixedWidth(220)
        self._language_combo.setFixedWidth(220)

        self._version_label.setText(f"{tr('settings.version')}: {__version__}")
        self._python_label.setText(
            f"{tr('settings.python')}: {platform.python_version()} "
            f"({sys.platform}, {platform.machine()})"
        )
