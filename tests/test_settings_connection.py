"""Settings page connection card tests (pytest-qt, offscreen)."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.event_bus import EventBus
from app.core.settings import Mt5AccountSettings
from app.mt5.credentials import CredentialStore
from app.ui.pages.settings import SettingsPage
from tests.fakes.fake_mt5 import FakeKeyring, FakeMetaTrader5, FakeSymbolConfig


@pytest.fixture
def keyring_module() -> FakeKeyring:
    return FakeKeyring()


@pytest.fixture
def account(settings_path: str) -> Mt5AccountSettings:
    return Mt5AccountSettings.load(settings_path)


def make_page(
    translator,
    theme_manager,
    account: Mt5AccountSettings,
    keyring_module: Any,
    qtbot,
) -> SettingsPage:
    fake_factory = lambda: FakeMetaTrader5(  # noqa: E731 - injected factory
        symbols=[FakeSymbolConfig("EURUSD.m"), FakeSymbolConfig("GBPUSD")]
    )
    page = SettingsPage(
        translator,
        theme_manager,
        None,
        account_settings=account,
        credential_store=CredentialStore(keyring_module),
        mt5_factory=fake_factory,
        bus=EventBus(),
    )
    qtbot.addWidget(page)
    return page


class TestSavePassword:
    def test_saves_to_vault(
        self, translator, theme_manager, account, keyring_module, qtbot
    ) -> None:
        page = make_page(translator, theme_manager, account, keyring_module, qtbot)
        page._login_edit.setText("12345678")
        page._password_edit.setText("hunter2")
        page._on_save_password()
        assert keyring_module.store[("MT5TradingWorkstation", "12345678")] == "hunter2"
        assert "Credential Manager" in page._result_label.text()

    def test_bad_login_shows_message(
        self, translator, theme_manager, account, keyring_module, qtbot
    ) -> None:
        page = make_page(translator, theme_manager, account, keyring_module, qtbot)
        page._login_edit.setText("abc")
        page._password_edit.setText("hunter2")
        page._on_save_password()
        assert "integer" in page._result_label.text() or "عدد" in page._result_label.text()
        assert not keyring_module.store


class TestTestConnection:
    def test_missing_login_server_shows_message(
        self, translator, theme_manager, account, keyring_module, qtbot
    ) -> None:
        page = make_page(translator, theme_manager, account, keyring_module, qtbot)
        page._on_test_connection()
        assert (
            "account number" in page._result_label.text()
            or "شماره حساب" in page._result_label.text()
        )

    def test_missing_password_shows_message(
        self, translator, theme_manager, account, keyring_module, qtbot
    ) -> None:
        page = make_page(translator, theme_manager, account, keyring_module, qtbot)
        page._login_edit.setText("12345678")
        page._server_edit.setText("FakeServer-Demo")
        page._on_test_connection()
        assert "password" in page._result_label.text().lower() or "رمز" in page._result_label.text()

    def test_full_probe_updates_result_and_bus(
        self, translator, theme_manager, account, keyring_module, qtbot
    ) -> None:
        page = make_page(translator, theme_manager, account, keyring_module, qtbot)
        seen: list[tuple[bool, str]] = []
        page._bus.mt5_connection_changed.connect(lambda ok, detail: seen.append((ok, detail)))

        page._login_edit.setText("12345678")
        page._server_edit.setText("FakeServer-Demo")
        page._password_edit.setText("hunter2")
        page._on_test_connection()

        def finished() -> bool:
            return page._test_button.isEnabled() and "Connected" in page._result_label.text()

        qtbot.wait_until(finished, timeout=10000)
        assert seen, "bus signal must fire with the probe verdict"
        ok, detail = seen[0]
        assert ok is True
        assert "***678" in detail
        assert "EURUSD.m" in detail
        # settings persisted
        assert account.login == 12345678
        assert account.server == "FakeServer-Demo"

    def test_probe_failure_reports(
        self, translator, theme_manager, account, keyring_module, qtbot
    ) -> None:
        def failing_factory() -> FakeMetaTrader5:
            fake = FakeMetaTrader5(symbols=[FakeSymbolConfig("EURUSD.m")])
            fake.fail_initialize = (-6, "Terminal: authorization failed")
            return fake

        page = SettingsPage(
            translator,
            theme_manager,
            None,
            account_settings=account,
            credential_store=CredentialStore(keyring_module),
            mt5_factory=failing_factory,
            bus=EventBus(),
        )
        qtbot.addWidget(page)
        page._login_edit.setText("12345678")
        page._server_edit.setText("FakeServer-Demo")
        page._password_edit.setText("hunter2")
        page._on_test_connection()

        def finished() -> bool:
            return page._test_button.isEnabled() and "failed" in page._result_label.text().lower()

        qtbot.wait_until(finished, timeout=10000)
        assert (
            "Login failed" in page._result_label.text()
            or "ورود ناموفق" in page._result_label.text()
        )
