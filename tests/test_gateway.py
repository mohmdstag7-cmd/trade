"""MT5Gateway tests — threading contract, lifecycle, reconnect, snapshots."""

from __future__ import annotations

import threading
import time

import pytest

from app.mt5 import errors as mt5_errors
from app.mt5.gateway import MT5Gateway
from app.mt5.models import ConnectionState, ConnectRequest
from tests.fakes.fake_mt5 import (
    ACCOUNT_TRADE_MODE_REAL,
    TRADE_RETCODE_NO_MONEY,
    FakeAccountConfig,
    FakeMetaTrader5,
    FakeSymbolConfig,
)


def make_request(**overrides: object) -> ConnectRequest:
    values: dict[str, object] = {
        "login": 12345678,
        "password": "secret-password",
        "server": "FakeServer-Demo",
        "terminal_path": "",
    }
    values.update(overrides)
    return ConnectRequest(**values)  # type: ignore[arg-type]


@pytest.fixture
def fake() -> FakeMetaTrader5:
    return FakeMetaTrader5(symbols=[FakeSymbolConfig("EURUSD"), FakeSymbolConfig("EURUSD.m")])


@pytest.fixture
def gateway(fake: FakeMetaTrader5) -> MT5Gateway:
    gw = MT5Gateway(mt5_factory=lambda: fake, request_timeout_s=5.0)
    gw.start()
    yield gw
    gw.stop()


