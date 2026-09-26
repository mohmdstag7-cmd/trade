"""Status bar (SPEC F2): connection, mode badge, clock, theme/language toggles.

v2: the connection dot is tri-state (connected / connecting / failed),
reflecting the *live* shared-gateway state, and the clock renders in a
monospace stack so digits stop jittering.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QPushButton, QStatusBar, QWidget

from app.core.clock import broker_time_string, format_local_time
from app.ui.i18n.translator import Translator

#: Gateway states → (label key, dot property value).
_GATEWAY_STATES: dict[str, tuple[str, str]] = {
    "connecting": ("status.gateway.connecting", "connecting"),
    "reconnecting": ("status.gateway.reconnecting", "connecting"),
    "connected": ("status.connected", "true"),
    "disconnected": ("status.disconnected", "false"),
}


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
        self._dot.setProperty("connected", "false")
        self._dot.setToolTip("")
        self.addWidget(self._dot)

        self._connection_label = QLabel()
        self.addWidget(self._connection_label)

        self._mode_badge = QLabel()
        self._mode_badge.setObjectName("Badge")
        self.addWidget(self._mode_badge)

        # -- right side: version, clock, toggles, kill switch ----------------
        self._version_label = QLabel(f"v{version}")
        self._version_label.setObjectName("MonoLabel")
        self.addPermanentWidget(self._version_label)

        self._clock_label = QLabel()
        self._clock_label.setObjectName("MonoLabel")
        self._clock_label.setToolTip("")
        self.addPermanentWidget(self._clock_label)

        self._theme_button = QPushButton()
        self._theme_button.setObjectName("GhostButton")
        self._theme_button.setCursor(self.cursor())
        self._theme_button.clicked.connect(on_toggle_theme)
        self.addPermanentWidget(self._theme_button)

        self._language_button = QPushButton()
        self._language_button.setObjectName("GhostButton")
        self._language_button.clicked.connect(on_toggle_language)
        self.addPermanentWidget(self._language_button)

        self._kill_switch = QPushButton()
        self._kill_switch.setObjectName("DangerButton")
        self._kill_switch.setEnabled(False)
        self.addPermanentWidget(self._kill_switch)

        self._broker_offset: int | None = None
        self._broker_offset_text = ""

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._update_clock)
        self._clock.start()
        self._update_clock()

        translator.language_changed.connect(lambda _lang: self.retranslate())
        self.retranslate()
        # seed the dot property AFTER QSS is applied so repolish works later
        self.set_gateway_state("disconnected", "")

    # -- public API (used by later phases) --------------------------------------
    def set_connection_state(self, connected: bool, detail: str = "") -> None:
        """Update the connection dot, label and tooltip (probe verdict)."""
        self._dot.setProperty("connected", "true" if connected else "false")
        repolish(self._dot)
        key = "status.connected" if connected else "status.disconnected"
        self._connection_label.setText(self._translator.translate(key))
        tooltip = self._translator.translate("status.connection.tooltip")
        if detail:
            tooltip = f"{detail} — {tooltip}"
        self._connection_label.setToolTip(tooltip)
        self._dot.setToolTip(tooltip)

    def set_gateway_state(self, state: str, detail: str = "") -> None:
        """Reflect the live shared-gateway state (connecting/connected/…)."""
        label_key, dot_value = _GATEWAY_STATES.get(state, ("status.disconnected", "false"))
        self._dot.setProperty("connected", dot_value)
        repolish(self._dot)
        self._connection_label.setText(self._translator.translate(label_key))
        tooltip = self._translator.translate("status.connection.tooltip")
        if detail:
            tooltip = f"{detail} — {tooltip}"
        self._connection_label.setToolTip(tooltip)
        self._dot.setToolTip(tooltip)

    def set_broker_offset(self, offset_minutes: int | None, offset_text: str = "") -> None:
        """Show the broker wall clock once the offset is detected (Phase 5)."""
        self._broker_offset = offset_minutes
        self._broker_offset_text = offset_text
        self._update_clock()

    # -- internals ---------------------------------------------------------------
    def _update_clock(self) -> None:
        parts = [format_local_time()]
        if self._broker_offset is not None:
            broker = broker_time_string(self._broker_offset)
            parts.append(f"{self._broker_offset_text} {broker}")
        self._clock_label.setText("  ·  ".join(parts))

    def retranslate(self) -> None:
        """Refresh all texts for the current language."""
        tr = self._translator.translate
        self._mode_badge.setText(tr("status.mode.none"))
        if self._broker_offset is not None:
            self._clock_label.setToolTip(
                tr(
                    "status.clock.tooltip.broker",
                    offset=self._broker_offset_text,
                    time=broker_time_string(self._broker_offset),
                )
            )
        else:
            self._clock_label.setToolTip(tr("status.clock.tooltip"))
        self._theme_button.setToolTip(tr("status.theme.tooltip"))
        self._language_button.setToolTip(tr("status.language.tooltip"))
        self._kill_switch.setText(tr("status.killswitch"))
        self._kill_switch.setToolTip(tr("status.killswitch.tooltip"))
        # connection label refreshes on the next gateway/probe event
