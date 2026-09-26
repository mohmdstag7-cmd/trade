"""Tests for app.analysis.indicators — closed-bar, no look-ahead."""

from __future__ import annotations

import numpy as np
import pytest

from app.analysis.indicators import adx, atr, ema, rsi, slope_pct, sma, true_range


class TestSmaEma:
    def test_sma_constant_series_is_constant(self) -> None:
        out = sma([5.0] * 30, 10)
        assert np.all(np.isnan(out[:9]))
        assert np.allclose(out[9:], 5.0)

    def test_sma_known_values(self) -> None:
        out = sma([1.0, 2.0, 3.0, 4.0], 2)
        assert np.isnan(out[0])
        assert out[1] == 1.5 and out[2] == 2.5 and out[3] == 3.5

    def test_ema_seed_is_sma_then_recursion(self) -> None:
        values = [float(i) for i in range(1, 21)]
        out = ema(values, 5)
        assert out[4] == pytest.approx(3.0)  # mean(1..5)
        alpha = 2.0 / 6.0
        expected = out[4]
        for i in range(5, 20):
            expected = alpha * values[i] + (1 - alpha) * expected
            assert out[i] == pytest.approx(expected)

    def test_ema_trending_up_stays_below_price(self) -> None:
        values = np.linspace(1.0, 100.0, 200)
        out = ema(values, 10)
        assert out[-1] < values[-1]

    def test_short_input_all_nan(self) -> None:
        assert np.all(np.isnan(ema([1.0, 2.0], 5)))
        assert np.all(np.isnan(sma([1.0], 2)))


class TestRsi:
    def test_all_gains_rsi_100(self) -> None:
        values = list(np.linspace(1.0, 50.0, 40))
        out = rsi(values, 14)
        assert out[-1] == pytest.approx(100.0)

    def test_flat_series_rsi_50(self) -> None:
        out = rsi([7.0] * 40, 14)
        assert out[-1] == pytest.approx(50.0)

    def test_all_losses_rsi_0(self) -> None:
        values = list(np.linspace(50.0, 1.0, 40))
        out = rsi(values, 14)
        assert out[-1] == pytest.approx(0.0)

    def test_range_between_0_and_100(self) -> None:
        rng = np.random.default_rng(7)
        values = 100 + np.cumsum(rng.normal(0, 1.0, 300))
        out = rsi(values, 14)
        finite = out[~np.isnan(out)]
        assert np.all((finite >= 0.0) & (finite <= 100.0))

    def test_too_short_all_nan(self) -> None:
        assert np.all(np.isnan(rsi([1.0] * 10, 14)))


class TestAtr:
    def test_constant_range_bar(self) -> None:
        n = 60
        highs = [11.0] * n
        lows = [9.0] * n
        closes = [10.0] * n
        out = atr(highs, lows, closes, 14)
        # constant true range of 2.0 → ATR converges to 2.0
        assert out[-1] == pytest.approx(2.0)

    def test_first_bar_uses_plain_range(self) -> None:
        tr = true_range([12.0], [10.0], [11.0])
        assert tr[0] == 2.0

    def test_spike_raises_atr(self) -> None:
        n = 60
        base = [10.0] * n
        highs = [11.0] * n
        lows = [9.0] * n
        closes = base
        highs[-1] = 20.0
        out = atr(highs, lows, closes, 14)
        assert out[-1] > 2.0

    def test_length_preserved(self) -> None:
        out = atr([1, 3, 2] * 20, [1, 1, 1] * 20, [2, 2, 1.5] * 20, 14)
        assert out.shape[0] == 60


class TestAdx:
    def _trend_bars(self, up: bool, n: int = 120) -> tuple[list[float], list[float], list[float]]:
        step = 0.5 if up else -0.5
        closes = [100.0 + step * i for i in range(n)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        return highs, lows, closes

    def test_strong_trend_high_adx(self) -> None:
        highs, lows, closes = self._trend_bars(up=True)
        adx_arr, plus_di, minus_di = adx(highs, lows, closes, 14)
        assert adx_arr[-1] > 40.0
        assert plus_di[-1] > minus_di[-1]

    def test_downtrend_minus_di_dominates(self) -> None:
        highs, lows, closes = self._trend_bars(up=False)
        adx_arr, plus_di, minus_di = adx(highs, lows, closes, 14)
        assert minus_di[-1] > plus_di[-1]
        assert adx_arr[-1] > 40.0

    def test_choppy_market_low_adx(self) -> None:
        rng = np.random.default_rng(3)
        closes = list(100 + np.cumsum(rng.normal(0, 0.05, 120)))
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        adx_arr, _, _ = adx(highs, lows, closes, 14)
        assert adx_arr[-1] < 35.0

    def test_nan_until_enough_bars(self) -> None:
        highs, lows, closes = self._trend_bars(up=True, n=20)
        adx_arr, plus_di, minus_di = adx(highs, lows, closes, 14)
        assert np.all(np.isnan(adx_arr))
        assert np.all(np.isnan(plus_di))
        assert np.all(np.isnan(minus_di))


class TestSlope:
    def test_positive_slope(self) -> None:
        out = slope_pct(list(np.linspace(10.0, 20.0, 50)), lookback=5)
        assert out[:5].sum() == 0.0
        assert out[-1] > 0.0

    def test_flat_is_zero(self) -> None:
        out = slope_pct([5.0] * 30, lookback=5)
        assert np.all(out == 0.0)
