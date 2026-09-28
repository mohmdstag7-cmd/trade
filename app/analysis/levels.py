"""Key levels: S/R clusters, prior day/week, sessions, round numbers (C3.3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import numpy as np

from app.analysis.structure import Swing
from app.mt5.models import RateBar


class LevelKind(StrEnum):
    SR = "sr"  # support/resistance from swing clusters
    PREV_DAY = "prev_day"  # PDH / PDL / PDC
    PREV_WEEK = "prev_week"  # PWH / PWL / PWC
    SESSION = "session"  # Asia/London/NY high/low
    ROUND = "round"  # round number grid


@dataclass(frozen=True, slots=True)
class Level:
    kind: LevelKind
    name: str  # e.g. "SR", "PDH", "Asia high", "1.0850"
    price: float
    touches: int = 1

    def distance_atr(self, price: float, atr_value: float) -> float:
        """Signed distance from ``price`` in ATR units."""
        if atr_value <= 0.0:
            return 0.0
        return (self.price - price) / atr_value


# -- S/R clustering -----------------------------------------------------------


def cluster_levels(
    swings: list[Swing],
    atr_value: float,
    tolerance_atr: float = 0.5,
) -> list[Level]:
    """Cluster swing prices within ``tolerance_atr`` into S/R levels.

    A cluster's price is the mean of its members; ``touches`` counts the
    swings in the cluster (more touches = stronger level). Works on both
    swing kinds together — a band that rejected price from both sides is
    still one zone.
    """
    if atr_value <= 0.0:
        return []
    tol = tolerance_atr * atr_value
    prices = sorted(s.price for s in swings)
    clusters: list[list[float]] = []
    for price in prices:
        if clusters and abs(price - clusters[-1][-1]) <= tol:
            clusters[-1].append(price)
        else:
            clusters.append([price])
    return [
        Level(LevelKind.SR, "SR", float(np.mean(cluster)), len(cluster))
        for cluster in clusters
        if len(cluster) >= 2  # a single touch is noise, not a level
    ]


# -- prior day / week ----------------------------------------------------------


def prev_day_levels(d1_bars: list[RateBar]) -> list[Level]:
    """PDH / PDL / PDC from the last CLOSED daily bar (bars oldest->newest).

    The series fed here contains closed bars only (the forming day is
    excluded at ingest), so "previous day" is the LAST bar, not ``[-2]``.
    """
    if not d1_bars:
        return []
    prev = d1_bars[-1]
    return [
        Level(LevelKind.PREV_DAY, "PDH", prev.high),
        Level(LevelKind.PREV_DAY, "PDL", prev.low),
        Level(LevelKind.PREV_DAY, "PDC", prev.close),
    ]


def prev_week_levels(w1_bars: list[RateBar]) -> list[Level]:
    """PWH / PWL / PWC from the last closed weekly bar (closed-only series)."""
    if not w1_bars:
        return []
    prev = w1_bars[-1]
    return [
        Level(LevelKind.PREV_WEEK, "PWH", prev.high),
        Level(LevelKind.PREV_WEEK, "PWL", prev.low),
        Level(LevelKind.PREV_WEEK, "PWC", prev.close),
    ]


# -- session levels --------------------------------------------------------------


def session_levels(
    h1_bars: list[RateBar],
    windows: dict[str, tuple[int, int]],
    *,
    reference_day: str | None = None,
) -> list[Level]:
    """High/low per session window (broker-wall hours) over ``reference_day``.

    ``windows`` maps session name -> (start_hour, end_hour) in broker wall
    time, end exclusive; a window may wrap midnight (start > end). Bars are
    broker-encoded epochs (SPEC C2.4). ``reference_day`` defaults to the
    broker day of the LAST bar.
    """
    if not h1_bars:
        return []
    if reference_day is None:
        last_dt = datetime.fromtimestamp(h1_bars[-1].time, tz=UTC)
        reference_day = last_dt.strftime("%Y-%m-%d")
    out: list[Level] = []
    for name, (start_h, end_h) in windows.items():
        day_bars = [
            b
            for b in h1_bars
            if datetime.fromtimestamp(b.time, tz=UTC).strftime("%Y-%m-%d") == reference_day
        ]
        selected: list[RateBar] = []
        for b in day_bars:
            hour = datetime.fromtimestamp(b.time, tz=UTC).hour
            if start_h < end_h:
                if start_h <= hour < end_h:
                    selected.append(b)
            else:  # wraps midnight
                if hour >= start_h or hour < end_h:
                    selected.append(b)
        if not selected:
            continue
        out.append(Level(LevelKind.SESSION, f"{name} high", max(b.high for b in selected)))
        out.append(Level(LevelKind.SESSION, f"{name} low", min(b.low for b in selected)))
    return out


# -- round numbers -----------------------------------------------------------------


def round_numbers(price: float, per_magnitude: int = 5) -> list[Level]:
    """Round-number grid around ``price`` (psychological levels).

    The grid step adapts to the price magnitude: 1000s for four-digit FX
    (e.g. JPY crosses / XAU), 0.0100 for EURUSD-like pairs, and so on —
    the step is chosen so roughly ``per_magnitude`` levels exist on each
    side within 2 percent of price.
    """
    if price <= 0.0:
        return []
    # One-hundredth of the leading decade: 0.01 for EURUSD-like prices,
    # 10 for gold-like prices, 100 for index-like prices.
    step = 10.0 ** (np.floor(np.log10(price)) - 2)
    base = round(price / step) * step
    levels: list[Level] = []
    for i in range(-per_magnitude, per_magnitude + 1):
        value = base + i * step
        if value <= 0.0:
            continue
        levels.append(Level(LevelKind.ROUND, f"{value:,.4f}".rstrip("0").rstrip("."), value))
    return levels


def nearest_levels(
    levels: list[Level], price: float, atr_value: float, max_count: int = 6
) -> list[tuple[Level, float]]:
    """Levels sorted by |distance| in ATR, closest first (bounded)."""
    pairs = [(lv, abs(lv.distance_atr(price, atr_value))) for lv in levels]
    pairs.sort(key=lambda t: t[1])
    return pairs[:max_count]


def days_between_levels_is_stale(latest: datetime, level_time: datetime) -> bool:
    """Helper kept trivial on purpose (used by tests to document semantics)."""
    return (latest - level_time) > timedelta(days=0)
