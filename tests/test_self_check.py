"""Self-check and CLI tests (SPEC I2)."""

from __future__ import annotations

import re
from types import ModuleType

import pytest

from app.__version__ import __version__
from app.main import SELF_CHECK_FAIL, SELF_CHECK_OK, main, run_self_check


def _fake_mt5(version: str) -> ModuleType:
    module = ModuleType("MetaTrader5")
    module.__version__ = version  # type: ignore[attr-defined]
    return module


def test_self_check_ok(capsys: pytest.CaptureFixture[str]) -> None:
    code = run_self_check(lambda: _fake_mt5("5.0.6180"))
    out = capsys.readouterr().out
    assert code == 0
    assert SELF_CHECK_OK in out
    assert "5.0.6180" in out


def test_self_check_fail_on_import_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _boom() -> ModuleType:
        raise ImportError("No module named 'MetaTrader5'")

    code = run_self_check(_boom)
    out = capsys.readouterr().out
    assert code == 1
    assert SELF_CHECK_FAIL in out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--version"])
    out = capsys.readouterr().out
    assert code == 0
    assert re.search(rf"MT5 Trading Workstation {re.escape(__version__)}", out)
