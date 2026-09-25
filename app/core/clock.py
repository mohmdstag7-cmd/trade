"""Time helpers.

MT5 bar times are broker server time (SPEC C2.4); everything is stored in
UTC and displayed in local time. Phase 1 only needs UTC now + local display;
broker-offset detection arrives with Phase 5.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=UTC)


def format_local_time(dt: datetime | None = None) -> str:
    """Format a datetime (default: now) as a local ``HH:MM:SS`` string."""
    if dt is None:
        dt = utc_now()
    return dt.astimezone().strftime("%H:%M:%S")
