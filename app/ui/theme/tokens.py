"""Design tokens (SPEC F1).

Dark is the default theme; the light theme uses the same roles with
WCAG-AA-friendly values. Adding a theme = adding a ``ThemeTokens`` instance
here plus registering it in ``THEMES``. Nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Color and shape roles for one visual theme.

    All colors are ``#RRGGBB`` hex strings so they can be embedded directly
    into generated QSS.
    """

    name: str
    # surfaces
    bg: str
    surface: str
    card: str
    border: str
    input_bg: str
    # text
    text: str
    text_secondary: str
    # brand / semantic
    accent: str
    accent_hover: str
    accent_pressed: str
    profit: str
    loss: str
    warning: str
    # navigation chrome
    sidebar_bg: str
    sidebar_active: str
    sidebar_hover: str


DARK = ThemeTokens(
    name="dark",
    bg="#0B0D12",
    surface="#12151C",
    card="#171B24",
    border="#232836",
    input_bg="#0F1219",
    text="#E6E8EE",
    text_secondary="#8A91A5",
    accent="#5B8CFF",
    accent_hover="#6E99FF",
    accent_pressed="#4A79E8",
    profit="#22C55E",
    loss="#EF4444",
    warning="#F59E0B",
    sidebar_bg="#0E1117",
    sidebar_active="#1C2230",
    sidebar_hover="#161B26",
)

LIGHT = ThemeTokens(
    name="light",
    bg="#F4F6FB",
    surface="#FFFFFF",
    card="#FFFFFF",
    border="#E2E6F0",
    input_bg="#FFFFFF",
    text="#111522",
    text_secondary="#5B6274",
    accent="#3D6EF7",
    accent_hover="#5A83F8",
    accent_pressed="#2F5BD9",
    profit="#16A34A",
    loss="#DC2626",
    warning="#D97706",
    sidebar_bg="#EDF0F8",
    sidebar_active="#DEE5F5",
    sidebar_hover="#E7EBF5",
)

#: Registry of available themes, keyed by theme name.
THEMES: dict[str, ThemeTokens] = {"dark": DARK, "light": LIGHT}

DEFAULT_THEME = "dark"
