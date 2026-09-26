"""Opportunity scanner: rank symbols by setup state (SPEC C3.11).

State rules (informational ranking, NOT signals):
- ``ready``   — |bias| ≥ 50, H1 not in a range, volatility not LOW and no
  high-impact event blocking.
- ``forming`` — |bias| ≥ 25 with no blockers, or ``ready`` conditions minus
  one (e.g. volatility still LOW).
- ``none``    — everything else.

The score is a documented PROXY for "probability x EV" until the ML layer
(C10) supplies calibrated numbers: ``|bias| x proximity`` where proximity
decays with the distance to the nearest key level (1.0 within 0.5 ATR →
0.0 at 3 ATR).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.levels import Level, nearest_levels


@dataclass(frozen=True, slots=True)
class ScanEntry:
    symbol: str
    state: str  # ready / forming / none
    score: float  # 0..100 proxy
    bias_score: int
    reasons: tuple[str, ...] = ()


def _proximity(distance_atr: float | None) -> float:
    if distance_atr is None:
        return 0.5  # unknown → neutral middle
    if distance_atr <= 0.5:
        return 1.0
    if distance_atr >= 3.0:
        return 0.0
    return 1.0 - (distance_atr - 0.5) / 2.5


def scan_symbol(
    symbol: str,
    bias_score: int,
    h1_trend_label: str,
    volatility_regime: str,
    event_blocked: bool,
    levels: list[Level],
    last_close: float,
    atr_value: float,
) -> ScanEntry:
    """Compute the scan state/score for ONE symbol."""
    reasons: list[str] = []
    if event_blocked:
        reasons.append("analysis.scan.event_blocked")
    if volatility_regime == "low":
        reasons.append("analysis.scan.low_volatility")
    if h1_trend_label == "range":
        reasons.append("analysis.scan.range")

    bias = abs(bias_score)
    distance: float | None = None
    if levels and atr_value > 0:
        nearest = nearest_levels(levels, last_close, atr_value, max_count=1)
        if nearest:
            distance = nearest[0][1]

    blockers = bool(reasons)
    if bias >= 50 and not blockers:
        state = "ready"
    elif bias >= 25 and not event_blocked:
        state = "forming"
        if reasons:
            pass
        if not reasons:
            reasons.append("analysis.scan.bias_building")
    else:
        state = "none"
        if not reasons:
            reasons.append("analysis.scan.no_edge")

    score = round(bias * _proximity(distance), 1)
    return ScanEntry(symbol, state, score, bias_score, tuple(reasons))


def rank(entries: list[ScanEntry]) -> list[ScanEntry]:
    """Ready first, then forming, then none; score descending inside."""
    order = {"ready": 0, "forming": 1, "none": 2}
    return sorted(entries, key=lambda e: (order.get(e.state, 3), -e.score))
