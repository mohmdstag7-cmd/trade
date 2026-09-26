"""Demo trade test guards and happy path (SPEC C13, I-3)."""

from __future__ import annotations

import pytest

from app.mt5.demo_trade_test import (
    MAX_TEST_VOLUME,
    DemoGuardError,
    run_demo_trade_test,
)
from app.mt5.errors import TradeError
from app.mt5.gateway import MT5Gateway
from app.mt5.models import ConnectRequest
from tests.fakes.fake_mt5 import (
    ACCOUNT_TRADE_MODE_CONTEST,
    ACCOUNT_TRADE_MODE_REAL,
    TRADE_RETCODE_NO_MONEY,
    FakeAccountConfig,
    FakeMetaTrader5,
    FakeSymbolConfig,
)

REQUEST = ConnectRequest(login=12345678, password="secret", server="FakeServer-Demo")


def connect_gateway(fake: FakeMetaTrader5) -> MT5Gateway:
    gateway = MT5Gateway(mt5_factory=lambda: fake, request_timeout_s=5.0)
    gateway.start()
    gateway.wait_for_result(gateway.connect(REQUEST), "connect")
    return gateway


class TestGuards:
    def test_real_account_refused_before_any_order(self) -> None:
        fake = FakeMetaTrader5(account=FakeAccountConfig(trade_mode=ACCOUNT_TRADE_MODE_REAL))
        gateway = connect_gateway(fake)
        try:
            with pytest.raises(DemoGuardError, match="REAL"):
                run_demo_trade_test(gateway, "EURUSD")
            assert "order_send" not in [name for name, _tid in fake.calls]
        finally:
            gateway.stop()

    def test_contest_account_refused(self) -> None:
        fake = FakeMetaTrader5(account=FakeAccountConfig(trade_mode=ACCOUNT_TRADE_MODE_CONTEST))
        gateway = connect_gateway(fake)
        try:
            with pytest.raises(DemoGuardError):
                run_demo_trade_test(gateway, "EURUSD")
        finally:
            gateway.stop()

    def test_volume_cap_enforced(self) -> None:
        fake = FakeMetaTrader5()
        gateway = connect_gateway(fake)
        try:
            with pytest.raises(DemoGuardError, match="cap"):
                run_demo_trade_test(gateway, "EURUSD", volume=MAX_TEST_VOLUME + 0.01)
        finally:
            gateway.stop()

    def test_volume_step_enforced(self) -> None:
        fake = FakeMetaTrader5()
        gateway = connect_gateway(fake)
        try:
            with pytest.raises(DemoGuardError, match="multiple"):
                run_demo_trade_test(gateway, "EURUSD", volume=0.017)
        finally:
            gateway.stop()

    def test_volume_below_broker_minimum_refused(self) -> None:
        fake = FakeMetaTrader5(symbols=[FakeSymbolConfig("EURUSD", volume_min=0.1)])
        gateway = connect_gateway(fake)
        try:
            with pytest.raises(DemoGuardError, match="minimum"):
                run_demo_trade_test(gateway, "EURUSD", volume=0.01)
        finally:
            gateway.stop()

    def test_closed_market_refused(self) -> None:
        # a symbol with zero quotes cannot be traded
        fake = FakeMetaTrader5(symbols=[])
        gateway = connect_gateway(fake)
        try:
            with pytest.raises(Exception):  # noqa: B017 - symbol_info raises TerminalError
                run_demo_trade_test(gateway, "EURUSD")
        finally:
            gateway.stop()


class TestHappyPath:
    def test_open_and_close(self) -> None:
        fake = FakeMetaTrader5()
        gateway = connect_gateway(fake)
        try:
            outcome = run_demo_trade_test(gateway, "EURUSD")
            assert outcome.ok
            assert outcome.opened and outcome.closed
            assert outcome.order_ticket > 0
            assert outcome.position_ticket == outcome.order_ticket
            assert outcome.volume == 0.01  # symbol minimum
            assert outcome.entry_price > 0
            # the open request carried a server-side SL and bot magic
            opens = [r for r in fake.order_requests if "position" not in r]
            assert opens and opens[0]["sl"] > 0
            assert opens[0]["magic"] == 20260926
            # nothing left behind
            assert fake.positions == []
            assert "demo trade test passed" in outcome.summary()
        finally:
            gateway.stop()

    def test_notes_are_masked(self) -> None:
        fake = FakeMetaTrader5()
        gateway = connect_gateway(fake)
        try:
            outcome = run_demo_trade_test(gateway, "EURUSD")
            joined = " ".join(outcome.notes)
            assert "***678" in joined
            assert "secret" not in joined.lower()
        finally:
            gateway.stop()

    def test_custom_volume_within_cap(self) -> None:
        fake = FakeMetaTrader5()
        gateway = connect_gateway(fake)
        try:
            outcome = run_demo_trade_test(gateway, "EURUSD", volume=0.05)
            assert outcome.volume == 0.05
        finally:
            gateway.stop()


class TestFailures:
    def test_open_rejected(self) -> None:
        fake = FakeMetaTrader5()
        gateway = connect_gateway(fake)
        try:
            fake.fail_order_send = (TRADE_RETCODE_NO_MONEY, "not enough money")
            with pytest.raises(TradeError):
                run_demo_trade_test(gateway, "EURUSD")
            assert fake.positions == []  # nothing opened
        finally:
            gateway.stop()

    def test_close_rejected_leaves_position(self) -> None:
        fake = FakeMetaTrader5()
        gateway = connect_gateway(fake)
        try:
            # first order_send (open) succeeds, second (close) fails
            original_send = fake.order_send
            calls = {"count": 0}

            def flaky_send(request: dict) -> object:
                calls["count"] += 1
                if calls["count"] == 2:
                    return type(
                        "R",
                        (),
                        {
                            "retcode": TRADE_RETCODE_NO_MONEY,
                            "comment": "no money",
                            "order": 0,
                            "deal": 0,
                            "volume": 0.0,
                            "price": 0.0,
                        },
                    )
                return original_send(request)

            fake.order_send = flaky_send  # type: ignore[method-assign]
            with pytest.raises(TradeError):
                run_demo_trade_test(gateway, "EURUSD")
            assert len(fake.positions) == 1  # position still open — loud failure
        finally:
            gateway.stop()
