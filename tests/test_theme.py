"""Design-token and QSS generation tests (SPEC F1)."""

from __future__ import annotations

import re

import pytest

from app.ui.theme.qss import build_qss
from app.ui.theme.tokens import DARK, DEFAULT_THEME, LIGHT, THEMES, ThemeTokens

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

_REQUIRED_ROLES = (
    "bg",
    "surface",
    "card",
    "border",
    "input_bg",
    "text",
    "text_secondary",
    "accent",
    "accent_hover",
    "accent_pressed",
    "profit",
    "loss",
    "warning",
    "sidebar_bg",
    "sidebar_active",
    "sidebar_hover",
)


@pytest.mark.parametrize("theme", list(THEMES.values()))
def test_tokens_have_all_roles_with_hex_colors(theme: ThemeTokens) -> None:
    for role in _REQUIRED_ROLES:
        value = getattr(theme, role)
        assert _HEX.match(value), f"role {role} is not a #RRGGBB color: {value!r}"


def test_dark_and_light_are_registered() -> None:
    assert set(THEMES) == {"dark", "light"}
    assert THEMES["dark"] is DARK
    assert THEMES["light"] is LIGHT
    assert DEFAULT_THEME == "dark"


def test_build_qss_embeds_theme_colors() -> None:
    qss = build_qss(DARK)
    assert DARK.bg in qss
    assert DARK.accent in qss
    assert "QMainWindow" in qss
    assert "#SidebarButton" in qss
    assert "#Card" in qss
    assert "#ConnectionDot" in qss


def test_themes_produce_different_stylesheets() -> None:
    assert build_qss(DARK) != build_qss(LIGHT)
