"""Tests for audit, backup, retention cleanup and the storage log sink."""

from __future__ import annotations

import datetime as dt
import pathlib

import loguru
import pytest

from app.storage.audit import AuditService
from app.storage.backup import BackupService
from app.storage.cleanup import CleanupScheduler, RetentionService
from app.storage.db import Database
from app.storage.log_sink import StorageLogSink
from app.storage.migrations import MigrationRunner
from app.storage.repositories import AuditLogRepository, OutboxRepository


@pytest.fixture
def db(tmp_path: pathlib.Path) -> Database:
    database = Database(tmp_path / "w.db")
    MigrationRunner(database).run_all()
    yield database
    database.close_all()


# ---------------------------------------------------------------------------
# AuditService
# ---------------------------------------------------------------------------
class TestAuditService:
    def test_setting_change_is_masked(self, db: Database) -> None:
        """A password inside the payload never reaches the audit row."""
        service = AuditService(db)
        service.record(
            "settings.cloud.changed",
            before={"url": "https://x.supabase.co"},
            after={"url": "https://x.supabase.co", "password": "hunter2"},
        )
        row = AuditLogRepository(db).recent(1)[0]
        after = str(row["after_json"])
        assert "hunter2" not in after
        assert "[REDACTED]" in after

    def test_typed_actions(self, db: Database) -> None:
        service = AuditService(db)
        service.app_started("0.4.0")
        service.kill_switch({"reason": "daily limit"})
        repo = AuditLogRepository(db)
        actions = [str(r["action"]) for r in repo.recent(10)]
        assert actions == ["risk.kill_switch", "app.started"]

    def test_sources(self, db: Database) -> None:
        service = AuditService(db)
        service.system("app.started")
        row = AuditLogRepository(db).recent(1)[0]
        assert row["source"] == "system"


# ---------------------------------------------------------------------------
# BackupService
# ---------------------------------------------------------------------------
class TestBackupService:
    def test_daily_backup_created(self, db: Database, tmp_path: pathlib.Path) -> None:
        service = BackupService(db, tmp_path / "backups", keep=7)
        target = service.backup_if_due(today="20260926")
        assert target is not None and target.exists()

    def test_second_call_same_day_is_noop(self, db: Database, tmp_path: pathlib.Path) -> None:
        service = BackupService(db, tmp_path / "backups", keep=7)
        assert service.backup_if_due(today="20260926") is not None
        assert service.backup_if_due(today="20260926") is None

    def test_rotation_keeps_only_n(self, db: Database, tmp_path: pathlib.Path) -> None:
        service = BackupService(db, tmp_path / "backups", keep=3)
        for day in ("20260922", "20260923", "20260924", "20260925", "20260926"):
            service.backup_if_due(today=day)
        names = [p.name for p in service.snapshots()]
        assert len(names) == 3
        assert "workstation_20260922.db" not in " ".join(names)
        assert "workstation_20260926.db" in " ".join(names)

    def test_backup_snapshot_is_valid_db(self, db: Database, tmp_path: pathlib.Path) -> None:
        from app.storage.repositories import TradeRepository

        TradeRepository(db).insert({"symbol": "EURUSD"})
        service = BackupService(db, tmp_path / "backups", keep=1)
        target = service.backup_if_due(today="20260926")
        assert target is not None
        check = Database(target)
        MigrationRunner(check)  # tables already exist in the snapshot
        assert check.row_count("trades") == 1
        check.close_all()


# ---------------------------------------------------------------------------
# RetentionService
# ---------------------------------------------------------------------------
def _insert_old(
    db: Database, table: str, days_ago: int, extra: dict[str, str] | None = None
) -> None:
    stamp = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    cols = {"id": f"old-{table}-{days_ago}", "created_at": stamp, **(extra or {})}
    names = ", ".join(f'"{c}"' for c in cols)
    marks = ", ".join("?" for _ in cols)
    db.execute(
        f'INSERT INTO "{table}" ({names}) VALUES ({marks})',
        list(cols.values()),
    )


