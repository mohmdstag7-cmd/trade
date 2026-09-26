"""Broker server time vs UTC (SPEC C2.4).

MetaTrader5 reports bar/tick times as seconds since 1970 **in the broker
server's timezone** — i.e. the epoch value treats server-local wall time as
if it were UTC. To reason about sessions, calendars and daily limits we
detect the broker's UTC offset and expose conversion helpers.

Detection compares a fresh tick's server-encoded time against real UTC:
``offset ≈ round((tick_epoch - utc_now) / 900) * 900`` minutes. Rounding to
15-minute quanta absorbs network/processing latency; several samples vote
so a single noisy tick cannot flip the answer. When the majority offset
differs from the current one the clock announces a DST change (broker
offsets move in whole hours; 30-minute zones keep their quantum).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC

from app.observability.logger import get_logger

log = get_logger("sync")

#: Quantum for offset detection (minutes) — absorbs latency.
QUANTUM_MIN = 15
#: Consecutive differing samples required before announcing a DST change.
DST_CONFIRM_SAMPLES = 3

UtcNowFn = Callable[[], float]


@dataclass(slots=True)
class BrokerClock:
    """Tracks the broker server's UTC offset from live tick samples."""

    utc_now_fn: UtcNowFn
    offset_minutes: int = 0
    detected: bool = False
    _samples_seen: int = 0
    _candidate_offset: int | None = None
    _candidate_votes: int = 0

    def sample(self, server_epoch: float) -> bool:
        """Feed one tick sample. Returns True when the offset (re)detected.

        A sample whose distance to the current offset is NOT a whole number
        of hours is ignored once a stable offset exists (bad clock / winter
        time in a weekend gap), so only genuine DST moves re-arm detection.
        """
        raw_minutes = (server_epoch - self.utc_now_fn()) / 60.0
        quantized = round(raw_minutes / QUANTUM_MIN) * QUANTUM_MIN
        # Clamp to the plausible broker range: UTC-12 .. UTC+14.
        quantized = max(-12 * 60, min(14 * 60, quantized))
        self._samples_seen += 1

        if self.detected and quantized == self.offset_minutes:
            return False

        if self._candidate_offset == quantized:
            self._candidate_votes += 1
        else:
            self._candidate_offset = quantized
            self._candidate_votes = 1

        threshold = DST_CONFIRM_SAMPLES if self.detected else 1
        if self._candidate_votes >= threshold:
            changed = self.detected and self.offset_minutes != quantized
            whole_hour_shift = self.detected and (quantized - self.offset_minutes) % 60 == 0
            self.offset_minutes = quantized
            self.detected = True
            self._candidate_votes = 0
            if changed and whole_hour_shift:
                log.info(
                    "analysis: broker UTC offset changed to {:+d} min (DST)",
                    self.offset_minutes,
                )
            elif changed:
                log.warning(
                    "analysis: broker UTC offset changed to {:+d} min "
                    "(unexpected shift — re-check broker settings)",
                    self.offset_minutes,
                )
            return True
        return False

    # -- conversions -----------------------------------------------------------
    @property
    def offset_seconds(self) -> int:
        return self.offset_minutes * 60

    def to_utc(self, server_epoch: float) -> float:
        """Convert a server-encoded epoch to a true UTC epoch."""
        return server_epoch - self.offset_seconds

    def to_server(self, utc_epoch: float) -> float:
        """Convert a true UTC epoch to the server-encoded epoch."""
        return utc_epoch + self.offset_seconds

    def utc_offset_string(self) -> str:
        """Human-readable offset like ``UTC+03:30`` (or ``UTC`` at zero)."""
        minutes = self.offset_minutes
        sign = "+" if minutes >= 0 else "-"
        minutes = abs(minutes)
        if minutes == 0:
            return "UTC"
        return f"UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"

    def broker_day(self, utc_epoch: float) -> str:
        """The broker trading day (YYYY-MM-DD) a UTC instant belongs to."""
        from datetime import datetime

        server = datetime.fromtimestamp(self.to_server(utc_epoch), tz=UTC)
        return server.strftime("%Y-%m-%d")
