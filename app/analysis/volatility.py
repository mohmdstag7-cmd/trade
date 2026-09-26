"""Volatility regime: ATR percentile, ADR, % used today (SPEC C3.4)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from app.mt5.models import RateBar


class Regime(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class VolatilitySnapshot:
    atr: float  # latest ATR (same timeframe as the input bars)
    atr_percentile: float  # 0..100 vs the trailing ATR history
    adr: float  # average daily range over the D1 period
    adr_used_pct: float  # how much of the ADR today's range used
    regime: Regime


def atr_percentile(atr_history: np.ndarray | list[float], period: int = 100) -> float:
    """Percentile (0..100) of the LAST ATR value within the trailing window."""
    arr = np.asarray(atr_history, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    if arr.shape[0] < 2:
        return 50.0
    window = arr[-period:]
    last = window[-1]
    n = window.shape[0] - 1
    below = float((window < last).sum())
    equal = float((window == last).sum()) - 1.0  # exclude the value itself
    return (below + 0.5 * max(equal, 0.0)) / float(n) * 100.0


def adr(d1_bars: list[RateBar], period: int = 20, *, exclude_today: bool = True) -> float:
    """Average daily range (high-low) over the last ``period`` CLOSED days.

    ``exclude_today`` keeps the still-forming daily bar (the newest) out of
    the average — it is not complete yet.
    """
    bars = d1_bars[:-1] if (exclude_today and len(d1_bars) > 1) else d1_bars
    if not bars:
        return 0.0
    recent = bars[-period:]
    ranges = [b.high - b.low for b in recent if b.high >= b.low]
    if not ranges:
        return 0.0
    return float(np.mean(ranges))


def adr_used_pct(current_d1_bar: RateBar | None, adr_value: float) -> float:
    """Percent of the ADR consumed by today's range so far (0..inf)."""
    if current_d1_bar is None or adr_value <= 0.0:
        return 0.0
    used = current_d1_bar.high - current_d1_bar.low
    return max(0.0, used / adr_value * 100.0)


def regime_from_percentile(pct: float) -> Regime:
    """Low < 20, high > 80, else normal (documented thresholds)."""
    if pct < 20.0:
        return Regime.LOW
    if pct > 80.0:
        return Regime.HIGH
    return Regime.NORMAL


def volatility_snapshot(
    h1_bars: list[RateBar],
    d1_bars: list[RateBar],
    atr_history: np.ndarray | list[float],
) -> VolatilitySnapshot:
    """Assemble the C3.4 volatility snapshot for one symbol."""
    from app.analysis.indicators import atr as atr_fn

    last_atr = 0.0
    arr = np.asarray(atr_history, dtype=np.float64)
    finite = arr[~np.isnan(arr)]
    if finite.shape[0]:
        last_atr = float(finite[-1])
    if last_atr == 0.0 and h1_bars:
        computed = atr_fn(
            [b.high for b in h1_bars],
            [b.low for b in h1_bars],
            [b.close for b in h1_bars],
            period=14,
        )
        finite = computed[~np.isnan(computed)]
        last_atr = float(finite[-1]) if finite.shape[0] else 0.0

    pct = atr_percentile(atr_history)
    adr_value = adr(d1_bars)
    today = d1_bars[-1] if d1_bars else None
    return VolatilitySnapshot(
        atr=last_atr,
        atr_percentile=pct,
        adr=adr_value,
        adr_used_pct=adr_used_pct(today, adr_value),
        regime=regime_from_percentile(pct),
    )
