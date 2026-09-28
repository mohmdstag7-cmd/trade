"""Tests for key levels, volatility and sessions (SPEC C3.3-C3.5)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.analysis.levels import (
    LevelKind,
    cluster_levels,
    nearest_levels,
    prev_day_levels,
    prev_week_levels,
    round_numbers,
    session_levels,
)
from app.analysis.sessions import SessionClock
from app.analysis.structure import Swing, SwingKind
from app.analysis.volatility import (
    Regime,
    adr,
    adr_used_pct,
    atr_percentile,
    regime_from_percentile,
    volatility_snapshot,
)
from app.mt5.models import RateBar


def d1_bar(day: int, high: float, low: float, close: float) -> RateBar:
    base = int(datetime(2024, 3, day, 0, 0, tzinfo=UTC).timestamp())
    return RateBar(time=base, open=low, high=high, low=low, close=close, tick_volume=1000, spread=5)


class TestClusterLevels:
    def test_clusters_merge_within_tolerance(self) -> None:
        swings = [
            Swing(SwingKind.HIGH, 0, 100.0, 2),
            Swing(SwingKind.LOW, 5, 100.2, 7),
            Swing(SwingKind.HIGH, 10, 99.9, 12),
            Swing(SwingKind.LOW, 15, 110.0, 17),
        ]
        levels = cluster_levels(swings, atr_value=1.0, tolerance_atr=0.5)
        # the lone 110.0 swing is a single touch and must be dropped
        assert len(levels) == 1
        assert levels[0].touches == 3
        assert levels[0].price == pytest.approx(100.0333, abs=0.01)
        assert all(lv.touches >= 2 for lv in levels)

    def test_single_touch_dropped(self) -> None:
        swings = [Swing(SwingKind.HIGH, 0, 100.0, 2)]
        assert cluster_levels(swings, 1.0) == []

    def test_zero_atr_returns_empty(self) -> None:
        assert cluster_levels([Swing(SwingKind.HIGH, 0, 1.0, 2)] * 3, 0.0) == []


class TestPrevLevels:
    # Closed-only contract: the series holds CLOSED bars only, so the
    # "previous day/week" is the LAST bar of the series.
    def test_prev_day_levels(self) -> None:
        bars = [d1_bar(4, 105, 95, 100), d1_bar(5, 110, 98, 108)]
        levels = prev_day_levels(bars)
        by_name = {lv.name: lv.price for lv in levels}
        assert by_name["PDH"] == 110.0
        assert by_name["PDL"] == 98.0
        assert by_name["PDC"] == 108.0

    def test_prev_week_levels(self) -> None:
        bars = [d1_bar(4, 200, 190, 195), d1_bar(5, 210, 188, 205)]
        levels = prev_week_levels(bars)
        by_name = {lv.name: lv.price for lv in levels}
        assert by_name["PWH"] == 210.0
        assert by_name["PWL"] == 188.0

    def test_needs_one_closed_bar(self) -> None:
        assert prev_day_levels([]) == []
        assert prev_week_levels([]) == []
        levels = prev_day_levels([d1_bar(4, 105, 95, 100)])
        assert {lv.name: lv.price for lv in levels}["PDH"] == 105.0


class TestSessionLevels:
    def test_sessions_by_broker_day(self) -> None:
        # broker wall 2024-03-05 00:00 .. 23:00 (UTC-encoded)
        day = datetime(2024, 3, 5, tzinfo=UTC)
        bars: list[RateBar] = []
        for hour in range(24):
            t = int(day.timestamp()) + hour * 3600
            high = 100.0 + (1.0 if 7 <= hour < 15 else 0.0)
            low = 99.0 - (0.5 if hour < 7 else 0.0)
            bars.append(
                RateBar(time=t, open=100, high=high, low=low, close=100, tick_volume=10, spread=2)
            )
        windows = {"London": (7, 15), "Asia": (0, 7)}
        levels = session_levels(bars, windows)
        by_name = {lv.name: lv.price for lv in levels}
        assert by_name["London high"] == 101.0
        assert by_name["Asia low"] == 98.5

    def test_wrapping_window(self) -> None:
        day = datetime(2024, 3, 5, tzinfo=UTC)
        bars = [
            RateBar(
                time=int(day.timestamp()) + h * 3600,
                open=100,
                high=100 + (1 if h >= 22 or h < 2 else 0),
                low=99,
                close=100,
                tick_volume=5,
                spread=1,
            )
            for h in range(24)
        ]
        levels = session_levels(bars, {"Late": (22, 2)})
        by_name = {lv.name: lv.price for lv in levels}
        assert by_name["Late high"] == 101.0


class TestRoundNumbers:
    def test_grid_around_price(self) -> None:
        levels = round_numbers(1.0850, per_magnitude=4)
        prices = [lv.price for lv in levels]
        assert any(p == pytest.approx(1.08) for p in prices)
        assert any(p == pytest.approx(1.09) for p in prices)

    def test_uses_level_kind_round(self) -> None:
        assert all(lv.kind is LevelKind.ROUND for lv in round_numbers(2350.0))

    def test_zero_price_empty(self) -> None:
        assert round_numbers(0.0) == []


class TestNearestLevels:
    def test_sorted_by_distance(self) -> None:
        from app.analysis.levels import Level

        price = 100.0
        levels = [
            Level(LevelKind.ROUND, "far", 110.0),
            Level(LevelKind.ROUND, "near", 100.5),
            Level(LevelKind.ROUND, "mid", 103.0),
        ]
        pairs = nearest_levels(levels, price, atr_value=1.0)
        assert pairs[0][0].name == "near"
        assert pairs[-1][0].name == "far"


class TestVolatility:
    def test_percentile_extremes(self) -> None:
        history = list(range(1, 101))  # 1..100
        assert atr_percentile(history) == pytest.approx(99.0, abs=1.0)
        assert atr_percentile([5.0] * 50) == pytest.approx(50.0, abs=1.0)

    def test_regime_thresholds(self) -> None:
        assert regime_from_percentile(10) is Regime.LOW
        assert regime_from_percentile(50) is Regime.NORMAL
        assert regime_from_percentile(95) is Regime.HIGH

    # Closed-only contract: the service feeds a series without the forming
    # day, so adr() uses every bar. exclude_today=True stays for callers
    # that still hold the forming bar.
    def test_adr_uses_all_closed_bars(self) -> None:
        bars = [d1_bar(1, 110, 90, 100)] * 10 + [d1_bar(11, 500, 90, 400)]
        value = adr(bars, period=20)
        assert value > 20.0  # every closed bar counts

    def test_adr_excludes_newest_when_asked(self) -> None:
        bars = [d1_bar(1, 110, 90, 100)] * 10 + [d1_bar(11, 500, 90, 400)]
        value = adr(bars, period=20, exclude_today=True)
        assert value == pytest.approx(20.0)  # the 500-range bar excluded

    def test_adr_used_pct(self) -> None:
        today = d1_bar(11, 130, 100, 125)
        assert adr_used_pct(today, 20.0) == pytest.approx(150.0)
        assert adr_used_pct(None, 20.0) == 0.0

    def test_snapshot_assembles(self) -> None:
        h1 = [d1_bar(1, 105, 95, 100)] * 50
        d1 = [d1_bar(1, 110, 90, 100)] * 25 + [d1_bar(2, 112, 100, 108)]
        snap = volatility_snapshot(h1, d1, atr_history=[1.0] * 100)
        assert snap.atr == pytest.approx(1.0)
        # Closed-only: the average includes the newest closed bar (12 range)
        # over the default 20-bar window: (19 x 20 + 12) / 20 = 19.6
        assert snap.adr == pytest.approx(19.6)
        assert snap.adr_used_pct == 0.0
        assert snap.regime is Regime.NORMAL


class TestSessionClock:
    def test_current_session_london(self) -> None:
        clock = SessionClock()
        # 2024-03-05 09:00 broker wall → London (7-15)
        epoch = int(datetime(2024, 3, 5, 9, 0, tzinfo=UTC).timestamp())
        state = clock.state(epoch)
        assert state.current == "London"

    def test_gap_between_sessions(self) -> None:
        clock = SessionClock()
        epoch = int(datetime(2024, 3, 5, 21, 0, tzinfo=UTC).timestamp())
        state = clock.state(epoch)
        assert state.current == "Off"
        assert state.next_transition_name == "Asia"

    def test_wrapping_window_active(self) -> None:
        clock = SessionClock({"Night": (22, 2)})
        epoch = int(datetime(2024, 3, 5, 23, 0, tzinfo=UTC).timestamp())
        assert clock.state(epoch).current == "Night"

    def test_session_start_and_extremes(self) -> None:
        clock = SessionClock()
        day = datetime(2024, 3, 5, tzinfo=UTC)
        bars = [
            RateBar(
                time=int(day.timestamp()) + h * 3600,
                open=100,
                high=100 + h * 0.1,
                low=99.0,
                close=100,
                tick_volume=5,
                spread=1,
            )
            for h in range(24)
        ]
        epoch = int(datetime(2024, 3, 5, 10, 0, tzinfo=UTC).timestamp())
        start = clock.session_start(epoch, "London")
        assert datetime.fromtimestamp(start, tz=UTC).hour == 7
        extremes = clock.session_extremes("London", bars, epoch)
        assert extremes is not None
        high, low = extremes
        assert high == pytest.approx(101.4, abs=0.06)  # hours 7..14
        assert low == 99.0

    def test_unknown_session(self) -> None:
        clock = SessionClock()
        assert clock.session_start(0, "Nowhere") is None
        assert clock.session_extremes("Nowhere", [], 0) is None
