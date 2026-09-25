"""Status bar (SPEC F2): connection, mode badge, clock, theme/language toggles.

Phase 1 shows only real state: the app is not connected to MT5 yet, so the
connection dot is neutral and no account numbers are displayed. Real values
arrive with Phase 3.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QPushButton, QStatusBar, QWidget

from app.core.clock import format_local_time
from app.ui.i18n.translator import Translator


def repolish(widget: QWidget) -> None:
    """Force the style engine to re-evaluate dynamic properties."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class StatusBar(QStatusBar):
    """Persistent status bar across the bottom of the main window."""

    def __init__(
        self,
        translator: Translator,
        version: str,
        on_toggle_theme: Callable[[], None],
        on_toggle_language: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._version = version

        # -- left side: connection + mode -----------------------------------
        self._dot = QLabel()
        self._dot.setObjectName("ConnectionDot")
        self._dot.setProperty("connected", False)
        self._dot.setToolTip("")
        self.addWidget(self._dot)

        self._connection_label = QLabel()
        self.addWidget(self._connection_label)

        self._mode_badge = QLabel()
        self._mode_badge.setObjectName("Badge")
        self.addWidget(self._mode_badge)

        # -- right side: version, clock, toggles, kill switch ----------------
        self._version_label = QLabel(f"v{version}")
        self.addPermanentWidget(self._version_label)

        self._clock_label = QLabel()
        self._clock_label.setToolTip("")
        self.addPermanentWidget(self._clock_label)

        self._theme_button = QPushButton()
        self._theme_button.setObjectName("Badge")
        self._theme_button.setCursor(self.cursor())
        self._theme_button.clicked.connect(on_toggle_theme)
        self.addPermanentWidget(self._theme_button)

        self._language_button = QPushButton()
        self._language_button.setObjectName("Badge")
        self._language_button.clicked.connect(on_toggle_language)
        self.addPermanentWidget(self._language_button)

        self._kill_switch = QPushButton()
        self._kill_switch.setObjectName("KillSwitchButton")
        self._kill_switch.setEnabled(False)
        self.addPermanentWidget(self._kill_switch)

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._update_clock)
        self._clock.start()
        self._update_clock()

        translator.language_changed.connect(lambda _lang: self.retranslate())
        self.retranslate()

    # -- public API (used by later phases) --------------------------------------
    def set_connection_state(self, connected: bool) -> None:
        """Update the connection dot and label."""
        self._dot.setProperty("connected", connected)
        repolish(self._dot)
        key = "status.connected" if connected else "status.disconnected"
        self._connection_label.setText(self._translator.translate(key))

    # -- internals ---------------------------------------------------------------
    def _update_clock(self) -> None:
        self._clock_label.setText(format_local_time())

    def retranslate(self) -> None:
        """Refresh all texts for the current language."""
        tr = self._translator.translate
        self.set_connection_state(False)
        self._mode_badge.setText(tr("status.mode.none"))
        self._clock_label.setToolTip(tr("status.clock.tooltip"))
        self._theme_button.setToolTip(tr("status.theme.tooltip"))
        self._language_button.setToolTip(tr("status.language.tooltip"))
        self._kill_switch.setText(tr("status.killswitch"))
        self._kill_switch.setToolTip(tr("status.killswitch.tooltip"))
