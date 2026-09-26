"""Tests for the market analysis orchestrator (SPEC C3 driver)."""

from __future__ import annotations

from concurrent.futures import Future
from datetime import UTC, datetime

import pytest

from app.analysis.service import MarketAnalysisService
from app.core.timeframes import Timeframe
from app.mt5.models import ConnectionState, SymbolSnapshot, TickSnapshot

TF_SECONDS = {
    Timeframe.M15: 900,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
    Timeframe.W1: 604800,
}


def make_bars(tf: Timeframe, start: float, step: float, count: int) -> tuple:
    base = int(datetime(2024, 3, 1, tzinfo=UTC).timestamp())
    bars = []
    for i in range(count):
        close = start + step * i
        t = base + i * TF_SECONDS[tf]
        bars.append(_bar(t, close))
    return tuple(bars)


def _bar(t: int, close: float):
    from app.mt5.models import RateBar

    return RateBar(
        time=t,
        open=close,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        tick_volume=100 + (t % 7),
        spread=10,
    )


def _done(value: object) -> Future:
    future: Future = Future()
    future.set_result(value)
    return future


class FakeGateway:
    """Instant-completed futures over synthetic trending markets."""

    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected
        self.fetch_calls = 0

    def state(self) -> ConnectionState:
        return ConnectionState.CONNECTED if self.connected else ConnectionState.DISCONNECTED

    def rates_from_pos(self, symbol: str, timeframe: str, start_pos: int, count: int):
        self.fetch_calls += 1
        tf = Timeframe(timeframe)
        if tf is Timeframe.M15:
            return _done(make_bars(tf, 1.0800, 0.0002, min(count, 70)))
        if tf is Timeframe.H1:
            return _done(make_bars(tf, 1.0800, 0.0005, min(count, 70)))
        if tf is Timeframe.H4:
            return _done(make_bars(tf, 1.0800, 0.0012, min(count, 70)))
        if tf is Timeframe.D1:
            return _done(make_bars(tf, 1.0800, 0.0030, min(count, 70)))
        return _done(make_bars(tf, 1.0800, 0.0050, min(count, 10)))

    def tick(self, symbol: str) -> Future:
        bars = make_bars(Timeframe.M15, 1.0800, 0.0002, 70)
        last = bars[-1].time
        return _done(
            TickSnapshot(
                time=last + TF_SECONDS[Timeframe.M15], bid=1.0810, ask=1.0812, last=0.0, volume=0.0
            )
        )

    def symbol_info(self, symbol: str) -> Future:
        return _done(
            SymbolSnapshot(
                name=symbol,
                description="fake",
                visible=True,
                trade_mode=0,
                digits=5,
                point=0.00001,
                spread=12,
                volume_min=0.01,
                volume_max=100.0,
                volume_step=0.01,
                trade_tick_value=1.0,
                trade_tick_size=0.00001,
                trade_contract_size=100000.0,
                currency_profit="USD",
            )
        )


class TestServicePipeline:
    def test_offline_service_never_builds_snapshot(self) -> None:
        service = MarketAnalysisService(None, watched=("EURUSD",))
        service.refresh_now()
        assert service.poll() is None
        assert service.snapshot is None

    def test_disconnected_gateway_skips_submission(self) -> None:
        gateway = FakeGateway(connected=False)
        service = MarketAnalysisService(gateway, watched=("EURUSD",))
        service.refresh_now()
        assert gateway.fetch_calls == 0
        assert not service.has_pending()

    def test_full_pipeline_builds_snapshot(self) -> None:
        gateway = FakeGateway()
        service = MarketAnalysisService(gateway, watched=("EURUSD", "GBPUSD", "XAUUSD"))
        service.refresh_now()
        snapshot = service.poll()
        assert snapshot is not None
        assert gateway.fetch_calls == 5 * 3  # 5 timeframes x 3 symbols
        assert len(snapshot.symbols) == 3
        entry = snapshot.by_symbol("EURUSD")
        assert entry is not None
        assert entry.digits == 5
        assert entry.card is not None
        assert entry.card.lines  # analysis card has content
        assert entry.trend.vectors  # trend matrix assessed
        assert entry.levels  # key levels extracted
        assert entry.broker_offset in ("UTC", "UTC+00:00") or entry.broker_offset.startswith("UTC")
        assert snapshot.scan
        assert snapshot.correlation is not None
        assert snapshot.strength

    def test_poll_again_without_new_bars_reuses_snapshot(self) -> None:
        gateway = FakeGateway()
        service = MarketAnalysisService(gateway, watched=("EURUSD",))
        service.refresh_now()
        first = service.poll()
        assert first is not None
        second = service.poll()  # no refresh_now → nothing pending → same object
        assert second is first

    def test_clock_learns_offset_from_ticks(self) -> None:
        gateway = FakeGateway()
        service = MarketAnalysisService(gateway, watched=("EURUSD",))
        service.refresh_now()
        service.poll()
        assert service.clock.detected is True
        assert service.clock.offset_minutes != 0

    def test_spread_recorded(self) -> None:
        gateway = FakeGateway()
        service = MarketAnalysisService(gateway, watched=("EURUSD",))
        service.refresh_now()
        service.poll()
        snap = service.snapshot.by_symbol("EURUSD")
        assert snap is not None
        assert snap.spread_points == pytest.approx(20.0)
        assert snap.spread_typical is None  # cold start: only one sample

    def test_calendar_event_feeds_card(self) -> None:
        from datetime import timedelta

        from app.calendar.events import EconomicEvent, EventStore

        gateway = FakeGateway()
        store = EventStore()
        soon = datetime.now(tz=UTC) + timedelta(minutes=45)
        store.add(EconomicEvent(time_utc=soon, currency="USD", title="CPI y/y", impact="high"))
        service = MarketAnalysisService(gateway, watched=("EURUSD",), calendar_store=store)
        service.refresh_now()
        service.poll()
        snap = service.snapshot.by_symbol("EURUSD")
        assert snap is not None
        assert snap.card.next_event is not None
        assert snap.card.next_event.title == "CPI y/y"
        assert snap.card.verdict == "wait"  # high-impact risk window


