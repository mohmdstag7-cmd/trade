"""Sidebar widget tests (SPEC F2)."""

from __future__ import annotations

import pytest

from app.core.settings import UiSettings
from app.ui.i18n.translator import Translator
from app.ui.pages.base import GROUPS, PAGES
from app.ui.widgets.sidebar import Sidebar


@pytest.fixture
def sidebar(translator: Translator, ui_settings: UiSettings) -> Sidebar:
    return Sidebar(translator, ui_settings)


def test_sidebar_has_all_pages(sidebar: Sidebar) -> None:
    assert len(sidebar._buttons) == len(PAGES)


def test_sidebar_groups_in_order(sidebar: Sidebar) -> None:
    # one group label per group, created in canonical order
    groups_in_order = [meta.group for meta in PAGES]
    unique = [g for i, g in enumerate(groups_in_order) if i == 0 or groups_in_order[i - 1] != g]
    assert unique == [key for key, _title in GROUPS]


def test_click_emits_navigate(qtbot: object, sidebar: Sidebar) -> None:
    captured: list[str] = []
    sidebar.navigate.connect(captured.append)
    with qtbot.waitSignal(sidebar.navigate, timeout=2000):  # type: ignore[attr-defined]
        sidebar._buttons["market"].click()
    assert captured == ["market"]


def test_set_active_checks_button(sidebar: Sidebar) -> None:
    sidebar.set_active("risk")
    assert sidebar._buttons["risk"].isChecked()
    assert sidebar._buttons["market"].isChecked() is False


def test_collapse_persists(translator: Translator, settings_path: str) -> None:
    settings = UiSettings.load(settings_path)
    sidebar = Sidebar(translator, settings)
    assert sidebar.collapsed is False

    sidebar.toggle_collapsed()
    assert sidebar.collapsed is True
    assert UiSettings.load(settings_path).sidebar_collapsed is True


def test_retranslate_updates_button_texts(sidebar: Sidebar, translator: Translator) -> None:
    assert sidebar._buttons["dashboard"].text() == "Dashboard"
    translator.set_language("fa")
    sidebar.retranslate()
    assert sidebar._buttons["dashboard"].text() == "داشبورد"
