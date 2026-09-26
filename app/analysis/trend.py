"""Multi-timeframe trend matrix with a bias score (SPEC C3.1).

Each analysed timeframe yields a direction (-1/0/+1), a strength (0..100)
and human-readable reason keys. The overall bias score weights timeframes
(H1/H4 count more than M15) into -100..+100 with the reasons attached —
everything rule-based and explainable, no black box.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.analysis.indicators import adx, ema, rsi, slope_pct
from app.core.timeframes import Timeframe
from app.mt5.models import RateBar

#: Bias weight per timeframe (H1/H4 anchor the bias, M15 fine-tunes).
BIAS_WEIGHTS: dict[Timeframe, float] = {
    Timeframe.M15: 1.0,
    Timeframe.H1: 2.0,
    Timeframe.H4: 3.0,
    Timeframe.D1: 4.0,
}

#: ADX above this means "trending" for strength purposes.
ADX_TREND_MIN = 20.0
#: Reason i18n key prefix.
REASON_PREFIX = "analysis.reason"


@dataclass(frozen=True, slots=True)
class TrendVector:
    timeframe: Timeframe
    direction: int  # -1 / 0 / +1
    strength: int  # 0..100
    reasons: tuple[str, ...] = ()  # i18n keys, e.g. analysis.reason.ema_up

    @property
    def label(self) -> str:
        if self.direction > 0:
            return "up"
        if self.direction < 0:
            return "down"
        return "flat"


@dataclass(frozen=True, slots=True)
class TrendMatrix:
    vectors: tuple[TrendVector, ...]
    bias_score: int  # -100..+100
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def vector(self, timeframe: Timeframe) -> TrendVector | None:
        for v in self.vectors:
            if v.timeframe is timeframe:
                return v
        return None


def assess_timeframe(bars: list[RateBar], timeframe: Timeframe) -> TrendVector | None:
    """Rule-based trend assessment for ONE timeframe's closed bars."""
    if len(bars) < 60:
        return None
    closes = np.asarray([b.close for b in bars], dtype=np.float64)
    highs = np.asarray([b.high for b in bars], dtype=np.float64)
    lows = np.asarray([b.low for b in bars], dtype=np.float64)

    ema_fast = ema(closes, 20)
    ema_slow = ema(closes, 50)
    adx_arr, plus_di, minus_di = adx(highs, lows, closes, 14)
    slope = slope_pct(closes, lookback=5)[-1]
    rsi_last = rsi(closes, 14)[-1]

    reasons: list[str] = []
    direction = 0
    if ema_fast[-1] > ema_slow[-1]:
        direction = +1
        reasons.append(f"{REASON_PREFIX}.ema_up")
    elif ema_fast[-1] < ema_slow[-1]:
        direction = -1
        reasons.append(f"{REASON_PREFIX}.ema_down")
    else:
        reasons.append(f"{REASON_PREFIX}.ema_flat")

    if slope > 0.0005:
        reasons.append(f"{REASON_PREFIX}.slope_up")
    elif slope < -0.0005:
        reasons.append(f"{REASON_PREFIX}.slope_down")

    last_adx = adx_arr[-1]
    strength = 0
    if np.isfinite(last_adx):
        trending = last_adx >= ADX_TREND_MIN
        if trending:
            # scale 20..50 → 30..100
            strength = int(min(100.0, (last_adx - ADX_TREND_MIN) / 30.0 * 70.0 + 30.0))
            reasons.append(f"{REASON_PREFIX}.adx_trending")
            if plus_di[-1] > minus_di[-1] and direction >= 0:
                reasons.append(f"{REASON_PREFIX}.di_bulls")
            elif minus_di[-1] > plus_di[-1] and direction <= 0:
                reasons.append(f"{REASON_PREFIX}.di_bears")
                if direction == 0:
                    direction = -1
        else:
            strength = int(min(30.0, last_adx / ADX_TREND_MIN * 30.0))
            reasons.append(f"{REASON_PREFIX}.adx_choppy")

    if np.isfinite(rsi_last):
        if rsi_last > 70:
            reasons.append(f"{REASON_PREFIX}.rsi_overbought")
        elif rsi_last < 30:
            reasons.append(f"{REASON_PREFIX}.rsi_oversold")

    # a flat EMA structure with no ADX confirmation is genuinely neutral
    if direction == 0 or strength == 0:
        direction = direction if strength >= 30 else 0

    return TrendVector(timeframe, direction, strength, tuple(reasons))


def build_matrix(
    bars_by_timeframe: dict[Timeframe, list[RateBar]],
    weights: dict[Timeframe, float] | None = None,
) -> TrendMatrix:
    """Assess every provided timeframe and compute the weighted bias score."""
    weights = weights or BIAS_WEIGHTS
    vectors: list[TrendVector] = []
    for tf in (
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
        Timeframe.M5,
        Timeframe.M30,
    ):
        bars = bars_by_timeframe.get(tf)
        if not bars:
            continue
        vector = assess_timeframe(bars, tf)
        if vector is not None:
            vectors.append(vector)

    total_weight = 0.0
    weighted = 0.0
    for v in vectors:
        w = weights.get(v.timeframe, 1.0)
        total_weight += w
        weighted += v.direction * (v.strength / 100.0) * w
    bias = round(weighted / total_weight * 100.0) if total_weight else 0
    bias = max(-100, min(100, bias))

    all_reasons: list[str] = []
    for v in vectors:
        all_reasons.extend(v.reasons)
    return TrendMatrix(tuple(vectors), bias, tuple(all_reasons))
