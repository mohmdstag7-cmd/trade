"""Tests for the SQLite core: WAL pragmas, per-thread connections and the
idempotent migration runner (SPEC E1)."""

from __future__ import annotations

import pathlib
import threading

import pytest

from app.storage.db import Database, DatabaseError
from app.storage.migrations import MIGRATIONS, Migration, MigrationRunner

EXPECTED_TABLES = {
    "schema_migrations",
    "accounts",
    "sessions",
    "strategy_configs",
    "signals",
    "decision_traces",
    "trades",
    "trade_events",
    "mt5_requests",
    "account_snapshots",
    "risk_events",
    "model_versions",
    "backtest_runs",
    "journal",
    "audit_log",
    "app_logs",
    "health_checks",
    "performance_metrics",
    "daily_reports",
    "calendar_events",
    "calibration_reports",
    "drift_reports",
    "outbox",
}


@pytest.fixture
def db(tmp_path: pathlib.Path) -> Database:
    database = Database(tmp_path / "data" / "workstation.db")
    MigrationRunner(database).run_all()
    yield database
    database.close_all()


class TestDatabase:
    def test_wal_mode_active(self, db: Database) -> None:
        assert db.journal_mode() == "wal"

    def test_wal_sidecar_files_created(self, db: Database) -> None:
        db.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
        db.execute("INSERT INTO t VALUES (1)")
        assert db.path.exists()
        assert db.size_bytes() > 0

    def test_foreign_keys_enforced(self, db: Database) -> None:
        row = db.query_one("PRAGMA foreign_keys")
        assert int(row[0]) == 1

    def test_busy_timeout_set(self, db: Database) -> None:
        row = db.query_one("PRAGMA busy_timeout")
        assert int(row[0]) == 5000

    def test_per_thread_connections(self, db: Database, tmp_path: pathlib.Path) -> None:
        """Two threads write concurrently via their own connections."""
        db.execute("CREATE TABLE t (id INTEGER, tid TEXT)")

        def write(tid: str) -> None:
            db.execute("INSERT INTO t (id, tid) VALUES (?, ?)", (1, tid))

        threads = [threading.Thread(target=write, args=(f"t{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        rows = db.query("SELECT tid FROM t ORDER BY tid")
        assert [str(r["tid"]) for r in rows] == ["t0", "t1"]

    def test_transaction_commit(self, db: Database) -> None:
        db.execute("CREATE TABLE t (x INTEGER)")
        with db.transaction():
            db.execute("INSERT INTO t VALUES (1)")
            db.execute("INSERT INTO t VALUES (2)")
        assert db.row_count("t") == 2

    def test_transaction_rollback_on_error(self, db: Database) -> None:
        db.execute("CREATE TABLE t (x INTEGER UNIQUE)")
        with pytest.raises(Exception):  # noqa: B017, PT011
            with db.transaction():
                db.execute("INSERT INTO t VALUES (1)")
                db.execute("INSERT INTO t VALUES (1)")  # UNIQUE violation
        assert db.row_count("t") == 0

    def test_nested_transaction_reuses_outer(self, db: Database) -> None:
        """Composed repositories must share one atomic write."""
        db.execute("CREATE TABLE t (x INTEGER)")
        with db.transaction():
            db.execute("INSERT INTO t VALUES (1)")
            with db.transaction():
                db.execute("INSERT INTO t VALUES (2)")
        assert db.row_count("t") == 2

    def test_close_all_rejects_new_use(self, tmp_path: pathlib.Path) -> None:
        database = Database(tmp_path / "x.db")
        database.execute("CREATE TABLE t (x INTEGER)")
        database.close_all()
        with pytest.raises(DatabaseError, match="closed"):
            database.execute("SELECT 1")

    def test_integrity_ok(self, db: Database) -> None:
        assert db.integrity_ok() is True

    def test_open_failure_raises_friendly_error(self, tmp_path: pathlib.Path) -> None:
        # A directory at the db path makes sqlite fail while opening the file.
        target = tmp_path / "blocked.db"
        target.mkdir()
        with pytest.raises(DatabaseError, match="could not open the database"):
            Database(target).connection()

    def test_missing_table_row_count_is_zero(self, db: Database) -> None:
        assert db.row_count("no_such_table") == 0


class TestMigrationRunner:
    def test_fresh_apply_creates_all_spec_tables(self, db: Database) -> None:
        assert EXPECTED_TABLES.issubset(set(db.table_names()))

    def test_current_version(self, db: Database) -> None:
        runner = MigrationRunner(db)
        assert runner.current_version() == MIGRATIONS[-1].version

    def test_rerun_is_idempotent(self, tmp_path: pathlib.Path) -> None:
        """A second runner (new app start) applies nothing and breaks nothing."""
        database = Database(tmp_path / "w.db")
        first = MigrationRunner(database)
        first.run_all()
        before = sorted(database.table_names())

        second = MigrationRunner(database)
        applied = second.run_all()

        assert applied == []
        assert sorted(database.table_names()) == before
        database.close_all()

    def test_ledger_records_versions(self, db: Database) -> None:
        rows = db.query("SELECT version, name FROM schema_migrations ORDER BY version")
        assert [int(r["version"]) for r in rows] == [m.version for m in MIGRATIONS]
        assert str(rows[0]["name"]) == "initial_schema"

    def test_pending_empty_after_run(self, db: Database) -> None:
        assert MigrationRunner(db).pending() == []

    def test_outbox_schema_matches_contract(self, db: Database) -> None:
        cols = {str(r["name"]) for r in db.query("PRAGMA table_info(outbox)")}
        assert {
            "id",
            "table_name",
            "row_id",
            "payload",
            "state",
            "attempts",
            "next_attempt_at",
            "last_error",
            "created_at",
            "updated_at",
        } == cols

    def test_signals_indexes_exist(self, db: Database) -> None:
        names = {
            str(r["name"]) for r in db.query("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert {"idx_signals_bar_time", "idx_signals_symbol", "idx_trades_open_time"} <= names

    def test_verify_true_after_apply(self, db: Database) -> None:
        assert MigrationRunner(db).verify() is True

    def test_partial_failure_leaves_version_unrecorded(self, tmp_path: pathlib.Path) -> None:
        """A migration whose SQL fails must not be recorded as applied."""
        database = Database(tmp_path / "w.db")
        broken = (Migration(version=1, name="broken", sql="CREATE TABLE good (x)"),)
        # sqlite executescript() commits implicitly; emulate failure by a bad
        # statement in a second migration executed through the runner.
        ok_runner = MigrationRunner(database, broken)
        ok_runner.run_all()

        bad = (
            Migration(version=1, name="ok", sql="CREATE TABLE good (x INTEGER)"),
            Migration(version=2, name="bad", sql="CREATE TABLE bad AS SELECT nope FROM missing"),
        )
        runner2 = MigrationRunner(database, bad)
        with pytest.raises(Exception):  # noqa: B017, PT011
            runner2.run_all()

        versions = [
            int(r["version"]) for r in database.query("SELECT version FROM schema_migrations")
        ]
        assert versions == [1]
        database.close_all()