class TestRetentionService:
    def test_old_rows_deleted_new_kept(self, db: Database) -> None:
        _insert_old(
            db, "app_logs", 40, {"level": "ERROR", "message": "old"}
        )  # beyond 30-day retention
        _insert_old(db, "app_logs", 1, {"level": "ERROR", "message": "new"})  # recent
        result = RetentionService(db).run_once()
        assert result.deleted.get("app_logs") == 1
        assert db.row_count("app_logs") == 1

    def test_protected_tables_never_touched(self, db: Database) -> None:
        _insert_old(db, "trades", 3_650)
        _insert_old(db, "signals", 3_650)
        _insert_old(db, "audit_log", 3_650, {"action": "app.started"})
        RetentionService(db).run_once()
        assert db.row_count("trades") == 1
        assert db.row_count("signals") == 1
        assert db.row_count("audit_log") == 1

    def test_missing_table_is_skipped(self, db: Database) -> None:
        service = RetentionService(db, {"no_such_table": 7})
        assert service.run_once().total == 0

    def test_scheduler_runs_and_stops(self, db: Database) -> None:
        _insert_old(db, "mt5_requests", 30, {"action": "ping"})
        service = RetentionService(db)
        scheduler = CleanupScheduler(service, interval_s=0.05)
        scheduler.start()
        import time

        deadline = time.monotonic() + 35  # first pass waits 30 s by design
        while time.monotonic() < deadline and db.row_count("mt5_requests") == 1:
            time.sleep(0.2)
        scheduler.stop()
        assert db.row_count("mt5_requests") == 0


# ---------------------------------------------------------------------------
# StorageLogSink
# ---------------------------------------------------------------------------
@pytest.fixture
def wired_sink(db: Database):
    """A sink wired into loguru; unhooked after the test."""
    sink = StorageLogSink(db)
    handler_id = loguru.logger.add(sink, level="TRACE")
    yield sink
    loguru.logger.remove(handler_id)


class TestStorageLogSink:
    def test_warning_plus_rows_are_stored(self, db: Database, wired_sink: StorageLogSink) -> None:
        logger = loguru.logger.bind(category="test")
        logger.warning("mt5 reconnecting")
        logger.error("mirror failed")
        logger.info("should be ignored")
        logger.debug("also ignored")
        assert wired_sink.flush() == 2
        rows = db.query("SELECT level, message FROM app_logs ORDER BY level")
        assert len(rows) == 2
        assert {str(r["level"]) for r in rows} == {"WARNING", "ERROR"}

    def test_rows_are_queued_for_mirror(self, db: Database, wired_sink: StorageLogSink) -> None:
        loguru.logger.bind(category="sync").warning("cloud hiccup")
        wired_sink.flush()
        assert OutboxRepository(db).counts().get("pending") == 1

    def test_message_truncated(self, db: Database, wired_sink: StorageLogSink) -> None:
        loguru.logger.bind(category="test").warning("x" * 5000)
        wired_sink.flush()
        row = db.query_one("SELECT message FROM app_logs")
        assert len(str(row["message"])) == 2000

    def test_exception_recorded(self, db: Database, wired_sink: StorageLogSink) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            loguru.logger.bind(category="test").exception("handler failed")
        wired_sink.flush()
        row = db.query_one("SELECT exception_type, stack_trace FROM app_logs")
        assert str(row["exception_type"]) == "ValueError"
        assert "boom" in str(row["stack_trace"])

    def test_background_flush_loop(self, db: Database) -> None:
        sink = StorageLogSink(db, flush_interval_s=0.05)
        handler_id = loguru.logger.add(sink, level="TRACE")
        sink.start()
        loguru.logger.bind(category="test").error("async row")
        import time

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and db.row_count("app_logs") == 0:
            time.sleep(0.05)
        sink.stop()
        loguru.logger.remove(handler_id)
        assert db.row_count("app_logs") == 1
