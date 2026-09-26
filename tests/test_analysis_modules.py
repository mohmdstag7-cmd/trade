"""Tests for trend matrix, correlation, spread, patterns, card, scanner."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from app.analysis.card import CardBuilder, CardLine, build_currency_notes
from app.analysis.correlation import (
    currency_strength,
    rolling_correlation,
    strength_ranking,
)
from app.analysis.levels import Level, LevelKind
from app.analysis.patterns import PatternKind, detect
from app.analysis.scanner import rank, scan_symbol
from app.analysis.spread import SpreadMonitor
from app.analysis.trend import assess_timeframe, build_matrix
from app.analysis.volatility import Regime, VolatilitySnapshot
from app.core.timeframes import Timeframe
from app.mt5.models import RateBar

TF15 = Timeframe.M15


def trend_bars(start: float, step: float, n: int = 120) -> list[RateBar]:
    t0 = int(datetime(2024, 3, 1, tzinfo=UTC).timestamp())
    closes = [start + step * i for i in range(n)]
    return [
        RateBar(
            time=t0 + i * 3600,
            open=c,
            high=c + 0.5,
            low=c - 0.5,
            close=c,
            tick_volume=100,
            spread=2,
        )
        for i, c in enumerate(closes)
    ]


def flat_bars(n: int = 120, price: float = 100.0) -> list[RateBar]:
    return trend_bars(price, 0.0, n)


class TestTrendMatrix:
    def test_uptrend_detected(self) -> None:
        vector = assess_timeframe(trend_bars(100.0, 0.5), Timeframe.H1)
        assert vector is not None
        assert vector.direction == +1
        assert vector.strength > 30
        assert any("ema_up" in r for r in vector.reasons)

    def test_downtrend_detected(self) -> None:
        vector = assess_timeframe(trend_bars(200.0, -0.5), Timeframe.H4)
        assert vector is not None
        assert vector.direction == -1

    def test_flat_is_neutral(self) -> None:
        bars = flat_bars(150, 100.0)
        vector = assess_timeframe(bars, Timeframe.H1)
        assert vector is not None
        assert vector.direction in (-1, 0, +1)
        assert vector.strength <= 30 or vector.direction == 0

    def test_too_few_bars_returns_none(self) -> None:
        assert assess_timeframe(trend_bars(100, 0.5, 30), Timeframe.M15) is None

    def test_matrix_bias_weighted(self) -> None:
        matrix = build_matrix(
            {
                Timeframe.M15: trend_bars(100, 0.5),
                Timeframe.H1: trend_bars(100, 0.5),
                Timeframe.H4: trend_bars(100, 0.5),
                Timeframe.D1: trend_bars(100, 0.5),
            }
        )
        assert matrix.bias_score > 50
        assert len(matrix.vectors) == 4

    def test_matrix_mixed_bias(self) -> None:
        matrix = build_matrix(
            {
                Timeframe.M15: trend_bars(100, -0.5),
                Timeframe.H1: trend_bars(100, -0.5),
                Timeframe.H4: trend_bars(100, 0.5),
                Timeframe.D1: trend_bars(100, 0.5),
            }
        )
        assert -100 <= matrix.bias_score <= 100
        # higher weights on the uptrends → non-negative bias
        assert matrix.bias_score >= 0

    def test_matrix_empty(self) -> None:
        assert build_matrix({}).bias_score == 0


class TestCorrelation:
    def test_perfect_correlation(self) -> None:
        base = list(np.linspace(100, 110, 60))
        corr = rolling_correlation({"EURUSD": base, "GBPUSD": base})
        assert corr.matrix[0, 1] == pytest.approx(1.0)

    def test_inverse_correlation(self) -> None:
        rng = np.random.default_rng(9)
        rets = rng.normal(0, 0.3, 60)
        a = list(100 + np.cumsum(rets))
        b = list(100 - np.cumsum(rets))
        corr = rolling_correlation({"EURUSD": a, "USDJPY": b})
        assert corr.matrix[0, 1] == pytest.approx(-1.0, abs=1e-3)

    def test_with_symbol_view(self) -> None:
        base = list(np.linspace(100, 110, 60))
        other = list(np.linspace(50, 55, 60))
        noise = list(np.linspace(100, 100.1, 60))
        corr = rolling_correlation({"EURUSD": base, "GBPUSD": other, "XAUUSD": noise})
        pairs = corr.with_symbol("EURUSD")
        assert pairs["GBPUSD"] == pytest.approx(1.0)
        assert -1.0 <= pairs["XAUUSD"] <= 1.0


class TestCurrencyStrength:
    def test_base_quote_split(self) -> None:
        eur_rising = list(np.linspace(1.0, 1.2, 30))  # EUR strong vs USD
        gbp_falling = list(np.linspace(1.5, 1.3, 30))  # GBP weak vs USD
        scores = currency_strength({"EURUSD": eur_rising, "GBPUSD": gbp_falling})
        assert scores["EUR"] > 0
        assert scores["USD"] < 0  # USD is quote on both
        assert scores["GBP"] < 0
        ranking = strength_ranking(scores)
        assert ranking[0][0] == "EUR"

    def test_six_letter_symbol(self) -> None:
        scores = currency_strength({"EURUSD": list(np.linspace(1.0, 1.1, 20))})
        assert "EUR" in scores and "USD" in scores

    def test_metal_contributes_quote_leg_only(self) -> None:
        scores = currency_strength({"XAUUSD": [1.0, 2.0, 3.0]})
        assert scores == {"USD": -1.5}
        assert "XAU" not in scores


class TestSpreadMonitor:
    def test_typical_and_abnormal(self) -> None:
        mon = SpreadMonitor()
        for _ in range(10):
            assert mon.update("EURUSD", 9, 20.0) is False
        assert mon.typical("EURUSD", 9) == 20.0
        assert mon.is_abnormal("EURUSD", 9, 45.0) is True
        assert mon.is_abnormal("EURUSD", 9, 25.0) is False

    def test_not_enough_history(self) -> None:
        mon = SpreadMonitor()
        mon.update("EURUSD", 9, 20.0)
        assert mon.typical("EURUSD", 9) is None
        assert mon.is_abnormal("EURUSD", 9, 999.0) is False

    def test_hour_wraps(self) -> None:
        mon = SpreadMonitor()
        for _ in range(6):
            mon.update("EURUSD", 23, 20.0)
        assert mon.typical("EURUSD", 47) == 20.0  # 47 % 24 == 23


def _bar(open_: float, close: float, high: float, low: float) -> RateBar:
    return RateBar(time=1, open=open_, high=high, low=low, close=close, tick_volume=10, spread=1)


class TestPatterns:
    def test_bullish_engulfing(self) -> None:
        bars = [_bar(10.0, 9.5, 10.2, 9.3), _bar(9.2, 10.8, 11.0, 9.1)]
        found = detect(bars)
        assert found[0].kind is PatternKind.BULL_ENGULFING
        assert found[0].direction == +1

    def test_bearish_engulfing(self) -> None:
        bars = [_bar(10.0, 10.5, 10.7, 9.8), _bar(10.8, 9.2, 10.9, 9.0)]
        found = detect(bars)
        assert found[0].kind is PatternKind.BEAR_ENGULFING

    def test_bull_pin(self) -> None:
        # long lower wick, small body at the top
        bars = [_bar(10.0, 10.1, 10.2, 9.9), _bar(10.1, 10.05, 10.2, 8.0)]
        found = detect(bars)
        assert found[0].kind is PatternKind.BULL_PIN

    def test_bear_pin(self) -> None:
        bars = [_bar(10.0, 9.9, 10.1, 9.8), _bar(9.9, 9.95, 12.0, 9.8)]
        found = detect(bars)
        assert found[0].kind is PatternKind.BEAR_PIN

    def test_inside_bar(self) -> None:
        bars = [_bar(10.0, 10.5, 11.0, 9.5), _bar(10.2, 10.4, 10.8, 9.8)]
        found = detect(bars)
        assert any(p.kind is PatternKind.INSIDE_BAR for p in found)

    def test_no_pattern_in_noise(self) -> None:
        bars = [_bar(10.0, 10.2, 10.3, 9.9), _bar(10.2, 10.1, 10.4, 9.8)]
        assert detect(bars, last_n=1) == []

    def test_needs_two_bars(self) -> None:
        assert detect([_bar(10, 10.1, 10.2, 9.9)]) == []


def _vol(regime: str = "normal") -> VolatilitySnapshot:
    return VolatilitySnapshot(
        atr=1.0,
        atr_percentile=50.0,
        adr=20.0,
        adr_used_pct=40.0,
        regime=Regime(regime),
    )


class TestCardBuilder:
    def _trend(self, bias: int) -> object:
        from app.analysis.trend import TrendMatrix, TrendVector

        v = TrendVector(Timeframe.H4, 1 if bias > 0 else -1, min(100, abs(bias)), ())
        return TrendMatrix((v,), bias)

    def test_card_lines_and_verdict(self) -> None:
        builder = CardBuilder()
        card = builder.build(
            symbol="EURUSD",
            trend=self._trend(70),
            h1_bars=[],
            d1_bars=[],
            levels=[Level(LevelKind.SR, "SR", 1.0840, 3)],
            volatility=_vol("high"),
            session_name="London",
            atr_value=0.0010,
            last_close=1.0845,
        )
        assert card.verdict == "bias_up"
        keys = [ln.key for ln in card.lines]
        assert "analysis.card.trend" in keys
        assert "analysis.card.level" in keys
        assert "analysis.card.volatility" in keys
        assert "analysis.card.session" in keys
        level_line = next(ln for ln in card.lines if ln.key.endswith(".level"))
        assert level_line.params["name"] == "SR"
        assert card.regime == "high"

    def test_wait_on_high_impact_event(self) -> None:
        from app.analysis.card import NextEvent

        builder = CardBuilder()
        card = builder.build(
            symbol="EURUSD",
            trend=self._trend(80),
            h1_bars=[],
            d1_bars=[],
            levels=[],
            volatility=_vol("high"),
            session_name="London",
            atr_value=0.001,
            last_close=1.08,
            next_event=NextEvent("USD", "CPI", 60, "high"),
        )
        assert card.verdict == "wait"
        assert card.next_event is not None
        assert card.next_event.minutes_until == 60

    def test_watch_for_mid_bias(self) -> None:
        builder = CardBuilder()
        card = builder.build(
            symbol="EURUSD",
            trend=self._trend(30),
            h1_bars=[],
            d1_bars=[],
            levels=[],
            volatility=_vol("normal"),
            session_name="Asia",
            atr_value=0.001,
            last_close=1.08,
        )
        assert card.verdict == "watch"

    def test_wait_for_weak_bias(self) -> None:
        builder = CardBuilder()
        card = builder.build(
            symbol="EURUSD",
            trend=self._trend(10),
            h1_bars=[],
            d1_bars=[],
            levels=[],
            volatility=_vol("low"),
            session_name="Asia",
            atr_value=0.001,
            last_close=1.08,
        )
        assert card.verdict == "wait"

    def test_cardline_defaults(self) -> None:
        line = CardLine("analysis.card.trend")
        assert line.params == {}


class TestScanner:
    def test_ready_ranking(self) -> None:
        entries = [
            scan_symbol(
                "EURUSD",
                70,
                "up",
                "normal",
                False,
                [Level(LevelKind.SR, "SR", 1.0840, 2)],
                1.0841,
                0.001,
            ),
            scan_symbol("GBPUSD", 30, "up", "normal", False, [], 1.26, 0.001),
            scan_symbol("XAUUSD", 60, "range", "normal", True, [], 2350.0, 1.0),
        ]
        ranked = rank(entries)
        assert ranked[0].symbol == "EURUSD"
        assert ranked[0].state == "ready"
        assert ranked[1].state == "forming"
        assert ranked[2].state == "none"
        assert "analysis.scan.event_blocked" in ranked[2].reasons

    def test_score_decays_with_distance(self) -> None:
        near = scan_symbol(
            "A", 80, "up", "normal", False, [Level(LevelKind.SR, "SR", 100.0, 2)], 100.1, 1.0
        )
        far = scan_symbol(
            "B", 80, "up", "normal", False, [Level(LevelKind.SR, "SR", 110.0, 2)], 100.0, 1.0
        )
        assert near.score > far.score
        assert far.score < 80.0

    def test_no_levels_neutral_proximity(self) -> None:
        entry = scan_symbol("A", 80, "up", "normal", False, [], 100.0, 1.0)
        assert entry.score == 40.0  # 80 * 0.5

    def test_blockers_force_none_or_forming(self) -> None:
        entry = scan_symbol("A", 90, "range", "low", True, [], 100.0, 0)
        assert entry.state == "none"


class TestCorrelationNotes:
    def test_strong_only(self) -> None:
        import numpy as np

        corr = rolling_correlation(
            {
                "EURUSD": list(np.linspace(1, 1.1, 60)),
                "GBPUSD": list(np.linspace(1, 1.1, 60)),
                "USDJPY": list(np.linspace(150, 140, 60)),
            }
        )
        notes = build_currency_notes(corr, "EURUSD")
        assert notes  # EURUSD~GBPUSD=1 and EURUSD~USDJPY=-1
        assert all(abs(ln.params["corr"]) >= 0.7 for ln in notes)
