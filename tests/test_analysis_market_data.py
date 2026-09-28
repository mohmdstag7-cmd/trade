"""Tests for the market data manager (ingest, sanity, evaluable gate)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC

from app.analysis.broker_time import BrokerClock
from app.analysis.market_data import MarketDataManager, SanityIssueKind
from app.core.timeframes import Timeframe
from app.mt5.models import RateBar

TF = Timeframe.M15
TF_SEC = TF.seconds


def bar(
    time: int, close: float, high: float | None = None, low: float | None = None, volume: int = 100
) -> RateBar:
    return RateBar(
        time=time,
        open=close,
        high=high if high is not None else close + 1.0,
        low=low if low is not None else close - 1.0,
        close=close,
        tick_volume=volume,
        spread=10,
    )


def steady(
    symbol: str, start: int, count: int, price: float = 100.0, volume: int = 100
) -> list[RateBar]:
    return [
        bar(start + i * TF_SEC, price, price + 0.5, price - 0.5, volume=volume)
        for i in range(count)
    ]


class TestIngest:
    def test_first_ingest_builds_series(self) -> None:
        mgr = MarketDataManager()
        issues = mgr.ingest("EURUSD", TF, steady("EURUSD", 1_000_000, 50))
        assert issues == []
        assert len(mgr.series("EURUSD", TF).bars) == 50

    def test_incremental_append_dedupes(self) -> None:
        mgr = MarketDataManager()
        base = steady("EURUSD", 1_000_000, 50)
        mgr.ingest("EURUSD", TF, base)
        newer = [*base[-10:], bar(1_000_000 + 50 * TF_SEC, 101.0)]
        issues = mgr.ingest("EURUSD", TF, newer)
        assert issues == []
        assert len(mgr.series("EURUSD", TF).bars) == 51

    def test_forming_bar_excluded(self) -> None:
        mgr = MarketDataManager()
        bars = steady("EURUSD", 1_000_000, 50)
        forming_open = bars[-1].time + TF_SEC
        mgr.ingest("EURUSD", TF, [*bars, bar(forming_open, 102.0)], forming_open_time=forming_open)
        times = mgr.series("EURUSD", TF).times()
        assert forming_open not in times
        assert len(times) == 50

    def test_max_bars_bounded(self) -> None:
        mgr = MarketDataManager(max_bars=100)
        mgr.ingest("EURUSD", TF, steady("EURUSD", 1_000_000, 300))
        assert len(mgr.series("EURUSD", TF).bars) == 100

    def test_multiple_symbols_isolated(self) -> None:
        mgr = MarketDataManager()
        mgr.ingest("EURUSD", TF, steady("EURUSD", 1_000_000, 10))
        mgr.ingest("XAUUSD", TF, steady("XAUUSD", 1_000_000, 20))
        assert len(mgr.series("EURUSD", TF).bars) == 10
        assert len(mgr.series("XAUUSD", TF).bars) == 20
        assert mgr.symbols() == ["EURUSD", "XAUUSD"]


class TestSanity:
    def test_missing_bars_detected(self) -> None:
        mgr = MarketDataManager()
        mgr.ingest("EURUSD", TF, steady("EURUSD", 1_000_000, 10))
        # next bar skips 3 slots
        issues = mgr.ingest("EURUSD", TF, [bar(1_000_000 + 14 * TF_SEC, 100.5)])
        kinds = [i.kind for i in issues]
        assert SanityIssueKind.MISSING_BARS in kinds
        assert mgr.evaluable("EURUSD", TF, issues) is True  # gaps do not block

    def test_weekend_gap_is_not_missing_bars(self) -> None:
        mgr = MarketDataManager()
        # 2024-03-15 is a Friday; 2024-03-18 is the next Monday (UTC)
        from datetime import datetime

        fri = int(datetime(2024, 3, 15, 21, 0, tzinfo=UTC).timestamp())
        mon = int(datetime(2024, 3, 18, 1, 0, tzinfo=UTC).timestamp())
        mgr.ingest("EURUSD", TF, steady("EURUSD", fri - 9 * TF_SEC, 10))
        issues = mgr.ingest("EURUSD", TF, [bar(mon, 100.5)])
        kinds = [i.kind for i in issues]
        assert SanityIssueKind.WEEKEND_GAP in kinds
        assert SanityIssueKind.MISSING_BARS not in kinds

    def test_zero_volume_flagged(self) -> None:
        mgr = MarketDataManager()
        issues = mgr.ingest("EURUSD", TF, steady("EURUSD", 1_000_000, 5, volume=0))
        assert SanityIssueKind.ZERO_VOLUME in [i.kind for i in issues]

    def test_spike_on_newest_bar_blocks_evaluation(self) -> None:
        mgr = MarketDataManager(spike_atr_factor=4.0)
        mgr.ingest("EURUSD", TF, steady("EURUSD", 1_000_000, 60))
        # huge range bar right after
        spike = bar(
            1_000_000 + 60 * TF_SEC,
            100.0,
            high=140.0,
            low=60.0,
        )
        issues = mgr.ingest("EURUSD", TF, [spike])
        assert SanityIssueKind.SPIKE in [i.kind for i in issues]
        assert mgr.evaluable("EURUSD", TF, issues) is False

    def test_spike_on_old_bar_does_not_block_newest(self) -> None:
        mgr = MarketDataManager(spike_atr_factor=4.0)
        base = steady("EURUSD", 1_000_000, 60)
        mgr.ingest("EURUSD", TF, base)
        spike = bar(base[-1].time + TF_SEC, 100.0, high=140.0, low=60.0)
        mgr.ingest("EURUSD", TF, [spike])
        normal = bar(spike.time + TF_SEC, 100.0)
        issues2 = mgr.ingest("EURUSD", TF, [normal])
        assert issues2 == []
        assert mgr.evaluable("EURUSD", TF, issues2) is True

    def test_time_jump_blocks_evaluation(self) -> None:
        mgr = MarketDataManager()
        # non-contiguous history: bars at slots 0-9 then a gap, then slot 15
        base = steady("EURUSD", 1_000_000, 10)
        base.append(bar(1_000_000 + 15 * TF_SEC, 100.0))
        mgr.ingest("EURUSD", TF, base)
        # a new bar landing BEFORE the newest one (but unseen) = time jump
        backwards = bar(1_000_000 + 12 * TF_SEC, 100.0)
        issues = mgr.ingest("EURUSD", TF, [backwards])
        assert SanityIssueKind.TIME_JUMP in [i.kind for i in issues]
        # The poisoned bar is dropped before entering the series, so the
        # newest CLOSED bar (slot 15) is untouched and stays evaluable.
        assert mgr.evaluable("EURUSD", TF, issues) is True
        # the jump bar is DROPPED — the closed series stays monotonic
        times = [b.time for b in mgr.series("EURUSD", TF).bars]
        assert times == sorted(times)
        assert (1_000_000 + 12 * TF_SEC) not in times

    def test_evaluable_false_when_no_bars(self) -> None:
        mgr = MarketDataManager()
        assert mgr.evaluable("NOPE", TF, []) is False


class TestStaleTick:
    def test_stale_tick_detected(self) -> None:
        mgr = MarketDataManager()
        mgr.ingest("EURUSD", TF, steady("EURUSD", 1_000_000, 5))
        t0 = 2_000_000
        assert mgr.mark_tick("EURUSD", t0) is None
        issue = mgr.mark_tick("EURUSD", t0 + 4 * TF_SEC)
        assert issue is not None
        assert issue.kind == SanityIssueKind.STALE_TICK

    def test_normal_tick_ok(self) -> None:
        mgr = MarketDataManager()
        mgr.ingest("EURUSD", TF, steady("EURUSD", 1_000_000, 5))
        assert mgr.mark_tick("EURUSD", 2_000_000) is None
        assert mgr.mark_tick("EURUSD", 2_000_000 + 60) is None

    def test_mark_tick_unknown_symbol(self) -> None:
        mgr = MarketDataManager()
        assert mgr.mark_tick("UNKNOWN", 1) is None


class TestBarSourceProtocol:
    def test_protocol_accepts_callable(self) -> None:
        def source(symbol: str, tf: Timeframe, count: int) -> Iterable[RateBar]:
            return steady(symbol, 1_000_000, count)

        bars = source("EURUSD", TF, 10)
        mgr = MarketDataManager()
        assert len(list(bars)) == 10
        assert mgr.ingest("EURUSD", TF, bars) == []


class TestClockIntegration:
    def test_manager_uses_injected_clock(self) -> None:
        clock = BrokerClock(utc_now_fn=lambda: 1_700_000_000.0)
        clock.sample(1_700_000_000.0 + 3 * 3600)
        mgr = MarketDataManager(clock=clock)
        mgr.ingest("EURUSD", TF, steady("EURUSD", 1_000_000, 5))
        assert mgr.clock.offset_minutes == 180
