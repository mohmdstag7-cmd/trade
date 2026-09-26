"""Tests for the economic calendar domain (SPEC C3.9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.calendar.events import EconomicEvent, EventStore, parse_csv
from app.calendar.importer import CalendarImporter, ExporterFilePoller

NOW = datetime(2024, 3, 12, 12, 0, tzinfo=UTC)


def ev(
    minutes_from_now: int,
    currency: str = "USD",
    title: str = "CPI y/y",
    impact: str = "high",
    actual: str = "",
) -> EconomicEvent:
    return EconomicEvent(
        time_utc=NOW + timedelta(minutes=minutes_from_now),
        currency=currency,
        title=title,
        impact=impact,
        actual=actual,
        forecast="3.1%",
    )


SAMPLE_CSV = """time_utc,currency,title,impact,actual,forecast,previous
2024-03-12T13:30:00Z,USD,"CPI y/y",high,3.2%,3.1%,3.1%
2024-03-12T09:00:00Z,EUR,"ZEW survey",medium,,55.0,54.2
2024-03-13T01:30:00Z,JPY,"BoJ rate decision",high,,,
"""


class TestParseCsv:
    def test_sample_rows(self) -> None:
        events, errors = parse_csv(SAMPLE_CSV)
        assert errors == []
        assert len(events) == 3
        assert events[0].currency == "EUR"  # sorted by time
        assert events[1].currency == "USD"
        assert events[1].impact == "high"
        assert events[1].actual == "3.2%"

    def test_header_optional(self) -> None:
        events, errors = parse_csv("2024-03-12T13:30:00Z,USD,CPI,high,3.2,3.1,3.1\n")
        assert errors == []
        assert len(events) == 1

    def test_bad_rows_reported_not_fatal(self) -> None:
        text = (
            "not-a-time,USD,CPI,high\n"
            "2024-03-12T13:30:00Z,USD,CPI,high\n"
            "2024-03-12T14:30:00Z,USD,OnlyThree\n"
        )
        events, errors = parse_csv(text)
        assert len(events) == 1
        assert len(errors) == 2  # bad time + short row both reported

    def test_unknown_impact_defaults_medium(self) -> None:
        events, _ = parse_csv("2024-03-12T13:30:00Z,USD,CPI,extreme\n")
        assert events[0].impact == "medium"

    def test_blank_lines_skipped(self) -> None:
        events, errors = parse_csv("\n\n2024-03-12T13:30:00Z,USD,CPI,high\n\n")
        assert len(events) == 1 and errors == []


class TestEventStore:
    def test_add_and_upcoming(self) -> None:
        store = EventStore()
        store.add(ev(60))
        store.add(ev(300, currency="EUR", impact="medium"))
        store.add(ev(-60))  # past
        upcoming = store.upcoming(NOW, within_minutes=600)
        assert len(upcoming) == 2
        assert upcoming[0].currency == "USD"  # soonest first

    def test_impact_filter(self) -> None:
        store = EventStore()
        store.add(ev(60, impact="low"))
        store.add(ev(120, title="GDP", impact="high"))
        assert len(store.upcoming(NOW, impact_at_least="high")) == 1
        assert len(store.upcoming(NOW, impact_at_least="low")) == 2

    def test_currency_filter(self) -> None:
        store = EventStore()
        store.add(ev(60))
        store.add(ev(90, currency="EUR", title="ZEW"))
        assert len(store.upcoming(NOW, currencies={"USD"})) == 1

    def test_risk_window(self) -> None:
        store = EventStore()
        store.add(ev(20))  # inside the window
        store.add(ev(200, title="Far away"))  # outside
        risky = store.risk_window(NOW, before_minutes=30, after_minutes=15)
        assert len(risky) == 1
        assert risky[0].title == "CPI y/y"

    def test_merge_idempotent(self) -> None:
        store = EventStore()
        batch, _ = parse_csv(SAMPLE_CSV)
        first = store.merge(batch)
        second = store.merge(batch)
        assert first == 3
        assert second == 0
        assert len(store) == 3

    def test_add_replaces_same_key(self) -> None:
        store = EventStore()
        store.add(ev(60, actual=""))
        store.add(ev(60, actual="3.2%"))
        assert len(store) == 1
        assert store.all()[0].actual == "3.2%"

    def test_minutes_until(self) -> None:
        store = EventStore()
        event = ev(90)
        assert store.minutes_until(event, NOW) == 90

    def test_save_and_reload_roundtrip(self, tmp_path: Path) -> None:
        store = EventStore()
        batch, _ = parse_csv(SAMPLE_CSV)
        store.merge(batch)
        path = tmp_path / "calendar.csv"
        store.save(path)
        reloaded = EventStore(path)
        assert len(reloaded) == 3
        assert reloaded.all()[1].forecast == "3.1%"

    def test_replace_from_csv(self) -> None:
        store = EventStore()
        store.merge(parse_csv(SAMPLE_CSV)[0])
        count, errors = store.replace_from_csv(SAMPLE_CSV)
        assert count == 3 and errors == []
        assert len(store) == 3


class TestImporter:
    def test_import_text_merges(self) -> None:
        store = EventStore()
        importer = CalendarImporter(store)
        added, errors = importer.import_text(SAMPLE_CSV)
        assert added == 3 and errors == []
        added2, _ = importer.import_text(SAMPLE_CSV)
        assert added2 == 0

    def test_import_file_utf8_bom(self, tmp_path: Path) -> None:
        path = tmp_path / "exp.csv"
        path.write_bytes(SAMPLE_CSV.encode("utf-8-sig"))
        importer = CalendarImporter(EventStore())
        added, errors = importer.import_file(path)
        assert added == 3 and errors == []

    def test_import_missing_file(self, tmp_path: Path) -> None:
        importer = CalendarImporter(EventStore())
        added, errors = importer.import_file(tmp_path / "nope.csv")
        assert added == 0 and len(errors) == 1


class TestExporterPoller:
    def test_poll_reads_new_file(self, tmp_path: Path) -> None:
        import os

        store = EventStore()
        path = tmp_path / "exporter.csv"
        poller = ExporterFilePoller(store, path, interval_s=0.0)
        added, errors = poller.poll(now_monotonic=0.0)
        assert added == 0 and errors == []  # no file yet — silent
        path.write_text(SAMPLE_CSV, encoding="utf-8")
        os.utime(path, (2000.0, 2000.0))
        added, errors = poller.poll(now_monotonic=1.0)
        assert added == 3 and errors == []
        # unchanged mtime → no re-import
        added, _ = poller.poll(now_monotonic=2.0)
        assert added == 0
        # changed mtime → re-import, but merge is idempotent
        os.utime(path, (3000.0, 3000.0))
        added, _ = poller.poll(now_monotonic=3.0)
        assert added == 0

    def test_interval_throttle(self, tmp_path: Path) -> None:
        store = EventStore()
        path = tmp_path / "exporter.csv"
        path.write_text(SAMPLE_CSV, encoding="utf-8")
        poller = ExporterFilePoller(store, path, interval_s=300.0)
        added, _ = poller.poll(now_monotonic=0.0)
        assert added == 3
        added, _ = poller.poll(now_monotonic=1.0)  # inside the interval
        assert added == 0
        added, _ = poller.poll(now_monotonic=1000.0)  # interval elapsed
        assert added == 0  # same mtime