class FakeResolvingGateway(FakeGateway):
    """FakeGateway that also lists decorated broker symbols (resolution path)."""

    def __init__(
        self,
        *,
        connected: bool = True,
        available: tuple[str, ...] = ("EURUSD.m", "GBPUSD.m", "XAUUSD.m"),
    ) -> None:
        super().__init__(connected=connected)
        self.available = available
        self.selected: list[str] = []
        self.fetched: list[str] = []
        self.names_calls = 0

    def symbol_names(self) -> Future:
        self.names_calls += 1
        return _done(self.available)

    def select_symbol(self, symbol: str) -> Future:
        self.selected.append(symbol)
        return _done(True)

    def rates_from_pos(self, symbol: str, timeframe: str, start_pos: int, count: int):
        self.fetched.append(symbol)
        return super().rates_from_pos(symbol, timeframe, start_pos, count)

    def tick(self, symbol: str) -> Future:
        self.fetched.append(symbol)
        return super().tick(symbol)

    def symbol_info(self, symbol: str) -> Future:
        self.fetched.append(symbol)
        return super().symbol_info(symbol)


class TestBrokerSymbolResolution:
    """Market data must use broker-decorated names (EURUSD.m), never crash
    or stay silently empty when the broker decorates or drops a symbol."""

    def _drive(self, service: MarketAnalysisService):
        service.refresh_now()  # submits symbol_names
        service.poll()  # resolves names, submits symbol_select
        service.refresh_now()  # drains selects, submits fetch batches
        return service.poll()  # drains batches, computes snapshot

    def test_suffix_resolved_and_used_for_fetches(self) -> None:
        gateway = FakeResolvingGateway()
        service = MarketAnalysisService(gateway, watched=("EURUSD", "GBPUSD", "XAUUSD"))

        snapshot = self._drive(service)

        assert snapshot is not None
        assert gateway.selected == ["EURUSD.m", "GBPUSD.m", "XAUUSD.m"]
        assert "EURUSD.m" in gateway.fetched
        assert "EURUSD" not in gateway.fetched
        assert snapshot.unresolved == ()
        assert {s.symbol for s in snapshot.symbols} == {"EURUSD", "GBPUSD", "XAUUSD"}

    def test_unresolved_symbol_surfaced_not_fatal(self) -> None:
        gateway = FakeResolvingGateway(available=("EURUSD.m", "GBPUSD.m"))
        service = MarketAnalysisService(gateway, watched=("EURUSD", "XAUUSD"))

        snapshot = self._drive(service)

        assert snapshot is not None
        assert snapshot.unresolved == ("XAUUSD",)
        assert {s.symbol for s in snapshot.symbols} == {"EURUSD"}
        assert gateway.fetch_calls > 0  # the resolvable symbol still flows

    def test_state_property_shape_blocks_offline(self) -> None:
        # the real gateway exposes `state` as a @property (enum instance),
        # NOT a method — the service must handle that shape too.
        gateway = FakeResolvingGateway()
        gateway.state = ConnectionState.DISCONNECTED
        service = MarketAnalysisService(gateway, watched=("EURUSD",))

        service.refresh_now()

        assert gateway.fetch_calls == 0
        assert service.poll() is None  # stays fully offline

        gateway.state = ConnectionState.CONNECTED
        snapshot = self._drive(service)
        assert snapshot is not None
        assert gateway.fetch_calls > 0

    def test_invalidate_on_disconnect_reresolves(self) -> None:
        gateway = FakeResolvingGateway()
        service = MarketAnalysisService(gateway, watched=("EURUSD", "GBPUSD", "XAUUSD"))
        self._drive(service)
        assert gateway.names_calls == 1

        service.invalidate_symbols()
        assert service.snapshot is None
        assert not service.manager.symbols()

        snapshot = self._drive(service)
        assert gateway.names_calls == 2  # fresh resolution pass
        assert snapshot is not None
        assert {s.symbol for s in snapshot.symbols} == {"EURUSD", "GBPUSD", "XAUUSD"}

    def test_dead_symbol_parked_after_repeated_failures(self) -> None:
        class DeadSymbolGateway(FakeResolvingGateway):
            def rates_from_pos(self, symbol: str, timeframe: str, start_pos: int, count: int):
                if symbol.endswith(".dead"):
                    future: Future = Future()
                    future.set_exception(RuntimeError("no history for symbol"))
                    return future
                return super().rates_from_pos(symbol, timeframe, start_pos, count)

        gateway = DeadSymbolGateway(available=("EURUSD.dead", "GBPUSD.m"))
        service = MarketAnalysisService(gateway, watched=("EURUSD", "GBPUSD"))

        snapshot = None
        for _ in range(5):
            service.refresh_now()
            snapshot = service.poll()

        assert snapshot is not None
        assert "EURUSD" in snapshot.unresolved  # parked after 3 failures
        assert {s.symbol for s in snapshot.symbols} == {"GBPUSD"}
