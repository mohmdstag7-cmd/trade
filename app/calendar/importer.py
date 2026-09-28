"""Calendar CSV importer + polling reader for the MQL5 exporter file."""

from __future__ import annotations

from pathlib import Path

from app.calendar.events import EventStore
from app.observability.logger import get_logger

log = get_logger("sync")


class CalendarImporter:
    """Loads calendar CSVs into an :class:`EventStore` (merge, idempotent)."""

    def __init__(self, store: EventStore) -> None:
        self._store = store

    @property
    def store(self) -> EventStore:
        return self._store

    def import_text(self, text: str) -> tuple[int, list[str]]:
        """Merge events from CSV text; returns (added, errors)."""
        from app.calendar.events import parse_csv

        events, errors = parse_csv(text)
        return self._store.merge(events), errors

    def import_file(self, path: Path) -> tuple[int, list[str]]:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            return 0, [f"cannot read {path}: {exc}"]
        return self.import_text(text)


class ExporterFilePoller:
    """Re-reads the CalendarExporter.mq5 output every ``interval_s``.

    The EA overwrites ONE csv file (Common\\Files by default). The poller
    remembers the file's mtime and re-imports only when it changed — cheap
    enough to call from a 5-second UI timer; heavy work is a single read.
    """

    def __init__(self, store: EventStore, csv_path: Path, *, interval_s: float = 300.0) -> None:
        self._importer = CalendarImporter(store)
        self._csv_path = csv_path
        self.interval_s = interval_s
        self._last_mtime: float | None = None
        self._last_poll: float | None = None

    def poll(self, now_monotonic: float) -> tuple[int, list[str]]:
        """Check the exporter file; returns (added, errors) when re-read."""
        if self._last_poll is not None and (now_monotonic - self._last_poll < self.interval_s):
            return 0, []
        self._last_poll = now_monotonic
        try:
            mtime = self._csv_path.stat().st_mtime
        except OSError:
            return 0, []  # no exporter file yet — normal until EA is attached
        if self._last_mtime is not None and mtime == self._last_mtime:
            return 0, []
        added, errors = self._importer.import_file(self._csv_path)
        # Record the mtime only AFTER a successful read: a torn read (EA
        # mid-write) must be retried on the next poll, not skipped.
        self._last_mtime = mtime
        if added:
            log.info("calendar: {} event(s) imported from exporter", added)
        for err in errors:
            log.warning("calendar: exporter row problem: {}", err)
        # Persist the merged events — previously save() was never called
        # and every imported event evaporated on restart.
        if added and hasattr(self._importer, "store"):
            store = self._importer.store
            if store is not None and hasattr(store, "save"):
                try:
                    store.save()
                except OSError:
                    log.opt(exception=True).warning("calendar: persisting store failed")
        return added, errors
