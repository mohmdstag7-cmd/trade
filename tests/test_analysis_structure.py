"""Tests for market structure: swings, labels, BOS/CHoCH, trend."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from app.analysis.structure import (
    EventType,
    SwingKind,
    TrendKind,
    classify_swings,
    classify_trend,
    detect_events,
    find_swings,
)


def zigzag(levels: list[float], leg: int = 3) -> tuple[list[float], list[float], list[float]]:
    """Piecewise-linear path touching each level in turn (extremum per leg).

    Returns (highs, lows, closes). Each leg lasts ``leg`` bars, so turning
    points sit exactly on leg boundaries and confirm ``strength`` bars later.
    """
    path: list[float] = [levels[0]]
    for i in range(1, len(levels)):
        prev, target = levels[i - 1], levels[i]
        for step in range(1, leg + 1):
            path.append(prev + (target - prev) * step / leg)
    closes = path
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    return highs, lows, closes


class TestFindSwings:
    def test_simple_up_down_sequence(self) -> None:
        # straight up 5 bars, straight down 5 bars
        closes = [10, 11, 12, 13, 14, 13, 12, 11, 10, 9, 8]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        swings = find_swings(highs, lows, strength=2)
        kinds = [(s.kind, s.index) for s in swings]
        assert (SwingKind.HIGH, 4) in kinds
        # the swing at index 4 is only CONFIRMED 2 bars later
        swing_high = next(s for s in swings if s.kind is SwingKind.HIGH)
        assert swing_high.confirmed_index == 6

    def test_no_lookahead_tail_not_swing(self) -> None:
        closes = [10, 11, 12, 13, 14]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        assert find_swings(highs, lows, strength=2) == []

    def test_insufficient_bars_raises_or_empty(self) -> None:
        assert find_swings([1.0, 2.0], [1.0, 2.0], strength=1) == []
        with pytest.raises(ValueError):
            find_swings([1.0, 2.0], [1.0], strength=1)

    def test_confirmation_delay_is_strength(self) -> None:
        closes = [5.0] * 10
        closes[4] = 20.0
        highs = [c + 0.2 for c in closes]
        highs[4] = 20.2
        lows = [c - 0.2 for c in closes]
        swings = find_swings(highs, lows, strength=3)
        assert swings
        assert all(s.confirmed_index == s.index + 3 for s in swings)

    def test_zigzag_finds_alternating_swings(self) -> None:
        highs, lows, _ = zigzag([10, 12, 10.5, 13, 11, 14], leg=3)
        swings = find_swings(highs, lows, strength=2)
        highs_found = [s for s in swings if s.kind is SwingKind.HIGH]
        lows_found = [s for s in swings if s.kind is SwingKind.LOW]
        assert len(highs_found) >= 2
        assert len(lows_found) >= 2
        # swings alternate between highs and lows (outside bars aside)
        seq = [s.kind for s in swings]
        alternating = sum(1 for a, b in itertools.pairwise(seq) if a is not b)
        assert alternating >= len(seq) - 2


class TestClassify:
    def test_hh_hl_sequence(self) -> None:
        highs, lows, _ = zigzag([10, 12, 10.5, 13, 11, 14], leg=3)
        swings = find_swings(highs, lows, strength=2)
        labelled = classify_swings(swings)
        swing_highs = [s for s in labelled if s.kind is SwingKind.HIGH]
        swing_lows = [s for s in labelled if s.kind is SwingKind.LOW]
        assert len(swing_highs) >= 2
        assert swing_highs[-1].price > swing_highs[0].price
        assert swing_highs[-1].trend_side == "+"
        assert swing_lows[-1].trend_side == "+"

    def test_labels_map(self) -> None:
        from app.analysis.structure import Swing

        s = Swing(SwingKind.HIGH, 0, 5.0, 2, "+")
        assert s.label == "HH"
        s2 = Swing(SwingKind.LOW, 1, 4.0, 3, "-")
        assert s2.label == "LL"


class TestEvents:
    def test_bos_continuation(self) -> None:
        # clean uptrend: two confirmed highs, each broken by later closes
        highs, lows, closes = zigzag([10, 12, 10.5, 13, 11, 14, 12.5, 15], leg=3)
        swings = find_swings(highs, lows, strength=2)
        events = detect_events(swings, closes)
        bos_events = [e for e in events if e.type is EventType.BOS]
        assert len(bos_events) >= 2
        assert all(e.direction == +1 for e in bos_events)

    def test_choch_against_trend(self) -> None:
        # uptrend then a deep reversal below the last higher low
        highs, lows, closes = zigzag([10, 12, 10.5, 13, 11, 14, 12, 9], leg=3)
        swings = find_swings(highs, lows, strength=2)
        events = detect_events(swings, closes)
        assert any(e.type is EventType.CHOCH and e.direction == -1 for e in events)

    def test_no_events_without_breaks(self) -> None:
        closes = [10.0] * 30
        highs = [10.5] * 30
        lows = [9.5] * 30
        assert detect_events([], closes) == []
        swings = find_swings(highs, lows, strength=2)
        assert detect_events(swings, closes) == []


class TestClassifyTrend:
    def test_uptrend(self) -> None:
        highs, lows, _ = zigzag([10, 12, 10.5, 13, 11, 14, 12.5, 15], leg=3)
        swings = find_swings(highs, lows, strength=2)
        assert classify_trend(swings) is TrendKind.UP

    def test_downtrend(self) -> None:
        highs, lows, _ = zigzag([14, 12, 13.5, 11, 12.5, 10, 11.5, 9], leg=3)
        swings = find_swings(highs, lows, strength=2)
        assert classify_trend(swings) is TrendKind.DOWN

    def test_range(self) -> None:
        # last high lower AND last low higher → compression
        highs, lows, _ = zigzag([12, 10, 12.1, 9.9, 11.9, 10.1, 11.5, 10.4, 11.0], leg=3)
        swings = find_swings(highs, lows, strength=2)
        assert classify_trend(swings) is TrendKind.RANGE

    def test_not_enough_swings_is_range(self) -> None:
        assert classify_trend([]) is TrendKind.RANGE


class TestNoLookaheadProperty:
    def test_swings_never_confirmed_after_end(self) -> None:
        rng = np.random.default_rng(11)
        highs = list(100 + rng.normal(0, 1.0, 200))
        lows = [h - abs(d) for h, d in zip(highs, rng.normal(0, 0.5, 200), strict=False)]
        swings = find_swings(highs, lows, strength=2)
        n = len(highs)
        assert all(s.confirmed_index < n for s in swings)
        assert all(s.confirmed_index == s.index + 2 for s in swings)
