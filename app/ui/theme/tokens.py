"""Design tokens (SPEC F1, v2 — Phase 6 design system refresh).

Dark is the default theme; the light theme uses the same roles with
WCAG-AA-friendly values. Adding a theme = adding a ``ThemeTokens`` instance
here plus registering it in ``THEMES``. Nothing else changes.

Every role is a ``#RRGGBB`` hex string so tokens embed directly into the
generated QSS. Semantic "soft" roles (``*_bg``) are the translucent-feeling
surface colors used behind pills and badges.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Color and shape roles for one visual theme."""

    name: str
    # surfaces
    bg: str
    surface: str
    card: str
    border: str
    border_strong: str
    input_bg: str
    hover: str
    selected: str
    # text
    text: str
    text_secondary: str
    text_disabled: str
    # brand / semantic
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    profit: str
    profit_bg: str
    loss: str
    loss_bg: str
    warning: str
    warning_bg: str
    info: str
    info_bg: str
    # navigation chrome
    sidebar_bg: str
    sidebar_active: str
    sidebar_hover: str
    # chart
    chart_grid: str
    chart_crosshair: str


DARK = ThemeTokens(
    name="dark",
    bg="#0B0D12",
    surface="#12151C",
    card="#161A23",
    border="#242A3A",
    border_strong="#323A50",
    input_bg="#0F1219",
    hover="#1A1F2C",
    selected="#20304C",
    text="#E8EAF2",
    text_secondary="#8B93A9",
    text_disabled="#565D72",
    accent="#5B8CFF",
    accent_hover="#729BFF",
    accent_pressed="#4A79E8",
    accent_soft="#1B2A4A",
    profit="#26C66E",
    profit_bg="#12291E",
    loss="#F15B5B",
    loss_bg="#2E1A1C",
    warning="#F5A623",
    warning_bg="#2E2415",
    info="#4CC3E8",
    info_bg="#14282F",
    sidebar_bg="#0D1017",
    sidebar_active="#1D2436",
    sidebar_hover="#161B28",
    chart_grid="#1C212E",
    chart_crosshair="#4A5470",
)

LIGHT = ThemeTokens(
    name="light",
    bg="#F2F4FA",
    surface="#FFFFFF",
    card="#FFFFFF",
    border="#DFE4F0",
    border_strong="#C3CCE0",
    input_bg="#FFFFFF",
    hover="#EEF1F9",
    selected="#DCE6FB",
    text="#131728",
    text_secondary="#5A6178",
    text_disabled="#9AA1B5",
    accent="#3D6EF7",
    accent_hover="#5A83F8",
    accent_pressed="#2F5BD9",
    accent_soft="#E3ECFE",
    profit="#14934B",
    profit_bg="#DDF3E6",
    loss="#D32F3E",
    loss_bg="#FBDFE1",
    warning="#C27407",
    warning_bg="#FCEED3",
    info="#0E86AC",
    info_bg="#DBF1F8",
    sidebar_bg="#E9EDF7",
    sidebar_active="#D5DFF4",
    sidebar_hover="#E1E7F4",
    chart_grid="#E8ECF6",
    chart_crosshair="#9AA6C0",
)

#: Registry of available themes, keyed by theme name.
THEMES: dict[str, ThemeTokens] = {"dark": DARK, "light": LIGHT}

DEFAULT_THEME = "dark"

# -- non-color scales (shared by both themes) --------------------------------

#: Type scale (px), used by the generated QSS.
FONT_SIZE_H1 = 21
FONT_SIZE_H2 = 15
FONT_SIZE_BODY = 13
FONT_SIZE_SMALL = 12
FONT_SIZE_CAPTION = 11

#: Corner radius scale (px).
RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14

#: Spacing scale (px) — the only horizontal/vertical rhythm the UI may use.
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 24
SPACE_6 = 32

#: Monospace stack for prices, clocks and any numeric readout.
MONO_FONT_STACK = '"Cascadia Mono", "Consolas", "SF Mono", "DejaVu Sans Mono", monospace'
