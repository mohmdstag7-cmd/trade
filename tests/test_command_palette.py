"""Command palette tests (SPEC F2)."""

from __future__ import annotations

import pytest

from app.ui.i18n.translator import Translator
from app.ui.widgets.command_palette import Command, CommandPalette


@pytest.fixture
def palette(translator: Translator, qtbot: object) -> CommandPalette:
    del qtbot  # ensures a QApplication exists
    return CommandPalette(translator)


def _commands() -> list[Command]:
    return [
        Command("goto.dashboard", "Go to Dashboard", lambda: None),
        Command("goto.market", "Go to Market", lambda: None),
        Command("toggle.theme", "Toggle dark / light theme", lambda: None),
        Command("app.quit", "Quit", lambda: None),
    ]


def test_filter_narrows_results(palette: CommandPalette) -> None:
    palette.set_commands(_commands())
    palette._input.setText("market")
    assert palette._list.count() == 1
    assert palette._list.item(0).text() == "Go to Market"


def test_filter_ranking_prefix_first(palette: CommandPalette) -> None:
    palette.set_commands(
        [
            Command("b", "Toggle theme", lambda: None),
            Command("a", "Toggle something", lambda: None),
        ]
    )
    palette._input.setText("toggle")
    assert palette._list.item(0).text() == "Toggle theme"


def test_empty_filter_shows_disabled_row(palette: CommandPalette) -> None:
    palette.set_commands(_commands())
    palette._input.setText("zzz-no-match")
    assert palette._list.count() == 1
    item = palette._list.item(0)
    assert item is not None
    assert not (
        item.flags() & __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.ItemFlag.ItemIsSelectable
    )


def test_enter_executes_and_emits(qtbot: object, palette: CommandPalette) -> None:
    executed: list[str] = []
    palette.command_executed.connect(executed.append)
    palette.set_commands(_commands())
    palette._input.setText("quit")

    with qtbot.waitSignal(palette.command_executed, timeout=2000):  # type: ignore[attr-defined]
        palette._activate_item(palette._list.item(0))
    assert executed == ["app.quit"]
