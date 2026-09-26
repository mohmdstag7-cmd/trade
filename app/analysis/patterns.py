"""Candlestick patterns: engulfing, pin bar, inside bar (SPEC C3.8).

Patterns are INFORMATION and future ML features only — never standalone
signals (SPEC C3.8). Detection runs on CLOSED bars and reports the bar
index it completed on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.mt5.models import RateBar


class PatternKind(StrEnum):
    BULL_ENGULFING = "bull_engulfing"
    BEAR_ENGULFING = "bear_engulfing"
    BULL_PIN = "bull_pin"  # hammer / bullish pin
    BEAR_PIN = "bear_pin"  # shooting star / bearish pin
    INSIDE_BAR = "inside_bar"


@dataclass(frozen=True, slots=True)
class Pattern:
    kind: PatternKind
    index: int  # closed bar where the pattern completed
    direction: int  # +1 bullish, -1 bearish, 0 neutral


def _body(b: RateBar) -> float:
    return abs(b.close - b.open)


def _upper_wick(b: RateBar) -> float:
    return b.high - max(b.open, b.close)


def _lower_wick(b: RateBar) -> float:
    return min(b.open, b.close) - b.low


def detect(bars: list[RateBar], last_n: int = 3) -> list[Pattern]:
    """Detect patterns on the last ``last_n`` CLOSED bars.

    Returns patterns newest-first. Requires at least 2 bars.
    """
    out: list[Pattern] = []
    if len(bars) < 2:
        return out
    start = max(1, len(bars) - last_n)
    for i in range(start, len(bars)):
        cur = bars[i]
        prev = bars[i - 1]
        body = _body(cur)

        # engulfing: body fully covers the previous body, opposite direction
        if body > 0.0:
            if (
                prev.close < prev.open  # prev bearish
                and cur.close > cur.open  # cur bullish
                and cur.close >= prev.open
                and cur.open <= prev.close
            ):
                out.append(Pattern(PatternKind.BULL_ENGULFING, i, +1))
            elif (
                prev.close > prev.open
                and cur.close < cur.open
                and cur.close <= prev.open
                and cur.open >= prev.close
            ):
                out.append(Pattern(PatternKind.BEAR_ENGULFING, i, -1))

        # pin bar: wick >= 2x body and body within the opposite third
        if body > 0.0:
            total = cur.high - cur.low
            if total > 0.0:
                if _lower_wick(cur) >= 2.0 * body and (
                    min(cur.open, cur.close) >= cur.low + 0.6 * total
                ):
                    out.append(Pattern(PatternKind.BULL_PIN, i, +1))
                elif _upper_wick(cur) >= 2.0 * body and (
                    max(cur.open, cur.close) <= cur.low + 0.4 * total
                ):
                    out.append(Pattern(PatternKind.BEAR_PIN, i, -1))

        # inside bar: current range fully inside the previous range
        if (
            cur.high <= prev.high
            and cur.low >= prev.low
            and (cur.high > cur.low or prev.high > prev.low)
        ):
            out.append(Pattern(PatternKind.INSIDE_BAR, i, 0))
    out.reverse()  # newest first
    return out
