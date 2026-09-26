"""Icon provider — qtawesome (Material Design Icons) with graceful fallback.

Icons are a visual garnish only: every call site must keep working when
qtawesome is unavailable (raises, or fonts fail to load in an exotic
environment). :func:`icon` therefore returns ``None`` instead of raising,
and callers simply skip ``setIcon``.

All names live in :data:`PAGE_ICONS` / :data:`UI_ICONS` so the visual
language stays in one place.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtGui import QIcon

from app.ui.theme.tokens import ThemeTokens

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import edge is exercised implicitly by the app
    import qtawesome as qta

    _QTA: Any | None = qta
except Exception:  # pragma: no cover - defensive: iconless fallback
    _QTA = None

#: Sidebar page key -> MDI icon name.
PAGE_ICONS: dict[str, str] = {
    "dashboard": "mdi.view-dashboard-outline",
    "market": "mdi.candlestick-chart",
    "signals": "mdi.bell-outline",
    "positions": "mdi.swap-horizontal-bold",
    "analytics": "mdi.chart-areaspline",
    "journal": "mdi.notebook-outline",
    "backtest": "mdi.history",
    "model": "mdi.brain",
    "ai_lab": "mdi.robot-outline",
    "strategies": "mdi.chess-knight",
    "risk": "mdi.shield-alert-outline",
    "logs": "mdi.text-box-outline",
    "health": "mdi.heart-pulse",
    "settings": "mdi.cog-outline",
}

#: Small UI icons used across chrome (status bar, cards, toasts).
UI_ICONS: dict[str, str] = {
    "collapse_left": "mdi.chevron-left",
    "collapse_right": "mdi.chevron-right",
    "theme_dark": "mdi.weather-night",
    "theme_light": "mdi.weather-sunny",
    "language": "mdi.translate",
    "update": "mdi.download-outline",
    "check": "mdi.check-bold",
    "alert": "mdi.alert-outline",
    "info": "mdi.information-outline",
    "refresh": "mdi.refresh",
    "link": "mdi.link-variant",
    "database": "mdi.database-outline",
    "server": "mdi.server-network-outline",
    "palette": "mdi.palette-outline",
    "empty": "mdi.tray-full",
    "open_folder": "mdi.folder-open-outline",
}


def icon(name: str, color: str, size: int = 18) -> QIcon | None:
    """Return an icon for ``name`` or ``None`` when icons are unavailable.

    ``name`` may be a full MDI name or one of the short keys in
    :data:`PAGE_ICONS` / :data:`UI_ICONS`.
    """
    if _QTA is None:
        return None
    resolved = PAGE_ICONS.get(name, UI_ICONS.get(name, name))
    try:
        result: QIcon | None = _QTA.icon(resolved, color=color)
        return result
    except Exception:  # pragma: no cover - never let a garnish crash the app
        logger.debug("icon %r unavailable", name)
        return None


def page_icon(key: str, tokens: ThemeTokens, size: int = 18) -> QIcon | None:
    """Sidebar icon for page ``key`` in the active theme."""
    name = PAGE_ICONS.get(key)
    if name is None:
        return None
    return icon(name, tokens.text_secondary, size)


def ui_icon(key: str, tokens: ThemeTokens, size: int = 18) -> QIcon | None:
    """Chrome icon for short key ``key`` in the active theme."""
    name = UI_ICONS.get(key)
    if name is None:
        return None
    return icon(name, tokens.text_secondary, size)
