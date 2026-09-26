"""Regression tests for the v0.6.1 startup crash.

``Gateway.state`` is a ``@property`` returning ``ConnectionState``; the settings
page used to call it as a method (``state()``), which raised
``TypeError: 'ConnectionState' object is not callable`` on every launch as soon
as a shared gateway was wired into the page (settings.py retranslate).

These tests keep a *live* gateway object attached to the page so the guarded
branch (``shared_gateway is not None``) is actually exercised — the existing
fixtures always passed ``None``, which is how the bug slipped through CI.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.core.event_bus import EventBus
from app.core.settings import Mt5AccountSettings
from app.mt5.credentials import CredentialStore
from app.mt5.gateway import MT5Gateway
from app.mt5.models import ConnectionState
from app.ui.pages.settings import SettingsPage
from tests.fakes.fake_mt5 import FakeKeyring, FakeMetaTrader5, FakeSymbolConfig


@pytest.fixture
def keyring_module() -> FakeKeyring:
    return FakeKeyring()


@pytest.fixture
def account(settings_path: str) -> Mt5AccountSettings:
    return Mt5AccountSettings.load(settings_path)


class _StatefulGatewayStub:
    """Minimal stand-in exposing the same property API the page relies on."""

    def __init__(self, state: ConnectionState) -> None:
        self._state = state

    @property
    def state(self) -> ConnectionState:
        return self._state


def _make_page(
    translator: Any,
    theme_manager: Any,
    account: Mt5AccountSettings,
    keyring_module: FakeKeyring,
    qtbot: Any,
    gateway: Any,
) -> SettingsPage:
    factory: Callable[[], FakeMetaTrader5] = lambda: FakeMetaTrader5(  # noqa: E731
        symbols=[FakeSymbolConfig("EURUSD.m")]
    )
    page = SettingsPage(
        translator,
        theme_manager,
        None,
        account_settings=account,
        credential_store=CredentialStore(keyring_module),
        mt5_factory=factory,
        bus=EventBus(),
        shared_gateway=gateway,
    )
    qtbot.addWidget(page)
    return page


class TestGatewayStatePropertyRegression:
    """v0.6.1 crashed in SettingsPage.retranslate when a gateway was attached."""

    def test_construction_and_retranslate_with_live_gateway(
        self, translator, theme_manager, account, keyring_module, qtbot
    ) -> None:
        gateway = MT5Gateway(mt5_factory=lambda: FakeMetaTrader5())
        page = _make_page(translator, theme_manager, account, keyring_module, qtbot, gateway)

        page.retranslate()  # crashed on 0.6.1

        assert page._connect_button.text() == translator.translate("connect.connect")

    def test_retranslate_with_connected_gateway(
        self, translator, theme_manager, account, keyring_module, qtbot
    ) -> None:
        gateway = _StatefulGatewayStub(ConnectionState.CONNECTED)
        page = _make_page(translator, theme_manager, account, keyring_module, qtbot, gateway)

        page.retranslate()

        assert page._connect_button.text() == translator.translate("connect.disconnect")

    def test_connect_click_when_connected_requests_disconnect(
        self, translator, theme_manager, account, keyring_module, qtbot, monkeypatch
    ) -> None:
        gateway = _StatefulGatewayStub(ConnectionState.CONNECTED)
        page = _make_page(translator, theme_manager, account, keyring_module, qtbot, gateway)
        calls: list[str] = []
        monkeypatch.setattr(
            page, "_start_connect_worker", lambda mode, request=None: calls.append(mode)
        )

        page._on_connect_clicked()  # crashed on 0.6.1 (state() call, line 427)

        assert calls == ["disconnect"]

    def test_gateway_state_is_enum_not_callable(self) -> None:
        gateway = MT5Gateway(mt5_factory=lambda: FakeMetaTrader5())

        assert isinstance(gateway.state, ConnectionState)
        with pytest.raises(TypeError):
            gateway.state()  # type: ignore[misc] # must stay a property, not a method
