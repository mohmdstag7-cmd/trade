"""Transient toast notifications (top-right overlay on the main window).

Toasts are fire-and-forget: :meth:`ToastHost.show_toast` stacks a small
non-modal card that auto-dismisses after ``TOAST_MS``. They carry an
optional semantic variant which tints the title icon (ok / bad / warn /
info). The host tracks its parent window's resize events via an event
filter and re-anchors to the leading corner (right in LTR, left in RTL).
"""

from __future__ import annotations

import contextlib
from typing import Any

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import icon
from app.ui.theme.manager import ThemeManager
from app.ui.theme.tokens import ThemeTokens

TOAST_MS = 4500
TOAST_WIDTH = 340
TOAST_MARGIN = 18
TOAST_GAP = 10
FADE_MS = 120

_VARIANT_ICONS = {
    "ok": ("check", "profit"),
    "bad": ("alert", "loss"),
    "warn": ("alert", "warning"),
    "info": ("info", "info"),
}


class ToastCard(QFrame):
    """One toast bubble."""

    closed = Signal(object)

    def __init__(
        self,
        title: str,
        body: str = "",
        variant: str = "info",
        theme: ThemeTokens | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._variant = variant
        self._theme = theme
        self._timer: QTimer | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 12, 12)
        row.setSpacing(10)

        self._icon_label = QLabel()
        self._icon_label.setFixedSize(20, 20)
        row.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._title = QLabel(title)
        self._title.setObjectName("ToastTitle")
        self._title.setWordWrap(True)
        text_col.addWidget(self._title)
        if body:
            self._body = QLabel(body)
            self._body.setObjectName("ToastBody")
            self._body.setWordWrap(True)
            text_col.addWidget(self._body)
        row.addLayout(text_col, 1)

        self.setFixedWidth(TOAST_WIDTH)
        self._apply_theme(theme)

    # -- behavior -------------------------------------------------------------
    def start_timer(self, ms: int = TOAST_MS) -> None:
        """Start (or restart) the auto-dismiss countdown."""
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(lambda: self.closed.emit(self))
        self._timer.start(ms)

    def _apply_theme(self, theme: ThemeTokens | None) -> None:
        if theme is None:
            return
        icon_name, color_role = _VARIANT_ICONS.get(self._variant, _VARIANT_ICONS["info"])
        ic = icon(icon_name, getattr(theme, color_role))
        if ic is not None:
            self._icon_label.setPixmap(ic.pixmap(18, 18))

    def enterEvent(self, event: Any) -> None:
        """Pause auto-dismiss while hovered."""
        super().enterEvent(event)
        if self._timer is not None:
            self._timer.stop()

    def leaveEvent(self, event: Any) -> None:
        """Resume auto-dismiss on leave."""
        super().leaveEvent(event)
        if self._timer is not None:
            self._timer.start()


class ToastHost(QWidget):
    """Overlay widget holding up to ``MAX_TOASTS`` stacked toast cards."""

    MAX_TOASTS = 3

    def __init__(self, window: QWidget, theme_manager: ThemeManager) -> None:
        super().__init__(window)
        self._window = window
        self._theme_manager = theme_manager
        self._cards: list[ToastCard] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()
        window.installEventFilter(self)
        theme_manager.theme_changed.connect(lambda _n: self._retheme())

    # -- public -------------------------------------------------------------
    def show_toast(self, title: str, body: str = "", variant: str = "info") -> None:
        """Show a transient toast; drops the oldest when the stack is full."""
        card = ToastCard(title, body, variant, self._theme_manager.tokens, self)
        card.closed.connect(self._dismiss)
        self._cards.append(card)
        while len(self._cards) > self.MAX_TOASTS:
            old = self._cards.pop(0)
            self._disconnect(old)
            old.deleteLater()
        self._relayout()
        self.show()
        self.raise_()
        card.start_timer()
        self._fade_in(card)

    # -- internals -----------------------------------------------------------
    def _disconnect(self, card: ToastCard) -> None:
        with contextlib.suppress(RuntimeError, TypeError):
            card.closed.disconnect()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Re-anchor the stack when the parent window resizes."""
        if obj is self._window and event.type() == QEvent.Type.Resize:
            self._relayout()
        return super().eventFilter(obj, event)

    def _dismiss(self, card: object) -> None:
        typed = card if isinstance(card, ToastCard) else None
        if typed is not None and typed in self._cards:
            self._cards.remove(typed)
            typed.deleteLater()
        if not self._cards:
            self.hide()
        else:
            self._relayout()

    def _retheme(self) -> None:
        for card in self._cards:
            card._apply_theme(self._theme_manager.tokens)

    def _anchor_x(self, width: int) -> int:
        rtl = self._window.layoutDirection() == Qt.LayoutDirection.RightToLeft
        if rtl:
            return TOAST_MARGIN
        return self._window.width() - TOAST_MARGIN - width

    def _relayout(self) -> None:
        y = TOAST_MARGIN
        for card in reversed(self._cards):
            card.adjustSize()
            h = card.sizeHint().height()
            card.setGeometry(self._anchor_x(TOAST_WIDTH), y, TOAST_WIDTH, h)
            y += h + TOAST_GAP

    def _fade_in(self, card: ToastCard) -> None:
        effect = QGraphicsOpacityEffect(card)
        card.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", card)
        anim.setDuration(FADE_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
