"""Timeframes shared across the whole application (SPEC C2, C4).

The enum lives in ``app.core`` because strategies (C4), the analysis layer
(C3), the gateway adapter and the UI all need it. The ``gateway`` field
holds the exact string the MetaTrader5 package expects.
"""

from __future__ import annotations

from enum import StrEnum


class Timeframe(StrEnum):
    """Supported chart timeframes with their bar duration."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"

    @property
    def seconds(self) -> int:
        """Bar duration in seconds."""
        return _SECONDS[self]

    @property
    def minutes(self) -> int:
        """Bar duration in minutes."""
        return self.seconds // 60

    @property
    def gateway(self) -> str:
        """The enum value is already the MetaTrader5 constant name."""
        return self.value


_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
    Timeframe.W1: 604800,
}

#: Context timeframes used by the analysis pipeline (SPEC B4).
ANALYSIS_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.M15,
    Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
)
