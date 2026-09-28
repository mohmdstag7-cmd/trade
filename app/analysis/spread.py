"""Spread monitor: current spread vs the typical spread for this hour (C3.7)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.observability.logger import get_logger

log = get_logger("sync")

#: Current spread above this factor x the hourly median is "abnormal".
ABNORMAL_FACTOR = 2.0
#: Samples per (symbol, hour) bucket kept for the rolling median.
BUCKET_SIZE = 60


@dataclass(slots=True)
class SpreadMonitor:
    """Rolling per-(symbol, hour) spread statistics (spread in points)."""

    abnormal_factor: float = ABNORMAL_FACTOR
    _history: dict[tuple[str, int], list[float]] = field(default_factory=dict)
    _latest_abnormal: dict[str, bool] = field(default_factory=dict)

    def update(self, symbol: str, hour_utc: int, spread_points: float) -> bool:
        """Record one spread sample; returns True when it looks abnormal."""
        key = (symbol, hour_utc % 24)
        bucket = self._history.setdefault(key, [])
        bucket.append(spread_points)
        if len(bucket) > BUCKET_SIZE:
            del bucket[0]
        abnormal = self.is_abnormal(symbol, hour_utc, spread_points)
        self._latest_abnormal[symbol] = abnormal
        return abnormal

    def latest_abnormal(self, symbol: str) -> bool:
        """Whether the newest recorded sample for ``symbol`` was abnormal."""
        return self._latest_abnormal.get(symbol, False)

    def typical(self, symbol: str, hour_utc: int) -> float | None:
        """Median spread for this (symbol, hour) — None before enough data."""
        bucket = self._history.get((symbol, hour_utc % 24))
        if not bucket or len(bucket) < 5:
            return None
        return statistics.median(bucket)

    def is_abnormal(self, symbol: str, hour_utc: int, spread_points: float) -> bool:
        typical = self.typical(symbol, hour_utc)
        if typical is None or typical <= 0.0:
            return False
        abnormal = spread_points > self.abnormal_factor * typical
        if abnormal:
            log.warning(
                "analysis: {} spread {} pts > {} x typical {:.0f} pts for hour {:+02d} UTC",
                symbol,
                spread_points,
                self.abnormal_factor,
                typical,
                hour_utc,
            )
        return abnormal
