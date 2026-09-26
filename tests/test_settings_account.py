"""Mt5AccountSettings persistence tests (password never lives here)."""

from __future__ import annotations

import pytest

from app.core.settings import Mt5AccountSettings


@pytest.fixture
def account(settings_path: str) -> Mt5AccountSettings:
    return Mt5AccountSettings.load(settings_path)


class TestMt5AccountSettings:
    def test_defaults(self, account: Mt5AccountSettings) -> None:
        assert account.login == 0
        assert account.server == ""
        assert account.terminal_path == ""
        assert account.is_configured is False

    def test_roundtrip(self, settings_path: str) -> None:
        account = Mt5AccountSettings.load(settings_path)
        account.login = 12345678
        account.server = "MetaQuotes-Demo"
        account.terminal_path = r"C:\MT5\terminal64.exe"

        reloaded = Mt5AccountSettings.load(settings_path)
        assert reloaded.login == 12345678
        assert reloaded.server == "MetaQuotes-Demo"
        assert reloaded.terminal_path == r"C:\MT5\terminal64.exe"
        assert reloaded.is_configured is True

    def test_server_is_trimmed(self, account: Mt5AccountSettings) -> None:
        account.server = "  MetaQuotes-Demo  "
        assert account.server == "MetaQuotes-Demo"

    def test_negative_login_raises(self, account: Mt5AccountSettings) -> None:
        with pytest.raises(ValueError, match="invalid login"):
            account.login = -5

    def test_corrupted_login_falls_back_to_zero(self, account: Mt5AccountSettings) -> None:
        account._qs.setValue("mt5/login", "not-a-number")
        account._qs.sync()
        assert account.login == 0

    def test_is_configured_needs_both(self, account: Mt5AccountSettings) -> None:
        account.login = 12345678
        assert account.is_configured is False
        account.server = "MetaQuotes-Demo"
        assert account.is_configured is True

    def test_login_accepts_numeric_string(self, account: Mt5AccountSettings) -> None:
        account._qs.setValue("mt5/login", "  42 ")
        assert account.login == 42
