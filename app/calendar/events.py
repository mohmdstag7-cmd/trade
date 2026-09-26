"""Economic calendar domain (SPEC C3.9).

The MetaTrader5 Python API cannot read the MT5 economic calendar, so the
app keeps its own event store:

- manual entries and CSV imports (canonical CSV format, documented here),
- a CSV written by the bundled ``mql5/CalendarExporter.mq5`` expert
  advisor, which the app re-reads every few minutes.

Everything is stored in UTC. The store is intentionally simple (a sorted
in-memory list persisted to one CSV) — it mirrors a few hundred events per
year, not a database workload.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability.logger import get_logger

log = get_logger("sync")

CSV_HEADER = ("time_utc", "currency", "title", "impact", "actual", "forecast", "previous")
IMPACT_LEVELS = ("low", "medium", "high")

#: Canonical CSV format documentation block (also used by the MQL5 EA).
FORMAT_DOC = """\
Calendar CSV format (UTF-8, one event per line):
  time_utc,currency,title,impact,actual,forecast,previous
  2024-03-12T13:30:00Z,USD,"CPI y/y",high,3.2%,3.1%,3.1%
- time_utc: ISO-8601 with Z suffix (UTC).
- impact: low | medium | high.
- actual/forecast/previous: free text; empty when not yet released.
"""


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    time_utc: datetime
    currency: str
    title: str
    impact: str  # low / medium / high
    actual: str = ""
    forecast: str = ""
    previous: str = ""

    def sort_key(self) -> datetime:
        return self.time_utc


def _parse_time(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text[:-1]).replace(tzinfo=UTC)
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_csv(text: str) -> tuple[list[EconomicEvent], list[str]]:
    """Parse calendar CSV content (canonical format, header optional).

    Returns (events, errors). Rows with an unparsable time are skipped and
    reported — one bad row never rejects a whole file.
    """
    events: list[EconomicEvent] = []
    errors: list[str] = []
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    for line_no, row in enumerate(rows, start=1):
        first = row[0].strip().lower()
        if first == "time_utc" and line_no == 1:
            continue  # header
        if len(row) < 4:
            errors.append(f"line {line_no}: expected >= 4 columns, got {len(row)}")
            continue
        when = _parse_time(row[0])
        if when is None:
            errors.append(f"line {line_no}: bad time {row[0]!r}")
            continue
        impact = row[3].strip().lower()
        if impact not in IMPACT_LEVELS:
            impact = "medium"  # conservative default
        extra = [*row[4:7], "", "", ""][:3]
        events.append(
            EconomicEvent(
                time_utc=when,
                currency=row[1].strip().upper()[:6],
                title=row[2].strip(),
                impact=impact,
                actual=extra[0].strip(),
                forecast=extra[1].strip(),
                previous=extra[2].strip(),
            )
        )
    events.sort(key=lambda e: e.sort_key())
    return events, errors


class EventStore:
    """In-memory event list persisted to a CSV file."""

    def __init__(self, path: Path | None = None) -> None:
        self._events: list[EconomicEvent] = []
        self._path = path
        if path is not None and path.exists():
            self._events, errors = parse_csv(path.read_text(encoding="utf-8"))
            for err in errors:
                log.warning("calendar: stored CSV: {}", err)

    # -- mutation -------------------------------------------------------------
    def add(self, event: EconomicEvent) -> None:
        """Insert or replace (same time+currency+title = same event)."""
        self._events = [
            e
            for e in self._events
            if (e.time_utc, e.currency, e.title) != (event.time_utc, event.currency, event.title)
        ]
        self._events.append(event)
        self._events.sort(key=lambda e: e.sort_key())

    def merge(self, events: list[EconomicEvent]) -> int:
        """Merge a batch (idempotent); returns the number of NEW events."""
        before = len(self._events)
        for event in events:
            self.add(event)
        return len(self._events) - before

    def save(self, path: Path | None = None) -> None:
        target = path or self._path
        if target is None:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(CSV_HEADER)
        for e in self._events:
            writer.writerow(
                [
                    e.time_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    e.currency,
                    e.title,
                    e.impact,
                    e.actual,
                    e.forecast,
                    e.previous,
                ]
            )
        target.write_text(buffer.getvalue(), encoding="utf-8")

    def replace_from_csv(self, text: str) -> tuple[int, list[str]]:
        """Replace the whole store from exporter CSV content."""
        events, errors = parse_csv(text)
        self._events = events
        return len(events), errors

    # -- queries ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._events)

    def all(self) -> list[EconomicEvent]:
        return list(self._events)

    def upcoming(
        self,
        now_utc: datetime,
        *,
        within_minutes: int = 24 * 60,
        currencies: set[str] | None = None,
        impact_at_least: str = "medium",
    ) -> list[EconomicEvent]:
        """Future events (soonest first) matching currency/impact filters."""
        min_idx = IMPACT_LEVELS.index(impact_at_least)
        horizon = now_utc + timedelta(minutes=within_minutes)
        out = []
        for e in self._events:
            if e.time_utc < now_utc or e.time_utc > horizon:
                continue
            if IMPACT_LEVELS.index(e.impact) < min_idx:
                continue
            if currencies and e.currency not in currencies:
                continue
            out.append(e)
        return out

    def risk_window(
        self,
        now_utc: datetime,
        *,
        before_minutes: int = 30,
        after_minutes: int = 15,
        currencies: set[str] | None = None,
        impact_at_least: str = "high",
    ) -> list[EconomicEvent]:
        """High-impact events inside the no-trade window around now."""
        lo = now_utc - timedelta(minutes=after_minutes)
        hi = now_utc + timedelta(minutes=before_minutes)
        min_idx = IMPACT_LEVELS.index(impact_at_least)
        out = []
        for e in self._events:
            if not (lo <= e.time_utc <= hi):
                continue
            if IMPACT_LEVELS.index(e.impact) < min_idx:
                continue
            if currencies and e.currency not in currencies:
                continue
            out.append(e)
        return out

    def minutes_until(self, event: EconomicEvent, now_utc: datetime) -> int:
        return int((event.time_utc - now_utc).total_seconds() // 60)
