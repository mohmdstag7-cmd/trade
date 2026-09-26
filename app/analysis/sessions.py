"""Trading sessions in broker wall time (SPEC C3.5).

Windows are configurable; defaults follow the widely used UTC anchors and
are expressed in BROKER wall-clock hours (SPEC C2.4: bar times are broker
server time). The clock handles windows that wrap midnight and exposes the
current session plus the time to the next transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.mt5.models import RateBar

#: Default windows in broker wall hours, end-exclusive, may wrap midnight.
#: Asia 00:00-07:00 matches the London Breakout reference strategy (C4).
DEFAULT_SESSIONS: dict[str, tuple[int, int]] = {
    "Asia": (0, 7),
    "London": (7, 15),
    "New York": (12, 20),
}


@dataclass(frozen=True, slots=True)
class SessionState:
    current: str  # "Asia" | "London" | "New York" | "Off"
    session_start_epoch: int | None
    next_transition_epoch: int
    next_transition_name: str


def _hour_of(server_epoch: float) -> int:
    return datetime.fromtimestamp(server_epoch, tz=UTC).hour


def _start_of_next_hour(server_epoch: float) -> int:
    dt = datetime.fromtimestamp(server_epoch, tz=UTC)
    nxt = dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return int(nxt.timestamp())


class SessionClock:
    """Session state machine over broker-encoded epochs."""

    def __init__(self, windows: dict[str, tuple[int, int]] | None = None) -> None:
        # deterministic priority: the window that started most recently wins
        self._windows = dict(windows or DEFAULT_SESSIONS)

    def _session_at(self, hour: int) -> str | None:
        for name, (start, end) in self._windows.items():
            if start < end:
                if start <= hour < end:
                    return name
            elif hour >= start or hour < end:
                return name
        return None

    def state(self, server_epoch: int) -> SessionState:
        hour = _hour_of(server_epoch)
        current = self._session_at(hour)
        # walk forward hour by hour (at most 24 steps) to the next boundary
        probe = server_epoch
        for _ in range(25):
            probe = _start_of_next_hour(probe)
            next_hour = _hour_of(probe)
            candidate = self._session_at(next_hour)
            if candidate != current:
                return SessionState(
                    current=current or "Off",
                    session_start_epoch=None,
                    next_transition_epoch=probe,
                    next_transition_name=candidate or "Off",
                )
        return SessionState(
            current=current or "Off",
            session_start_epoch=None,
            next_transition_epoch=_start_of_next_hour(server_epoch),
            next_transition_name=current or "Off",
        )

    def session_start(self, server_epoch: int, session: str) -> int | None:
        """Epoch of the most recent start of ``session`` at/before epoch."""
        window = self._windows.get(session)
        if window is None:
            return None
        start_hour = window[0]
        dt = datetime.fromtimestamp(server_epoch, tz=UTC)
        candidate = dt.replace(hour=start_hour % 24, minute=0, second=0, microsecond=0)
        if start_hour >= 24 or (candidate.timestamp() > server_epoch and dt.hour < start_hour):
            candidate -= timedelta(days=1)
        if candidate.timestamp() > server_epoch:
            candidate -= timedelta(days=1)
        return int(candidate.timestamp())

    def session_extremes(
        self,
        session: str,
        h1_bars: list[RateBar],
        server_epoch: int,
    ) -> tuple[float, float] | None:
        """(high, low) of ``session`` for its most recent occurrence."""
        start = self.session_start(server_epoch, session)
        if start is None:
            return None
        window = self._windows[session]
        duration_h = window[1] - window[0] if window[1] > window[0] else 24 - window[0] + window[1]
        end = start + duration_h * 3600
        bars = [b for b in h1_bars if start <= b.time < end]
        if not bars:
            return None
        return max(b.high for b in bars), min(b.low for b in bars)
