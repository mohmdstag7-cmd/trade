"""Market analysis orchestrator (SPEC C3 pipeline driver).

Owns the analysis state for the watched symbols and drives it from the
gateway in small, non-blocking steps:

``refresh_now()`` submits bounded ``rates_from_pos`` fetches + a tick per
(symbol, timeframe) through the shared gateway futures. ``poll()`` drains
completed futures, ingests closed bars into the manager and — only when a
NEW closed bar appeared — recomputes the full per-symbol analysis (trend
matrix, structure, levels, volatility, sessions, patterns, card) plus the
cross-symbol correlation/currency-strength snapshot and the scanner rank.

Threading: the service runs on the caller's (UI) thread but never blocks —
every MT5 touch is a gateway future; heavy numpy work runs only on
closed-bar changes (3 symbols x 4 timeframes of 60-300 bars, a few ms).
The gateway is accessed through the narrow :class:`MarketDataGateway`
protocol so tests inject instant futures without MetaTrader5.
"""

from __future__ import annotations

import math
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.analysis import patterns as patterns_mod
from app.analysis import structure as structure_mod
from app.analysis import volatility as volatility_mod
from app.analysis.broker_time import BrokerClock
from app.analysis.card import AnalysisCard, CardBuilder, NextEvent
from app.analysis.correlation import CorrelationMatrix, currency_strength, rolling_correlation
from app.analysis.levels import (
    Level,
    cluster_levels,
    prev_day_levels,
    prev_week_levels,
    round_numbers,
    session_levels,
)
from app.analysis.market_data import (
    MarketDataManager,
    SanityIssue,
    SanityIssueKind,
)
from app.analysis.scanner import ScanEntry, scan_symbol
from app.analysis.sessions import DEFAULT_SESSIONS, SessionClock
from app.analysis.spread import SpreadMonitor
from app.analysis.trend import TrendMatrix, build_matrix
from app.calendar.events import EventStore
from app.calendar.importer import ExporterFilePoller
from app.core.timeframes import ANALYSIS_TIMEFRAMES, Timeframe
from app.mt5.models import RateBar, TickSnapshot
from app.mt5.models import SymbolSnapshot as Mt5SymbolInfo
from app.mt5.symbols import SymbolResolver
from app.observability.logger import get_logger

log = get_logger("sync")

#: Bars fetched per timeframe (bounded copy_rates_from_pos counts).
FETCH_COUNTS: dict[Timeframe, int] = {
    Timeframe.M15: 300,
    Timeframe.H1: 400,
    Timeframe.H4: 300,
    Timeframe.D1: 260,
    Timeframe.W1: 60,
}

DEFAULT_WATCHED: tuple[str, ...] = ("EURUSD", "GBPUSD", "XAUUSD")

#: consecutive failed fetch batches before a symbol is parked as unusable
_MAX_FETCH_FAILURES = 3


class MarketDataGateway(Protocol):
    """The slice of the gateway the analysis service needs."""

    def rates_from_pos(self, symbol: str, timeframe: str, start_pos: int, count: int) -> Future: ...

    def tick(self, symbol: str) -> Future: ...

    def symbol_info(self, symbol: str) -> Future: ...

    def symbol_names(self) -> Future: ...

    def select_symbol(self, symbol: str) -> Future: ...


@dataclass(frozen=True, slots=True)
class SymbolSnapshot:
    """Everything the Market page renders for one symbol."""

    symbol: str
    digits: int
    card: AnalysisCard
    trend: TrendMatrix
    levels: tuple[Level, ...]
    volatility: volatility_mod.VolatilitySnapshot
    session_name: str
    session_extremes: tuple[float, float] | None
    spread_points: float | None
    spread_typical: float | None
    spread_abnormal: bool
    patterns: tuple[str, ...]
    structure_events: tuple[structure_mod.StructureEvent, ...]
    last_close: float | None
    last_price: float | None
    issues: tuple[str, ...]
    broker_offset: str
    bar_count: int


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """The full analysis state rendered by the Market page."""

    symbols: tuple[SymbolSnapshot, ...]
    correlation: CorrelationMatrix | None
    strength: tuple[tuple[str, float], ...]
    scan: tuple[ScanEntry, ...]
    #: canonical names this broker does not list (suffix resolution failed)
    unresolved: tuple[str, ...] = ()

    def by_symbol(self, symbol: str) -> SymbolSnapshot | None:
        for s in self.symbols:
            if s.symbol == symbol:
                return s
        return None


