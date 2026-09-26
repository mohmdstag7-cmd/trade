"""Regression tests for the v0.6.5 diagnostics batch.

Covers:
- named gateways: ``gateway[<name>]: …`` log tags disambiguate the shared
  gateway from one-shot probes in user-supplied logs;
- GatewayTimeoutError("connect", …) carries the cold-terminal-start hint;
- ConnectionProbe borrowing the shared gateway never re-initializes a live
  session, restores idle state, and never stops the shared gateway;
- the market pipeline logs its resolution verdict and per-symbol first
  data-ready batch (the Market page used to be a silent black box).
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from loguru import logger

from app.analysis.service import MarketAnalysisService
from app.mt5.diagnostics import ConnectionProbe
from app.mt5.errors import GatewayTimeoutError
from app.mt5.gateway import MT5Gateway
from app.mt5.models import ConnectionState, ConnectRequest
from tests.fakes.fake_mt5 import FakeMetaTrader5, FakeSymbolConfig
from tests.test_analysis_service import FakeResolvingGateway


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
@pytest.fixture
def log_capture() -> Any:
    records: list[Any] = []
    handler_id = logger.add(records.append, level="INFO")
    yield records
    logger.remove(handler_id)


def _messages(records: list[Any]) -> list[str]:
    # a plain-callable loguru sink receives the fully formatted message string
    return [str(record) for record in records]


def _request() -> ConnectRequest:
    return ConnectRequest(
        login=12345678,
        password="secret",
        server="FakeServer-Demo",
        terminal_path="",
    )


def _make_fake() -> FakeMetaTrader5:
    return FakeMetaTrader5(symbols=[FakeSymbolConfig("EURUSD.m")])


def _poll_until_finished(probe: ConnectionProbe, timeout_s: float = 10.0):
    deadline = time.monotonic() + timeout_s
    state = probe.poll()
    while not state.finished and time.monotonic() < deadline:
        time.sleep(0.02)
        state = probe.poll()
    return state


def _shared_gateway(fake: FakeMetaTrader5 | None = None) -> MT5Gateway:
    gateway = MT5Gateway(
        mt5_factory=lambda: fake if fake is not None else _make_fake(),
        request_timeout_s=10.0,
        name="shared",
    )
    gateway.start()
    return gateway


# --------------------------------------------------------------------------- #
# named gateways                                                               #
# --------------------------------------------------------------------------- #
class TestGatewayNames:
    def test_custom_name_in_logs(self, log_capture: Any) -> None:
        gw = MT5Gateway(mt5_factory=lambda: object(), name="testy")
        gw.start()
        gw.stop()
        texts = _messages(log_capture)
        assert any("gateway[testy]: started" in t for t in texts)
        assert any("gateway[testy]: stopped" in t for t in texts)

    def test_default_name_is_gw(self, log_capture: Any) -> None:
        gw = MT5Gateway(mt5_factory=lambda: object())
        gw.start()
        gw.stop()
        assert any("gateway[gw]: started" in t for t in _messages(log_capture))


# --------------------------------------------------------------------------- #
# connect timeout hint                                                         #
# --------------------------------------------------------------------------- #
class TestConnectTimeoutHint:
    def test_connect_timeout_mentions_terminal_start(self) -> None:
        err = GatewayTimeoutError("connect", 30.0)
        assert "terminal may still be starting" in str(err)

    def test_other_operations_get_no_hint(self) -> None:
        err = GatewayTimeoutError("rates_from_pos", 5.0)
        assert "terminal may still be starting" not in str(err)
        assert "timed out after 5.0s" in str(err)


# --------------------------------------------------------------------------- #
# probe borrowing the shared gateway                                           #
# --------------------------------------------------------------------------- #
class TestProbeBorrowedGateway:
    def test_connected_session_inspected_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gw = _shared_gateway()
        try:
            gw.wait_for_result(gw.connect(_request()), "connect", 10.0)
            stop_calls: list[str] = []
            monkeypatch.setattr(gw, "stop", lambda *a: stop_calls.append("stop"))

            probe = ConnectionProbe(_request(), None, shared_gateway=gw)
            probe.start()
            state = _poll_until_finished(probe)

            assert state.finished
            assert state.ok
            # session untouched: no disconnect step, still connected, never stopped
            assert "session closed" not in state.detail
            assert gw.state is ConnectionState.CONNECTED
            assert stop_calls == []
            # read-only steps still collected real detail (account/terminal/symbols)
            assert "balance 10000.00 USD" in state.detail
            assert "EURUSD.m" in state.detail
        finally:
            gw.stop()

    def test_idle_session_full_cycle_restores_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gw = _shared_gateway()
        try:
            stop_calls: list[str] = []
            monkeypatch.setattr(gw, "stop", lambda *a: stop_calls.append("stop"))

            probe = ConnectionProbe(_request(), None, shared_gateway=gw)
            probe.start()
            state = _poll_until_finished(probe)

            assert state.finished
            assert state.ok
            # full cycle ran: connect … disconnect, state restored to idle
            assert "session closed" in state.detail
            assert gw.state is ConnectionState.DISCONNECTED
            # … but the shared gateway itself was never stopped
            assert stop_calls == []
        finally:
            gw.stop()

    def test_owned_probe_still_stops_its_own_gateway(self) -> None:
        probe = ConnectionProbe(_request(), _make_fake)
        probe.start()
        state = _poll_until_finished(probe)
        assert state.finished
        assert state.ok
        assert "session closed" in state.detail


# --------------------------------------------------------------------------- #
# market pipeline observability                                                #
# --------------------------------------------------------------------------- #
class TestMarketPipelineLogs:
    def test_resolution_and_data_ready_logged(self, log_capture: Any) -> None:
        gateway = FakeResolvingGateway()
        service = MarketAnalysisService(gateway, watched=("EURUSD",))
        service.refresh_now()  # submits symbol_names
        service.poll()  # completes resolution, submits select futures
        service.poll()  # select futures done → batches submitted + drained

        texts = _messages(log_capture)
        assert any("analysis: resolved 1/1 watched symbols (EURUSD→EURUSD.m)" in t for t in texts)
        assert any("analysis: EURUSD data ready" in t for t in texts)

    def test_data_ready_announced_once_per_session(self, log_capture: Any) -> None:
        gateway = FakeResolvingGateway()
        service = MarketAnalysisService(gateway, watched=("EURUSD",))
        for _ in range(2):  # two refresh cycles in one session
            service.refresh_now()
            service.poll()
            service.poll()

        ready_logs = [t for t in _messages(log_capture) if "data ready" in t]
        assert len(ready_logs) == 1

    def test_invalidate_reannounces_after_reconnect(self, log_capture: Any) -> None:
        gateway = FakeResolvingGateway()
        service = MarketAnalysisService(gateway, watched=("EURUSD",))
        service.refresh_now()
        service.poll()
        service.poll()
        service.invalidate_symbols()  # disconnect bookkeeping
        service.refresh_now()
        service.poll()
        service.poll()

        ready_logs = [t for t in _messages(log_capture) if "data ready" in t]
        assert len(ready_logs) == 2

    def test_unresolved_symbols_logged_as_warning(self, log_capture: Any) -> None:
        gateway = FakeResolvingGateway(available=("EURUSD.m",))
        service = MarketAnalysisService(gateway, watched=("EURUSD", "XAUUSD"))
        service.refresh_now()
        service.poll()

        texts = _messages(log_capture)
        assert any("analysis: unresolved symbols: XAUUSD" in t for t in texts)