def wait_until(predicate, timeout_s: float = 5.0, poll_s: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return False


class TestLifecycle:
    def test_connect_returns_account_snapshot(
        self, gateway: MT5Gateway, fake: FakeMetaTrader5
    ) -> None:
        account = gateway.wait_for_result(gateway.connect(make_request()), "connect")
        assert account.login == 12345678
        assert account.server == "FakeServer-Demo"
        assert account.mode_name == "demo"
        assert gateway.state is ConnectionState.CONNECTED

    def test_credentials_reach_initialize(self, gateway: MT5Gateway, fake: FakeMetaTrader5) -> None:
        request = make_request(terminal_path=r"C:\MT5\terminal64.exe")
        gateway.wait_for_result(gateway.connect(request), "connect")
        assert fake.initialize_kwargs["login"] == 12345678
        assert fake.initialize_kwargs["password"] == "secret-password"
        assert fake.initialize_kwargs["path"] == r"C:\MT5\terminal64.exe"

    def test_all_mt5_calls_run_on_one_dedicated_thread(
        self, gateway: MT5Gateway, fake: FakeMetaTrader5
    ) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        gateway.wait_for_result(gateway.account_info(), "account_info")
        gateway.wait_for_result(gateway.terminal_info(), "terminal_info")
        gateway.wait_for_result(gateway.symbol_names(), "symbol_names")
        gateway.wait_for_result(gateway.rates_from_pos("EURUSD", "M15", 0, 5), "rates")
        gateway.wait_for_result(gateway.ping(), "ping")

        thread_ids = {tid for _name, tid in fake.calls}
        assert len(thread_ids) == 1, "every MT5 call must run on the gateway thread"

    def test_worker_thread_is_not_the_caller_thread(
        self, gateway: MT5Gateway, fake: FakeMetaTrader5
    ) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        worker_thread = fake.calls_by("initialize")[0]
        assert worker_thread != threading.get_ident()

    def test_auth_failure_maps_to_auth_error(self, fake: FakeMetaTrader5) -> None:
        fake.fail_initialize = (-6, "Terminal: authorization failed")
        gateway = MT5Gateway(mt5_factory=lambda: fake)
        gateway.start()
        try:
            with pytest.raises(mt5_errors.AuthError):
                gateway.wait_for_result(gateway.connect(make_request()), "connect")
            assert gateway.state is ConnectionState.DISCONNECTED
        finally:
            gateway.stop()

    def test_terminal_missing_maps_to_connection_lost(self, fake: FakeMetaTrader5) -> None:
        fake.fail_initialize = (-5, "Terminal: failed to connect")
        gateway = MT5Gateway(mt5_factory=lambda: fake)
        gateway.start()
        try:
            with pytest.raises(mt5_errors.ConnectionLostError):
                gateway.wait_for_result(gateway.connect(make_request()), "connect")
        finally:
            gateway.stop()

    def test_commands_fail_before_connect(self, gateway: MT5Gateway) -> None:
        with pytest.raises(mt5_errors.ConnectionLostError):
            gateway.wait_for_result(gateway.account_info(), "account_info")

    def test_disconnect_closes_session(self, gateway: MT5Gateway, fake: FakeMetaTrader5) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        gateway.wait_for_result(gateway.disconnect(), "disconnect")
        assert gateway.state is ConnectionState.DISCONNECTED
        assert "shutdown" in [name for name, _tid in fake.calls]

    def test_submit_after_stop_fails_fast(self, fake: FakeMetaTrader5) -> None:
        gateway = MT5Gateway(mt5_factory=lambda: fake)
        gateway.start()
        gateway.stop()
        future = gateway.account_info()
        with pytest.raises(mt5_errors.MT5Error, match="stopped"):
            future.result(timeout=1.0)

    def test_stop_timeout_keeps_worker_and_blocks_second_start(self) -> None:
        """A stop() that times out (slow call draining) must NOT lose the
        thread reference — otherwise start() spawns a second worker that
        races the still-alive first one (duplicate started / ghost
        session-closed lines, concurrent MT5 module access)."""
        entered = threading.Event()
        release = threading.Event()

        class BlockingMT5:
            initialized = False

            def initialize(self, **_kwargs: object) -> bool:
                entered.set()
                release.wait(timeout=5.0)
                self.initialized = True
                return True

            def login(self, *_args: object, **_kwargs: object) -> bool:
                return True

            def shutdown(self) -> None:
                self.initialized = False

            def last_error(self) -> tuple[int, str]:
                return (0, "")

            def account_info(self) -> None:
                return None

        blocking = BlockingMT5()
        gateway = MT5Gateway(mt5_factory=lambda: blocking, request_timeout_s=5.0)
        gateway.start()
        try:
            draining = gateway._thread
            assert draining is not None
            gateway.connect(make_request())
            assert entered.wait(timeout=5.0), "worker never reached initialize()"

            gateway.stop(timeout_s=0.2)  # join times out — call still draining
            assert gateway._thread is draining, "stop() dropped the draining thread"
            assert gateway._thread.is_alive()

            gateway.start()  # must be a no-op, never a second worker
            assert gateway._thread is draining
            assert sum(1 for t in threading.enumerate() if t is draining) == 1
        finally:
            release.set()
        assert wait_until(lambda: not draining.is_alive()), "worker never exited"

        gateway.start()  # recovery once the drained thread is gone
        fresh = gateway._thread
        assert fresh is not None and fresh is not draining and fresh.is_alive()
        gateway.stop()
        assert gateway._thread is None

    def test_state_history_recorded(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        gateway.wait_for_result(gateway.disconnect(), "disconnect")
        values = [entry.split("@")[0] for entry in gateway.stats.state_history]
        assert values[:3] == ["connecting", "connected", "disconnected"]


class TestDataCommands:
    def test_terminal_snapshot(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        terminal = gateway.wait_for_result(gateway.terminal_info(), "terminal_info")
        assert terminal.build == 4885
        assert terminal.connected is True

    def test_symbol_names(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        names = gateway.wait_for_result(gateway.symbol_names(), "symbol_names")
        assert "EURUSD.m" in names

    def test_symbol_info_snapshot(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        info = gateway.wait_for_result(gateway.symbol_info("EURUSD.m"), "symbol_info")
        assert info.name == "EURUSD.m"
        assert info.volume_min == 0.01
        assert info.fill_mode == 2

    def test_symbol_info_missing_raises(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        with pytest.raises(mt5_errors.TerminalError, match="not found"):
            gateway.wait_for_result(gateway.symbol_info("NOPE"), "symbol_info")

    def test_tick_snapshot(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        tick = gateway.wait_for_result(gateway.tick("EURUSD"), "tick")
        assert tick.ask > tick.bid > 0

    def test_rates_conversion(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        bars = gateway.wait_for_result(gateway.rates_from_pos("EURUSD", "M15", 0, 10), "rates")
        assert len(bars) == 10
        assert bars[0].time > bars[1].time  # newest first
        assert bars[0].tick_volume == 100
        assert bars[0].real_volume == 0

    def test_positions_empty_returns_tuple(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        positions = gateway.wait_for_result(gateway.positions(), "positions")
        assert positions == ()

    def test_ping_returns_positive_latency(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        latency = gateway.wait_for_result(gateway.ping(), "ping")
        assert latency >= 0.0


class TestOrdering:
    def test_order_send_success(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        request = {
            "action": 1,
            "symbol": "EURUSD",
            "volume": 0.01,
            "type": 0,
            "price": 1.08010,
            "sl": 1.05849,
            "tp": 0.0,
            "deviation": 50,
            "magic": 20260926,
            "comment": "gateway test",
            "type_time": 0,
            "type_filling": 1,
        }
        result = gateway.wait_for_result(gateway.order_send(request), "order_send")
        assert result.succeeded
        assert result.order_ticket > 0
        positions = gateway.wait_for_result(gateway.positions(), "positions")
        assert len(positions) == 1
        assert positions[0].magic == 20260926

    def test_order_send_rejection_raises_trade_error(self, fake: FakeMetaTrader5) -> None:
        fake.fail_order_send = (TRADE_RETCODE_NO_MONEY, "not enough money")
        gateway = MT5Gateway(mt5_factory=lambda: fake)
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            with pytest.raises(mt5_errors.TradeError) as excinfo:
                gateway.wait_for_result(
                    gateway.order_send({"action": 1, "symbol": "EURUSD"}), "order_send"
                )
            assert excinfo.value.code == TRADE_RETCODE_NO_MONEY
            assert "margin" in str(excinfo.value).lower()
        finally:
            gateway.stop()

    def test_order_check_passes(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        result = gateway.wait_for_result(
            gateway.order_check({"action": 1, "symbol": "EURUSD", "volume": 0.01}),
            "order_check",
        )
        assert result.succeeded


class TestReconnect:
    def test_connection_loss_triggers_reconnect(
        self, fake: FakeMetaTrader5, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.mt5.gateway._IDLE_POLL_S", 0.02)
        gateway = MT5Gateway(
            mt5_factory=lambda: fake,
            reconnect_initial_s=0.05,
            reconnect_max_s=0.2,
        )
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            # first data call after `fail_after` calls simulates the drop
            fake.fail_calls_with = (10031, "no connection")
            fake.fail_after = 0
            with pytest.raises(mt5_errors.ConnectionLostError):
                gateway.wait_for_result(gateway.account_info(), "account_info")
            assert gateway.state is ConnectionState.RECONNECTING

            # heal the fake: next initialize succeeds again
            fake.fail_calls_with = None
            assert wait_until(lambda: gateway.state is ConnectionState.CONNECTED, 5.0)
            assert gateway.stats.reconnects >= 1
        finally:
            gateway.stop()

    def test_auto_reconnect_disabled_teardowns(self, fake: FakeMetaTrader5) -> None:
        gateway = MT5Gateway(mt5_factory=lambda: fake, auto_reconnect=False)
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            fake.fail_calls_with = (10031, "no connection")
            fake.fail_after = 0
            with pytest.raises(mt5_errors.ConnectionLostError):
                gateway.wait_for_result(gateway.account_info(), "account_info")
            assert gateway.state is ConnectionState.DISCONNECTED
        finally:
            gateway.stop()

    def test_commands_fail_fast_while_reconnecting(self, fake: FakeMetaTrader5) -> None:
        gateway = MT5Gateway(
            mt5_factory=lambda: fake, reconnect_initial_s=10.0, reconnect_max_s=10.0
        )
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            fake.fail_calls_with = (10031, "no connection")
            fake.fail_after = 0
            with pytest.raises(mt5_errors.ConnectionLostError):
                gateway.wait_for_result(gateway.account_info(), "account_info")
            # while the (slow) backoff timer runs, new commands must not hang
            with pytest.raises(mt5_errors.ConnectionLostError):
                gateway.wait_for_result(gateway.account_info(), "account_info", 0.2)
        finally:
            gateway.stop()

    def test_backoff_grows(self, fake: FakeMetaTrader5) -> None:
        gateway = MT5Gateway(
            mt5_factory=lambda: fake,
            reconnect_initial_s=0.01,
            reconnect_max_s=1.0,
            reconnect_factor=4.0,
        )
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            fake.fail_initialize = (
                -5,
                "Terminal: failed to connect",
            )  # reconnect attempts fail too
            fake.fail_calls_with = (10031, "no connection")
            fake.fail_after = 0
            with pytest.raises(mt5_errors.ConnectionLostError):
                gateway.wait_for_result(gateway.account_info(), "account_info")
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and gateway.stats.reconnects < 2:
                time.sleep(0.01)
            assert gateway.stats.reconnects >= 1
            assert gateway._reconnect_delay_s >= 0.04  # 0.01 * factor^2
        finally:
            gateway.stop()


class TestStatsAndHeartbeat:
    def test_counters(self, gateway: MT5Gateway) -> None:
        gateway.wait_for_result(gateway.connect(make_request()), "connect")
        gateway.wait_for_result(gateway.ping(), "ping")
        assert gateway.stats.commands_total >= 2
        assert gateway.stats.commands_failed == 0
        assert gateway.stats.last_latency_ms >= 0.0

    def test_heartbeat_called_from_worker(self, fake: FakeMetaTrader5) -> None:
        beats: list[int] = []
        gateway = MT5Gateway(
            mt5_factory=lambda: fake,
            heartbeat=lambda: beats.append(threading.get_ident()),
        )
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            assert wait_until(lambda: len(beats) >= 3, 3.0)
            worker = fake.calls_by("initialize")[0]
            assert all(beat == worker for beat in beats)
        finally:
            gateway.stop()

    def test_state_callback_fires(self, fake: FakeMetaTrader5) -> None:
        seen: list[ConnectionState] = []
        gateway = MT5Gateway(mt5_factory=lambda: fake, on_state_change=seen.append)
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            assert ConnectionState.CONNECTED in seen
            assert seen[0] is ConnectionState.CONNECTING
        finally:
            gateway.stop()

    def test_real_account_connects_and_reports_real(self) -> None:
        fake = FakeMetaTrader5(account=FakeAccountConfig(trade_mode=ACCOUNT_TRADE_MODE_REAL))
        gateway = MT5Gateway(mt5_factory=lambda: fake)
        gateway.start()
        try:
            account = gateway.wait_for_result(gateway.connect(make_request()), "connect")
            assert account.is_real
            assert not account.is_demo
        finally:
            gateway.stop()
