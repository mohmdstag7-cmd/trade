"""Retention cleanup — old low-level rows are aggregated away (SPEC E1).

High-volume, low-value tables grow forever without this; trades, signals,
audit_log and journal are NEVER touched (spec: "never trades/signals").
Runs at startup and then on a slow timer from :mod:`app.storage.service`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from app.observability.logger import get_logger
from app.storage.db import Database

log = get_logger("sync")

#: table → retention days (low-level rows only — business data is forever).
DEFAULT_RETENTION_DAYS: dict[str, int] = {
    "app_logs": 30,
    "mt5_requests": 14,
    "account_snapshots": 30,
    "performance_metrics": 14,
    "health_checks": 30,
}

PROTECTED_TABLES: frozenset[str] = frozenset(
    {"trades", "signals", "audit_log", "journal", "decision_traces"}
)


@dataclass(slots=True)
class CleanupResult:
    deleted: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.deleted.values())


class RetentionService:
    """Deletes rows older than their retention window, table by table."""

    def __init__(
        self,
        db: Database,
        retention_days: dict[str, int] | None = None,
    ) -> None:
        self._db = db
        self._retention = retention_days or DEFAULT_RETENTION_DAYS

    def run_once(self) -> CleanupResult:
        """Apply every table's retention window; return deleted counts."""
        result = CleanupResult()
        for table, days in self._retention.items():
            if table in PROTECTED_TABLES:
                log.warning("storage: refusing to clean protected table {}", table)
                continue
            deleted = self._delete_older_than(table, days)
            if deleted:
                result.deleted[table] = deleted
        if result.total:
            log.info("storage: cleanup removed {} low-level row(s)", result.total)
        return result

    def _delete_older_than(self, table: str, days: int) -> int:
        names = self._db.table_names()
        if table not in names:
            return 0
        cursor = self._db.execute(
            f'DELETE FROM "{table}" '
            "WHERE created_at < strftime('%Y-%m-%dT%H:%M:%S.%fZ','now', ?)",
            (f"-{int(days)} days",),
        )
        return cursor.rowcount


class CleanupScheduler:
    """Runs :class:`RetentionService` on a slow background timer."""

    def __init__(self, service: RetentionService, interval_s: float = 6 * 3600.0) -> None:
        self._service = service
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cleanup", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    def _loop(self) -> None:
        # First pass after a short grace period (startup burst finished),
        # then once per interval.
        if not self._stop.wait(30.0):
            try:
                self._service.run_once()
            except Exception:
                log.opt(exception=True).error("storage: scheduled cleanup failed")
        while not self._stop.wait(self._interval):
            try:
                self._service.run_once()
            except Exception:
                log.opt(exception=True).error("storage: scheduled cleanup failed")
