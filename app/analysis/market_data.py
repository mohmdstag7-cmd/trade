"""Market data manager: incremental closed-bar cache + sanity checks.

Responsibilities (SPEC C2.2, C2.3):

- **Cache per (symbol, timeframe)**: bars arrive from the gateway as a
  bounded ``copy_rates_from_pos`` window. The manager merges each window
  into an ordered, de-duplicated series keyed by bar-open time and keeps it
  bounded. The still-forming bar (the newest one when its open time is not
  yet a full bar old relative to the newest tick) is tracked separately and
  NEVER enters the closed series — closed-bar evaluation only (SPEC C7).
- **Sanity checks** log every anomaly and gate evaluation:
  missing bars (gap > 1 bar inside the trading week), zero tick volume,
  price spikes > N x ATR, weekend gaps (expected around closures - logged,
  never fatal), broker time jumps (bar times moving backwards), stale ticks
  (no fresh tick for longer than the bar duration).
  A ``SPIKE`` or ``TIME_JUMP`` on the newest closed bar marks the symbol's
  bar as *not evaluable* for that update — downstream skips it (SPEC C2.3).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC
from typing import Protocol

from app.analysis.broker_time import BrokerClock
from app.analysis.indicators import atr
from app.core.timeframes import Timeframe
from app.mt5.models import RateBar
from app.observability.logger import get_logger

log = get_logger("sync")

DEFAULT_MAX_BARS = 5000


class BarSource(Protocol):
    """Something that can return recent bars (the gateway, or a fake)."""

    def __call__(self, symbol: str, timeframe: Timeframe, count: int) -> Iterable[RateBar]: ...


class SanityIssueKind:
    """String constants for sanity issue kinds (stable for tests/UI)."""

    MISSING_BARS = "missing_bars"
    ZERO_VOLUME = "zero_volume"
    SPIKE = "spike"
    WEEKEND_GAP = "weekend_gap"
    TIME_JUMP = "time_jump"
    STALE_TICK = "stale_tick"


@dataclass(frozen=True, slots=True)
class SanityIssue:
    """One detected data-quality problem (logged, surfaced in the UI)."""

    kind: str
    symbol: str
    timeframe: Timeframe
    bar_time: int  # server epoch of the offending (or first missing) bar
    detail: str


@dataclass(slots=True)
class BarSeries:
    """Ordered closed-bar series for one (symbol, timeframe)."""

    symbol: str
    timeframe: Timeframe
    bars: deque[RateBar] = field(default_factory=deque)
    #: server epoch of the newest tick seen for this symbol (staleness)
    last_tick_epoch: int = 0

    def times(self) -> list[int]:
        return [b.time for b in self.bars]

    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    def highs(self) -> list[float]:
        return [b.high for b in self.bars]

    def lows(self) -> list[float]:
        return [b.low for b in self.bars]

    def volumes(self) -> list[int]:
        return [b.tick_volume for b in self.bars]


def _is_weekend_gap(prev_time: int, time: int, tf_seconds: int) -> bool:
    """True when the gap crosses a weekend closure (Fri close → Mon open).

    Broker wall time encodes a week in which Saturday and Sunday have no
    bars; a gap from Friday to Monday therefore spans 1-3 calendar days
    while an intra-week gap of that size would be suspicious.
    """
    from datetime import datetime, timedelta

    prev_dt = datetime.fromtimestamp(prev_time, tz=UTC)
    dt = datetime.fromtimestamp(time, tz=UTC)
    missing_bars = int((time - prev_time) / tf_seconds) - 1
    if missing_bars < 1:
        return False
    # Walk the calendar days the gap covers; a closure contains Sat/Sun.
    day = prev_dt.date() + timedelta(days=1)
    end = dt.date()
    while day <= end:
        if day.weekday() in (5, 6):
            return True
        day += timedelta(days=1)
    return False


class MarketDataManager:
    """Owns bar series for all watched (symbol, timeframe) pairs."""

    def __init__(
        self,
        *,
        max_bars: int = DEFAULT_MAX_BARS,
        clock: BrokerClock | None = None,
        spike_atr_factor: float = 6.0,
    ) -> None:
        self._max_bars = max_bars
        self.clock = clock or BrokerClock(utc_now_fn=lambda: 0.0)
        self._spike_atr_factor = spike_atr_factor
        self._series: dict[tuple[str, Timeframe], BarSeries] = {}

    # -- access -----------------------------------------------------------------
    def series(self, symbol: str, timeframe: Timeframe) -> BarSeries:
        """Return (creating if needed) the series for a pair."""
        key = (symbol, timeframe)
        if key not in self._series:
            self._series[key] = BarSeries(symbol, timeframe)
        return self._series[key]

    def symbols(self) -> list[str]:
        return sorted({s for s, _ in self._series})

    # -- ingestion ----------------------------------------------------------------
    def ingest(
        self,
        symbol: str,
        timeframe: Timeframe,
        bars: Iterable[RateBar],
        *,
        forming_open_time: int | None = None,
    ) -> list[SanityIssue]:
        """Merge a fetched window into the closed-bar cache.

        ``forming_open_time`` — server epoch of the still-forming bar's
        open (from the newest tick); bars with ``time >= forming_open_time``
        are excluded from the closed series. ``None`` keeps every bar.
        Returns the sanity issues detected during this update.
        """
        ser = self.series(symbol, timeframe)
        existing = {b.time: b for b in ser.bars}
        new_bars: list[RateBar] = []
        for bar in bars:
            if forming_open_time is not None and bar.time >= forming_open_time:
                continue
            if bar.time in existing:
                continue
            new_bars.append(bar)
            existing[bar.time] = bar

        new_bars.sort(key=lambda b: b.time)
        issues: list[SanityIssue] = []
        if new_bars:
            issues.extend(self._check_against_history(ser, new_bars, timeframe))
            ser.bars.extend(new_bars)
            while len(ser.bars) > self._max_bars:
                ser.bars.popleft()
            log.debug(
                "analysis: {} {} +{} closed bar(s) (total {})",
                symbol,
                timeframe.value,
                len(new_bars),
                len(ser.bars),
            )
        return issues

    def mark_tick(self, symbol: str, server_epoch: int) -> SanityIssue | None:
        """Record the newest tick epoch for a symbol; detect staleness."""
        newest: BarSeries | None = None
        for ser in self._series.values():
            if ser.symbol == symbol and (
                newest is None or ser.timeframe.seconds < newest.timeframe.seconds
            ):
                newest = ser
        if newest is None:
            return None
        previous = newest.last_tick_epoch
        newest.last_tick_epoch = server_epoch
        if previous and server_epoch <= previous:
            return None  # equal/backwards duplicate — ignore
        tf_seconds = newest.timeframe.seconds
        if previous and (server_epoch - previous) > 3 * tf_seconds:
            issue = SanityIssue(
                SanityIssueKind.STALE_TICK,
                symbol,
                newest.timeframe,
                server_epoch,
                f"no tick for {server_epoch - previous}s (> 3 x {newest.timeframe.value})",
            )
            log.warning("analysis: {}", issue.detail)
            return issue
        return None

    # -- sanity ----------------------------------------------------------------------
    def _check_against_history(
        self,
        ser: BarSeries,
        new_bars: list[RateBar],
        timeframe: Timeframe,
    ) -> list[SanityIssue]:
        tf_seconds = timeframe.seconds
        issues: list[SanityIssue] = []
        prev: RateBar | None = ser.bars[-1] if ser.bars else None

        for bar in new_bars:
            # backwards / duplicate times already de-duped, but ordering
            # against history must hold — a backwards time is a data fault.
            if prev is not None and bar.time <= prev.time:
                issues.append(
                    SanityIssue(
                        SanityIssueKind.TIME_JUMP,
                        ser.symbol,
                        timeframe,
                        bar.time,
                        f"bar time {bar.time} not after previous {prev.time}",
                    )
                )
                prev = bar
                continue

            if prev is not None:
                gap_bars = round((bar.time - prev.time) / tf_seconds) - 1
                if gap_bars > 0:
                    if _is_weekend_gap(prev.time, bar.time, tf_seconds):
                        issues.append(
                            SanityIssue(
                                SanityIssueKind.WEEKEND_GAP,
                                ser.symbol,
                                timeframe,
                                bar.time,
                                f"weekend closure: {gap_bars} bar(s) missing",
                            )
                        )
                    else:
                        issues.append(
                            SanityIssue(
                                SanityIssueKind.MISSING_BARS,
                                ser.symbol,
                                timeframe,
                                bar.time,
                                f"{gap_bars} bar(s) missing before {bar.time}",
                            )
                        )

            if bar.tick_volume <= 0:
                issues.append(
                    SanityIssue(
                        SanityIssueKind.ZERO_VOLUME,
                        ser.symbol,
                        timeframe,
                        bar.time,
                        "zero tick volume",
                    )
                )
            prev = bar

        issues.extend(self._check_spikes(ser, new_bars, timeframe))
        return issues

    def _check_spikes(
        self,
        ser: BarSeries,
        new_bars: list[RateBar],
        timeframe: Timeframe,
    ) -> list[SanityIssue]:
        """Flag bars whose range exceeds N x the recent ATR."""
        history = list(ser.bars)
        if len(history) < 20:
            return []  # not enough context to judge
        issues: list[SanityIssue] = []
        context = history[-200:]
        for bar in new_bars:
            highs = [b.high for b in context]
            lows = [b.low for b in context]
            closes = [b.close for b in context]
            atr_value = atr(highs, lows, closes, period=14)[-1]
            if not math.isfinite(atr_value) or atr_value <= 0.0:
                continue
            bar_range = bar.high - bar.low
            if bar_range > self._spike_atr_factor * atr_value:
                issues.append(
                    SanityIssue(
                        SanityIssueKind.SPIKE,
                        ser.symbol,
                        timeframe,
                        bar.time,
                        f"range {bar_range:.5f} > {self._spike_atr_factor} x ATR {atr_value:.5f}",
                    )
                )
        return issues

    # -- evaluation gate -------------------------------------------------------------
    def evaluable(
        self,
        symbol: str,
        timeframe: Timeframe,
        issues: Iterable[SanityIssue],
    ) -> bool:
        """False when the newest closed bar must NOT be evaluated.

        A spike or a time jump on the newest bar poisons the evaluation;
        missing/weekend gaps and zero volume only downgrade quality (logged
        and surfaced, but a single quiet zero-volume bar is normal on some
        brokers).
        """
        newest_time = (
            self.series(symbol, timeframe).bars[-1].time
            if self.series(symbol, timeframe).bars
            else None
        )
        if newest_time is None:
            return False
        for issue in issues:
            if (
                issue.kind in (SanityIssueKind.SPIKE, SanityIssueKind.TIME_JUMP)
                and issue.bar_time == newest_time
            ):
                log.warning(
                    "analysis: {} {} bar {} not evaluable ({})",
                    symbol,
                    timeframe.value,
                    newest_time,
                    issue.kind,
                )
                return False
        return True
