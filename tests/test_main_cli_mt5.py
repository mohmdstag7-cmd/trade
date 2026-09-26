"""CLI tests for --mt5-smoke-test and --mt5-trade-test (SPEC C13)."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from app import main as app_main
from tests.fakes.fake_mt5 import (
    ACCOUNT_TRADE_MODE_REAL,
    FakeAccountConfig,
    FakeMetaTrader5,
    FakeSymbolConfig,
)


@pytest.fixture
def fake_module(monkeypatch: pytest.MonkeyPatch) -> FakeMetaTrader5:
    fake = FakeMetaTrader5(
        symbols=[FakeSymbolConfig("EURUSD.m"), FakeSymbolConfig("GBPUSD")],
    )
    monkeypatch.setattr(app_main, "_import_mt5", lambda: fake)
    return fake


@pytest.fixture
def stored_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the password is already in the OS vault."""
    from app.mt5.credentials import CredentialStore as _CS

    class VaultCredentialStore(_CS):
        def __init__(self, keyring_module: Any = None) -> None:
            super().__init__(keyring_module)

        def get_password(self, login: int | str) -> str | None:
            if str(login) == "12345678":
                return "vault-password"
            return None

    monkeypatch.setattr("app.mt5.credentials.CredentialStore", VaultCredentialStore)


def smoke_args(extra: list[str] | None = None) -> list[str]:
    args = [
        "--mt5-smoke-test",
        "--login",
        "12345678",
        "--server",
        "FakeServer-Demo",
    ]
    return args + (extra or [])


class TestSmokeTestCLI:
    def test_missing_credentials_exit_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = app_main.main(["--mt5-smoke-test"])
        assert code == 2
        assert "--login" in capsys.readouterr().out

    def test_no_password_exit_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = app_main.main(smoke_args())
        assert code == 2
        assert "password" in capsys.readouterr().out.lower()

    def test_happy_path_exit_0(
        self,
        fake_module: FakeMetaTrader5,
        stored_credentials: None,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        monkeypatch.setattr(app_main, "default_logs_dir", lambda: tmp_path)
        code = app_main.main(smoke_args())
        out = capsys.readouterr().out
        assert code == 0
        assert "SMOKE TEST PASSED" in out
        assert "secret" not in out
        assert "vault-password" not in out
        # JSON report written to the logs dir
        reports = list(tmp_path.glob("mt5_smoke_test_*.json"))
        assert len(reports) == 1
        payload = json.loads(reports[0].read_text(encoding="utf-8"))
        assert payload["ok"] is True

    def test_json_output(
        self,
        fake_module: FakeMetaTrader5,
        stored_credentials: None,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        monkeypatch.setattr(app_main, "default_logs_dir", lambda: tmp_path)
        code = app_main.main(smoke_args(["--json"]))
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["ok"] is True

    def test_password_via_stdin(
        self,
        fake_module: FakeMetaTrader5,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("typed-password\n"))
        monkeypatch.setattr(app_main, "default_logs_dir", lambda: tmp_path)
        code = app_main.main(smoke_args(["--password-stdin"]))
        assert code == 0
        assert "SMOKE TEST PASSED" in capsys.readouterr().out

    def test_auth_failure_exit_1(
        self,
        stored_credentials: None,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        def failing() -> FakeMetaTrader5:
            fake = FakeMetaTrader5(symbols=[FakeSymbolConfig("EURUSD.m")])
            fake.fail_initialize = (-6, "Terminal: authorization failed")
            return fake

        monkeypatch.setattr(app_main, "_import_mt5", failing)
        monkeypatch.setattr(app_main, "default_logs_dir", lambda: tmp_path)
        code = app_main.main(smoke_args())
        assert code == 1
        assert "SMOKE TEST FAILED" in capsys.readouterr().out


class TestTradeTestCLI:
    def test_requires_yes(
        self, fake_module: FakeMetaTrader5, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = app_main.main(
            ["--mt5-trade-test", "--login", "12345678", "--server", "FakeServer-Demo"]
        )
        assert code == 2
        assert "--yes" in capsys.readouterr().out

    def test_demo_account_trades(
        self,
        fake_module: FakeMetaTrader5,
        stored_credentials: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        code = app_main.main(
            [
                "--mt5-trade-test",
                "--login",
                "12345678",
                "--server",
                "FakeServer-Demo",
                "--symbol",
                "EURUSD",
                "--yes",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "demo trade test passed" in out
        assert "vault-password" not in out

    def test_real_account_refused(
        self,
        stored_credentials: None,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = FakeMetaTrader5(account=FakeAccountConfig(trade_mode=ACCOUNT_TRADE_MODE_REAL))
        monkeypatch.setattr(app_main, "_import_mt5", lambda: fake)
        code = app_main.main(
            [
                "--mt5-trade-test",
                "--login",
                "12345678",
                "--server",
                "FakeServer-Demo",
                "--yes",
            ]
        )
        out = capsys.readouterr().out
        assert code == 2
        assert "REFUSED" in out
