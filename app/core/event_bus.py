"""Application-wide event bus.

A tiny typed pub/sub built on Qt signals. Components communicate through
this bus instead of holding references to each other, which keeps the UI
layer decoupled and testable.

Scope note (SPEC D2): the bus intentionally carries only UI-level events in
Phase 1. Trading events arrive with the engine phases.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    """Application event bus (UI-level events in Phase 1)."""

    #: Emitted when the visual theme changed ("dark" / "light").
    theme_changed = Signal(str)

    #: Emitted when the UI language changed ("en" / "fa").
    language_changed = Signal(str)

    #: Emitted when someone requests navigation to a page key.
    navigate_requested = Signal(str)

    #: Emitted when the MT5 connection verdict changed (ok, detail).
    mt5_connection_changed = Signal(bool, str)
