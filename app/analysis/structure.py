"""Market structure: confirmed swings, HH/HL/LH/LL, BOS / CHoCH (SPEC C3.2).

**No look-ahead.** A swing high at index ``i`` requires ``strength`` bars
on each side, so it can only be KNOWN at index ``i + strength``. Every
structure object therefore carries both ``index`` (where the swing is) and
``confirmed_index`` (when it became usable). Downstream consumers must
treat a swing as information only from ``confirmed_index`` onward.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np


class SwingKind(StrEnum):
    HIGH = "high"
    LOW = "low"


class TrendKind(StrEnum):
    UP = "up"
    DOWN = "down"
    RANGE = "range"


class EventType(StrEnum):
    BOS = "bos"  # break of structure (continuation)
    CHOCH = "choch"  # change of character (first break against trend)


@dataclass(frozen=True, slots=True)
class Swing:
    kind: SwingKind
    index: int  # bar index of the swing extremum
    price: float
    confirmed_index: int  # index at which the swing became known

    @property
    def label(self) -> str:
        """HH / HL / LH / LL label assigned by :func:`classify_swings`."""
        return _LABELS.get((self.kind, self.trend_side), "?")

    #: set by classify_swings: "+" for higher, "-" for lower, "" for first
    trend_side: str = ""


_LABELS: dict[tuple[SwingKind, str], str] = {
    (SwingKind.HIGH, "+"): "HH",
    (SwingKind.HIGH, "-"): "LH",
    (SwingKind.HIGH, ""): "H",
    (SwingKind.LOW, "+"): "HL",
    (SwingKind.LOW, "-"): "LL",
    (SwingKind.LOW, ""): "L",
}


@dataclass(frozen=True, slots=True)
class StructureEvent:
    type: EventType
    index: int  # bar whose CLOSE broke the level
    level: float  # the broken swing price
    broken: Swing  # the swing that was broken
    direction: int  # +1 bullish break, -1 bearish break


def find_swings(
    highs: np.ndarray | list[float],
    lows: np.ndarray | list[float],
    strength: int = 2,
) -> list[Swing]:
    """Detect swing highs/lows with a confirmation delay of ``strength``.

    A swing high at ``i``: ``highs[i]`` strictly greater than the
    ``strength`` highs on each side (ties -> not a swing). Interleaved
    chronologically; ``confirmed_index = index + strength``.
    """
    h = np.asarray(highs, dtype=np.float64)
    lows_a = np.asarray(lows, dtype=np.float64)
    n = h.shape[0]
    if h.shape[0] != lows_a.shape[0]:
        raise ValueError("highs/lows length mismatch")
    if strength < 1:
        raise ValueError("strength must be >= 1")
    raw: list[tuple[int, Swing]] = []
    for i in range(n):
        lo = i - strength
        hi = i + strength
        if lo < 0 or hi >= n:
            continue  # not yet confirmable — no look-ahead
        window_h = np.concatenate((h[lo:i], h[i + 1 : hi + 1]))
        window_l = np.concatenate((lows_a[lo:i], lows_a[i + 1 : hi + 1]))
        if h[i] > window_h.max() and lows_a[i] < window_l.min():
            # an outside bar can be both; register the dominant side by
            # comparing protrusions — simpler: register both, order by index
            raw.append((i + strength, Swing(SwingKind.HIGH, i, float(h[i]), i + strength)))
            raw.append((i + strength, Swing(SwingKind.LOW, i, float(lows_a[i]), i + strength)))
        elif h[i] > window_h.max():
            raw.append((i + strength, Swing(SwingKind.HIGH, i, float(h[i]), i + strength)))
        elif lows_a[i] < window_l.min():
            raw.append((i + strength, Swing(SwingKind.LOW, i, float(lows_a[i]), i + strength)))
    raw.sort(key=lambda t: (t[0], t[1].index))
    return [s for _, s in raw]


def classify_swings(swings: list[Swing]) -> list[Swing]:
    """Annotate consecutive same-kind swings with HH/HL/LH/LL labels."""
    out: list[Swing] = []
    last_high: Swing | None = None
    last_low: Swing | None = None
    for s in swings:
        if s.kind is SwingKind.HIGH:
            side = ""
            if last_high is not None:
                side = "+" if s.price > last_high.price else "-"
            annotated = Swing(s.kind, s.index, s.price, s.confirmed_index, side)
            last_high = annotated
        else:
            side = ""
            if last_low is not None:
                side = "+" if s.price > last_low.price else "-"
            annotated = Swing(s.kind, s.index, s.price, s.confirmed_index, side)
            last_low = annotated
        out.append(annotated)
    return out


def detect_events(
    swings: list[Swing],
    closes: np.ndarray | list[float],
) -> list[StructureEvent]:
    """Detect BOS / CHoCH from confirmed swings vs subsequent closes.

    Rules (deterministic, closed-bar):
    - Track the most recent confirmed swing high and swing low.
    - A close ABOVE the last swing high breaks it: BOS when the prevailing
      direction (from the last two labelled swings) is up, CHoCH otherwise.
    - A close BELOW the last swing low mirrors the same logic down.
    - After a break the corresponding swing is consumed (next break needs a
      NEW swing beyond it).
    """
    c = np.asarray(closes, dtype=np.float64)
    labelled = classify_swings(swings)
    events: list[StructureEvent] = []
    # prevailing direction: +1 up, -1 down, 0 unknown
    direction = 0
    active_high: Swing | None = None
    active_low: Swing | None = None

    for i in range(c.shape[0]):
        # activate swings as they become known (confirmed_index <= i)
        for s in labelled:
            if s.confirmed_index == i:
                if s.kind is SwingKind.HIGH:
                    active_high = s
                else:
                    active_low = s

        if active_high is not None and c[i] > active_high.price:
            etype = EventType.BOS if direction >= 0 else EventType.CHOCH
            events.append(StructureEvent(etype, i, active_high.price, active_high, +1))
            direction = +1
            active_high = None
        elif active_low is not None and c[i] < active_low.price:
            etype = EventType.BOS if direction <= 0 else EventType.CHOCH
            events.append(StructureEvent(etype, i, active_low.price, active_low, -1))
            direction = -1
            active_low = None
    return events


def classify_trend(swings: list[Swing]) -> TrendKind:
    """Trend vs range from the LAST two same-kind swing pairs."""
    labelled = classify_swings(swings)
    highs = [s for s in labelled if s.kind is SwingKind.HIGH]
    lows = [s for s in labelled if s.kind is SwingKind.LOW]
    if len(highs) < 2 or len(lows) < 2:
        return TrendKind.RANGE
    up_highs = highs[-1].price > highs[-2].price
    up_lows = lows[-1].price > lows[-2].price
    if up_highs and up_lows:
        return TrendKind.UP
    if not up_highs and not up_lows:
        return TrendKind.DOWN
    return TrendKind.RANGE