@dataclass(slots=True)
class _Pending:
    """One symbol's in-flight fetch batch."""

    symbol: str
    futures: dict[tuple[str, int], Future] = field(default_factory=dict)
    tick_future: Future | None = None
    info_future: Future | None = None


class MarketAnalysisService:
    """Drives the C3 pipeline for the watched symbols."""

    def __init__(
        self,
        gateway: MarketDataGateway | None,
        *,
        watched: tuple[str, ...] = DEFAULT_WATCHED,
        clock: BrokerClock | None = None,
        calendar_store: EventStore | None = None,
        calendar_poller: ExporterFilePoller | None = None,
        monotonic_fn: object = time.monotonic,
    ) -> None:
        self._gateway = gateway
        self._watched = watched
        self.manager = MarketDataManager(clock=clock or BrokerClock(utc_now_fn=_zero))
        self.clock = self.manager.clock
        self.spread_monitor = SpreadMonitor()
        self.sessions = SessionClock(DEFAULT_SESSIONS)
        self._card_builder = CardBuilder(spread_monitor=self.spread_monitor)
        self.calendar = calendar_store or EventStore()
        self.calendar_poller = calendar_poller
        self._mono = monotonic_fn if callable(monotonic_fn) else time.monotonic
        self._digits: dict[str, int] = dict.fromkeys(watched, 5)
        self._pending: dict[str, _Pending] = {}
        self._last_bar_time: dict[str, int] = {}
        self._snapshot: MarketSnapshot | None = None
        self._symbol_snapshots: dict[str, SymbolSnapshot] = {}
        self._spread_state: dict[str, tuple[float | None, float | None, bool]] = {}
        self.last_refresh_monotonic = 0.0
        # -- broker symbol resolution (canonical -> decorated name) ----------
        self._resolver = SymbolResolver()
        self._mapping: dict[str, str] = {}
        self._ready: set[str] = set()
        self._unresolved: dict[str, str] = {}
        self._resolve_future: Future | None = None
        self._select_futures: dict[str, Future] = {}
        self._resolved_once = False
        self._fetch_failures: dict[str, int] = {}
        # symbols whose first successful fetch batch has been announced in
        # the log this session (cleared on disconnect/invalidate)
        self._announced_ready: set[str] = set()
        # recent sanity issues per symbol (bounded, newest last) feeding the
        # evaluable() gate and the card's data_issues
        self._recent_issues: dict[str, list[SanityIssue]] = {}
        # newest H1 ATR per symbol — the scanner scores proximity in H1-ATR
        # units, consistent with the card's distance_atr
        self._atr_h1: dict[str, float] = {}

    # -- public API ---------------------------------------------------------------
    @property
    def snapshot(self) -> MarketSnapshot | None:
        return self._snapshot

    def has_pending(self) -> bool:
        return bool(self._pending)

    def invalidate_symbols(self) -> None:
        """Drop broker symbol mapping and all cached analysis.

        Called when the gateway drops its session: the next connect may be a
        different broker account with different symbol decorations, and the
        cached bars would silently mix two price feeds.
        """
        self._resolver.invalidate()
        self._mapping.clear()
        self._ready.clear()
        self._unresolved.clear()
        self._resolve_future = None
        self._select_futures.clear()
        self._pending.clear()
        self._last_bar_time.clear()
        self._symbol_snapshots.clear()
        self._snapshot = None
        self._spread_state.clear()
        self._resolved_once = False
        self._fetch_failures.clear()
        self._announced_ready.clear()
        self._recent_issues.clear()
        self._atr_h1.clear()
        for symbol in self._watched:
            for tf in (*ANALYSIS_TIMEFRAMES, Timeframe.W1):
                self.manager._series.pop((symbol, tf), None)

    # -- symbol resolution ---------------------------------------------------------
    def _gateway_state_connected(self, gateway: Any) -> bool | None:
        """True/False from ``gateway.state``; ``None`` when not exposed.

        The real gateway exposes ``state`` as a property (an enum instance);
        some test fakes expose it as a method. Accept both shapes.
        """
        raw = getattr(gateway, "state", None)
        if raw is None:
            return None
        from app.mt5.models import ConnectionState

        state = raw() if callable(raw) else raw
        return state is ConnectionState.CONNECTED

    def _pump_resolution(self, gateway: Any) -> set[str]:
        """Drive symbol_names -> resolve -> select_symbol until ready.

        Returns the canonical symbols that became ready in this call (the
        caller submits their first fetch batches immediately). Runs exactly
        one resolution pass per session; afterwards failures are surfaced
        via ``_unresolved`` instead of re-querying forever.
        """
        if not self._mapping and not self._resolved_once:
            names_fn = getattr(gateway, "symbol_names", None)
            if names_fn is None:
                # Legacy fake gateway without resolution support: assume the
                # canonical names are exact broker names (old behaviour).
                self._mapping = {s: s for s in self._watched}
                self._ready = set(self._watched)
                self._resolved_once = True
                return set(self._ready)
            if self._resolve_future is None or self._resolve_future.done():
                if self._resolve_future is None:
                    self._resolve_future = names_fn()
                else:
                    try:
                        names = self._resolve_future.result()
                    except Exception as exc:
                        log.warning("analysis: symbol_names failed: {}", exc)
                        names = ()
                    self._resolve_future = None
                    self._resolved_once = True
                    resolved = self._resolver.resolve_all(self._watched, names)
                    self._mapping.update({c: r.broker_symbol for c, r in resolved.items()})
                    for canonical in self._watched:
                        if canonical not in resolved:
                            self._unresolved[canonical] = (
                                f"not listed by this broker (looked for {canonical} + suffix)"
                            )
                    # One-line resolution verdict: the Market page staying
                    # empty used to be invisible in user-supplied logs.
                    if self._mapping:
                        detail = ", ".join(f"{c}→{b}" for c, b in self._mapping.items())
                        log.info(
                            "analysis: resolved {}/{} watched symbols ({})",
                            len(self._mapping),
                            len(self._watched),
                            detail,
                        )
                    if self._unresolved:
                        log.warning(
                            "analysis: unresolved symbols: {}",
                            ", ".join(sorted(self._unresolved)),
                        )
                    for canonical, broker in self._mapping.items():
                        select_fn = getattr(gateway, "select_symbol", None)
                        if select_fn is None:
                            self._ready.add(canonical)
                        else:
                            self._select_futures[canonical] = select_fn(broker)
            return set()
        newly_ready: set[str] = set()
        for canonical in list(self._select_futures):
            future = self._select_futures[canonical]
            if not future.done():
                continue
            del self._select_futures[canonical]
            try:
                future.result()
                self._ready.add(canonical)
                newly_ready.add(canonical)
            except Exception as exc:
                log.warning("analysis: symbol_select {} failed: {}", canonical, exc)
                self._mapping.pop(canonical, None)
                self._unresolved[canonical] = str(exc) or "symbol_select failed"
        return newly_ready

    def refresh_now(self) -> None:
        """Submit fetch batches for every watched symbol (non-blocking)."""
        gateway = self._gateway
        if gateway is None:
            return
        connected = self._gateway_state_connected(gateway)
        if connected is False:
            return  # offline: the page shows its empty state
        self.last_refresh_monotonic = float(self._mono())
        self._pump_resolution(gateway)
        self._submit_ready_batches(gateway)

    def _submit_ready_batches(self, gateway: Any) -> None:
        """Queue a fetch batch per resolved symbol that has none in flight."""
        for symbol in self._ready:
            if symbol in self._pending:
                continue
            broker = self._mapping.get(symbol, symbol)
            pending = _Pending(symbol)
            for tf in (*ANALYSIS_TIMEFRAMES, Timeframe.W1):
                pending.futures[(tf.gateway, FETCH_COUNTS[tf])] = gateway.rates_from_pos(
                    broker, tf.gateway, 0, FETCH_COUNTS[tf]
                )
            pending.tick_future = gateway.tick(broker)
            pending.info_future = gateway.symbol_info(broker)
            self._pending[symbol] = pending

    def poll(self) -> MarketSnapshot | None:
        """Drain completed futures and (re)compute analysis when due."""
        if self.calendar_poller is not None:
            added, _errors = self.calendar_poller.poll(float(self._mono()))
            if added:
                self._recompute_all(force=True)
        if self._gateway is not None and self._gateway_state_connected(self._gateway) is not False:
            newly_ready = self._pump_resolution(self._gateway)
            if newly_ready:
                # resolution just completed — don't wait for the next
                # refresh tick (up to a minute of an empty Market page)
                self._submit_ready_batches(self._gateway)
        if not self._pending:
            return self._snapshot
        for symbol in list(self._pending):
            if self._drain(symbol):
                self._compute_symbol(symbol)
        if not self._pending:
            self._recompute_cross()
        return self._snapshot

    # -- internals: draining ------------------------------------------------------
    def _drain(self, symbol: str) -> bool:
        """Ingest a symbol's fetch batch once every future completed."""
        pending = self._pending.get(symbol)
        if pending is None:
            return False

        # Mark the tick BEFORE ingesting rates: the forming-bar heuristic
        # compares bar age against the newest tick. Ingesting first would
        # let the still-forming bar slip into the closed series on the very
        # first batch (no tick epoch yet) — and ingest() never revises a
        # known bar, so a partial OHLC would be frozen forever (stale PDH/
        # PDL/indicators). The tick sample also feeds the broker-clock
        # offset detection used by the wall-clock fallback below.
        if pending.tick_future is not None and pending.tick_future.done():
            try:
                tick: TickSnapshot = pending.tick_future.result()
                self.manager.mark_tick(symbol, tick.time)
                self.clock.sample(float(tick.time))
                self._record_spread(symbol, tick)
            except Exception as exc:
                log.warning("analysis: tick {} failed: {}", symbol, exc)

        for key, future in pending.futures.items():
            if not future.done():
                return False
            tf_gateway, count = key
            tf = Timeframe(tf_gateway)
            try:
                bars: tuple[RateBar, ...] = future.result()
            except Exception as exc:  # gateway errors surface in the UI elsewhere
                log.warning("analysis: fetch {} {} failed: {}", symbol, tf.value, exc)
                self._pending.pop(symbol, None)
                self._note_fetch_failure(symbol, exc)
                return False
            issues = self._ingest_closed(symbol, tf, bars, count)
            if issues:
                self._recent_issues.setdefault(symbol, []).extend(issues)
                # Bounded: keep only the newest 20 issues per symbol.
                del self._recent_issues[symbol][:-20]
            if any(i.kind == SanityIssueKind.SPIKE for i in issues):
                log.debug("analysis: spike issues on {} {}", symbol, tf.value)

        if pending.info_future is not None and pending.info_future.done():
            try:
                info: Mt5SymbolInfo = pending.info_future.result()
                self._digits[symbol] = int(info.digits)
            except Exception as exc:
                log.warning("analysis: symbol_info {} failed: {}", symbol, exc)
        self._fetch_failures[symbol] = 0
        self._pending.pop(symbol, None)
        if symbol not in self._announced_ready:
            self._announced_ready.add(symbol)
            log.info(
                "analysis: {} data ready across {} timeframes",
                symbol,
                len(pending.futures),
            )
        return True

    def _note_fetch_failure(self, symbol: str, exc: Exception) -> None:
        """Escalate a symbol to 'unresolved' after repeated dead fetches.

        A listed-but-empty symbol (no history, refused select) would otherwise
        retry forever, spamming the log every refresh cycle.
        """
        failures = self._fetch_failures.get(symbol, 0) + 1
        self._fetch_failures[symbol] = failures
        if failures < _MAX_FETCH_FAILURES:
            return
        detail = str(exc) or type(exc).__name__
        log.warning("analysis: {} disabled after {} failed fetches ({})", symbol, failures, detail)
        self._ready.discard(symbol)
        self._mapping.pop(symbol, None)
        self._unresolved[symbol] = detail

    def _ingest_closed(
        self, symbol: str, tf: Timeframe, bars: tuple[RateBar, ...], count: int
    ) -> list[SanityIssue]:
        """Drop the forming bar, ingest the rest, return issues."""
        forming_open = None
        if bars:
            newest = bars[-1]
            latest_closed = (
                self.manager.series(symbol, tf).bars[-1].time
                if self.manager.series(symbol, tf).bars
                else 0
            )
            if newest.time > latest_closed and self._maybe_forming(symbol, tf, newest):
                forming_open = newest.time
        return self.manager.ingest(symbol, tf, bars, forming_open_time=forming_open)

    def _maybe_forming(self, symbol: str, tf: Timeframe, newest: RateBar) -> bool:
        """Heuristic: the newest fetched bar is 'forming' when its open time
        is not yet a full bar behind the newest tick (or the wall clock).

        Wall-clock fallback (only with a detected broker offset): without it
        the first batch of a session would treat the still-forming bar as
        closed and freeze its partial OHLC in the series forever.
        """
        newest_tick = 0
        for ser in self.manager._series.values():
            if ser.symbol == symbol:
                newest_tick = max(newest_tick, ser.last_tick_epoch)
        if newest_tick:
            age = newest_tick - newest.time
            return age < tf.seconds
        if self.clock.detected:
            # A bar whose open is within one bar-period of "now" (or that
            # appears to open in the future — clock skew) is still forming.
            server_now = self.clock.to_server(self.clock.utc_now_fn())
            return server_now - newest.time < tf.seconds
        return False  # no tick and no offset yet: conservative, treat as closed

    def _record_spread(self, symbol: str, tick: TickSnapshot) -> None:
        from datetime import UTC, datetime

        info_digits = self._digits.get(symbol, 5)
        point = 10 ** (-info_digits)
        spread_points = (tick.ask - tick.bid) / point if point else 0.0
        hour = datetime.fromtimestamp(tick.time, tz=UTC).hour
        abnormal = self.spread_monitor.update(symbol, hour, spread_points)
        typical = self.spread_monitor.typical(symbol, hour)
        self._spread_state[symbol] = (spread_points, typical, abnormal)

    # -- internals: analysis ---------------------------------------------------------
    def _compute_symbol(self, symbol: str, *, force: bool = False) -> None:
        bars_by_tf: dict[Timeframe, list[RateBar]] = {}
        for tf in (*ANALYSIS_TIMEFRAMES, Timeframe.W1):
            bars_by_tf[tf] = list(self.manager.series(symbol, tf).bars)
        m15 = bars_by_tf[Timeframe.M15]
        h1 = bars_by_tf[Timeframe.H1]
        d1 = bars_by_tf[Timeframe.D1]
        w1 = bars_by_tf[Timeframe.W1]
        if not m15 or not h1 or not d1:
            return  # not enough data yet

        newest_time = m15[-1].time
        if (
            not force
            and self._snapshot is not None
            and self._last_bar_time.get(symbol) == newest_time
            and not self._pending
        ):
            return  # no new closed bar → keep the previous analysis

        trend = build_matrix(
            {tf: bars for tf, bars in bars_by_tf.items() if tf in ANALYSIS_TIMEFRAMES}
        )

        # structure on H1 (the strategy timeframe anchor)
        from app.analysis.indicators import atr as atr_fn

        h1_highs = [b.high for b in h1]
        h1_lows = [b.low for b in h1]
        h1_closes = [b.close for b in h1]
        swings = structure_mod.find_swings(h1_highs, h1_lows, strength=2)
        structure_events = structure_mod.detect_events(swings, h1_closes)
        atr_h1 = atr_fn(h1_highs, h1_lows, h1_closes, period=14)
        atr_value = float(atr_h1[-1]) if math.isfinite(float(atr_h1[-1])) else 0.0
        self._atr_h1[symbol] = atr_value
        sr_levels = cluster_levels(swings, atr_value) if atr_value > 0 else []
        levels: list[Level] = [
            *sr_levels,
            *prev_day_levels(d1),
            *prev_week_levels(w1),
            *session_levels(h1, DEFAULT_SESSIONS),
            *round_numbers(h1_closes[-1]),
        ]

        d1_highs = [b.high for b in d1]
        d1_lows = [b.low for b in d1]
        d1_closes = [b.close for b in d1]
        atr_d1 = atr_fn(d1_highs, d1_lows, d1_closes, period=14)
        vol = volatility_mod.volatility_snapshot(h1, d1, list(atr_d1))

        now_server = m15[-1].time
        session_name = self.sessions.state(now_server).current
        session_extremes = self.sessions.session_extremes(
            "London" if session_name in ("London", "Off") else session_name, h1, now_server
        )

        patterns = patterns_mod.detect(m15, last_n=3)

        # calendar: next high-impact event touching this symbol's currencies
        next_event = self._next_event(symbol)

        # Data-quality gate (SPEC C2.3): a SPIKE/TIME_JUMP on the newest
        # closed bar poisons the evaluation — keep the previous card instead
        # of publishing garbage. Collect the real issues for the UI.
        issues = self._recent_issues.get(symbol, [])
        if not self.manager.evaluable(symbol, Timeframe.H1, issues):
            log.warning("analysis: {} H1 newest bar not evaluable — keeping previous card", symbol)
            return
        if not self.manager.evaluable(symbol, Timeframe.D1, issues):
            log.warning("analysis: {} D1 newest bar not evaluable — keeping previous card", symbol)
            return
        card = self._card_builder.build(
            symbol=symbol,
            trend=trend,
            h1_bars=h1,
            d1_bars=d1,
            levels=levels,
            volatility=vol,
            session_name=session_name,
            atr_value=atr_value,
            last_close=h1_closes[-1],
            patterns=patterns,
            next_event=next_event,
            issues=issues,
            structure_events=structure_events,
        )

        spread_points, spread_typical, spread_abnormal = self._spread_state.get(
            symbol, (None, None, False)
        )
        snap = SymbolSnapshot(
            symbol=symbol,
            digits=self._digits.get(symbol, 5),
            card=card,
            trend=trend,
            levels=tuple(levels),
            volatility=vol,
            session_name=session_name,
            session_extremes=session_extremes,
            spread_points=spread_points,
            spread_typical=spread_typical,
            spread_abnormal=spread_abnormal,
            patterns=tuple(p.kind.value for p in patterns),
            structure_events=tuple(structure_events[-3:]),
            last_close=h1_closes[-1],
            last_price=h1_closes[-1],
            issues=tuple(str(i.kind) for i in issues[-5:]),
            broker_offset=self.clock.utc_offset_string(),
            bar_count=len(m15),
        )
        self._last_bar_time[symbol] = newest_time
        self._symbol_snapshots[symbol] = snap

    def _next_event(self, symbol: str) -> NextEvent | None:
        from datetime import UTC, datetime

        base, _quote = _split_symbol(symbol)
        currencies = {c for c in (base, _quote) if c}
        now = datetime.now(tz=UTC)
        events = self.calendar.upcoming(
            now,
            within_minutes=24 * 60,
            currencies=currencies or None,
            impact_at_least="medium",
        )
        if not events:
            return None
        event = events[0]
        return NextEvent(
            currency=event.currency,
            title=event.title,
            minutes_until=self.calendar.minutes_until(event, now),
            impact=event.impact,
        )

    def _recompute_all(self, *, force: bool) -> None:
        """Calendar changed → refresh cards only (cheap, no MT5 calls)."""
        for symbol in list(self._symbol_snapshots):
            if force:
                self._compute_symbol(symbol, force=True)

    def _recompute_cross(self) -> None:
        """Correlation + currency strength + scanner over all symbols."""
        daily_closes: dict[str, list[tuple[int, float]]] = {}
        for symbol in self._watched:
            d1_bars = list(self.manager.series(symbol, Timeframe.D1).bars)
            if len(d1_bars) >= 30:
                # (time, close) pairs — correlation aligns by timestamp
                daily_closes[symbol] = [(b.time, b.close) for b in d1_bars]
        correlation = rolling_correlation(daily_closes) if len(daily_closes) >= 2 else None
        plain_closes = {s: [close for _t, close in pairs] for s, pairs in daily_closes.items()}
        strength_scores = currency_strength(plain_closes) if len(plain_closes) >= 2 else {}
        scan: list[ScanEntry] = []
        for symbol, snap in self._symbol_snapshots.items():
            h1_vector = snap.trend.vector(Timeframe.H1)
            h1_label = h1_vector.label if h1_vector else "range"
            event_blocked = (
                snap.card.next_event is not None and snap.card.next_event.impact == "high"
            )
            scan.append(
                scan_symbol(
                    symbol,
                    snap.trend.bias_score,
                    h1_label,
                    snap.volatility.regime.value,
                    event_blocked,
                    list(snap.levels),
                    snap.last_close or 0.0,
                    self._atr_h1.get(symbol, 0.0),
                )
            )
        self._snapshot = MarketSnapshot(
            symbols=tuple(
                self._symbol_snapshots[s] for s in self._watched if s in self._symbol_snapshots
            ),
            correlation=correlation,
            strength=tuple(sorted(strength_scores.items(), key=lambda kv: kv[1], reverse=True)),
            scan=tuple(scan),
            unresolved=tuple(self._unresolved),
        )


def _split_symbol(symbol: str) -> tuple[str, str]:
    s = symbol.upper()
    if len(s) == 6 and s.isalpha():
        return s[:3], s[3:]
    return s, ""


def _zero() -> float:
    return 0.0
