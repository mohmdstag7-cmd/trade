"""Repository tests: typed CRUD, state transitions and the atomic outbox."""

from __future__ import annotations

import json
import pathlib

import pytest

from app.storage.db import Database
from app.storage.migrations import MigrationRunner
from app.storage.repositories import (
    MIRRORED_TABLES,
    AccountSnapshotRepository,
    AppLogRepository,
    AuditLogRepository,
    BaseRepository,
    DecisionTraceRepository,
    HealthCheckRepository,
    Mt5RequestRepository,
    OutboxRepository,
    RiskEventRepository,
    SessionRepository,
    SignalRepository,
    TradeRepository,
    deterministic_id,
    new_id,
    now_iso,
    repository_for,
)


@pytest.fixture
def db(tmp_path: pathlib.Path) -> Database:
    database = Database(tmp_path / "w.db")
    MigrationRunner(database).run_all()
    yield database
    database.close_all()


@pytest.fixture
def outbox(db: Database) -> OutboxRepository:
    return OutboxRepository(db)


class TestHelpers:
    def test_new_id_is_uuid(self) -> None:
        value = new_id()
        assert len(value) == 36 and value != new_id()

    def test_now_iso_is_utc_iso8601(self) -> None:
        stamp = now_iso()
        assert stamp.endswith("Z") and "T" in stamp

    def test_deterministic_id_stable(self) -> None:
        a = deterministic_id("trade", "123")
        b = deterministic_id("trade", "123")
        assert a == b and a != deterministic_id("trade", "124")

    def test_mirrored_tables_match_spec(self) -> None:
        assert "trades" in MIRRORED_TABLES
        assert "signals" in MIRRORED_TABLES
        assert "app_logs" in MIRRORED_TABLES
        assert "performance_metrics" not in MIRRORED_TABLES  # high-frequency stays local


class TestBaseRepository:
    def test_insert_adds_id_and_created_at(self, db: Database) -> None:
        repo = BaseRepository(db, "calendar_events")
        row_id = repo.insert({"event_time": "2026-01-01T00:00Z", "title": "NFP"})
        row = repo.get(row_id)
        assert row is not None
        assert row["id"] == row_id
        assert row["created_at"]

    def test_insert_enqueues_outbox_atomically(
        self, db: Database, outbox: OutboxRepository
    ) -> None:
        repo = BaseRepository(db, "trades")
        row_id = repo.insert({"symbol": "EURUSD", "direction": "buy"})
        entries = outbox.claim_batch(10)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["table_name"] == "trades"
        assert entry["row_id"] == row_id
        payload = json.loads(str(entry["payload"]))
        assert payload["symbol"] == "EURUSD"

    def test_insert_mirror_false_skips_outbox(self, db: Database, outbox: OutboxRepository) -> None:
        repo = BaseRepository(db, "performance_metrics", mirrored=False)
        repo.insert({"name": "cpu", "value": 12.5})
        assert outbox.claim_batch(10) == []

    def test_update_patches_and_reenqueues(self, db: Database, outbox: OutboxRepository) -> None:
        repo = BaseRepository(db, "journal")
        row_id = repo.insert({"narrative": "v1"})
        outbox.claim_batch(10)  # drain

        assert repo.update(row_id, {"notes": "reviewed"}) is True
        assert repo.get(row_id)["notes"] == "reviewed"
        entries = outbox.claim_batch(10)
        assert len(entries) == 1
        payload = json.loads(str(entries[0]["payload"]))
        assert payload["notes"] == "reviewed"

    def test_update_missing_row_returns_false(self, db: Database) -> None:
        repo = BaseRepository(db, "journal")
        assert repo.update(str(new_id()), {"notes": "x"}) is False

    def test_recent_orders_desc(self, db: Database) -> None:
        repo = BaseRepository(db, "health_checks")
        repo.insert({"component": "a", "status": "ok"})
        repo.insert({"component": "b", "status": "ok"})
        names = [r["component"] for r in repo.recent(10)]
        assert names == ["b", "a"]

    def test_generic_repository_for(self, db: Database) -> None:
        repo = repository_for(db, "daily_reports")
        assert repo.table == "daily_reports"
        repo.insert({"day": "2026-01-01", "report_json": "{}"})
        assert repo.count() == 1


class TestSignalRepository:
    def test_insert_signal_defaults_state(self, db: Database) -> None:
        repo = SignalRepository(db)
        row_id = repo.insert_signal({"strategy": "trend_pullback", "symbol": "EURUSD"})
        row = repo.get(row_id)
        assert row["state"] == "new"

    def test_insert_signal_requires_strategy(self, db: Database) -> None:
        repo = SignalRepository(db)
        with pytest.raises(ValueError, match="strategy"):
            repo.insert_signal({"symbol": "EURUSD"})

    def test_update_state_transition(self, db: Database) -> None:
        repo = SignalRepository(db)
        row_id = repo.insert_signal({"strategy": "trend_pullback"})
        assert repo.update_state(row_id, "risk_rejected", reject_reason="daily_loss")
        row = repo.get(row_id)
        assert row["state"] == "risk_rejected"
        assert row["reject_reason"] == "daily_loss"

    def test_update_state_missing_signal(self, db: Database) -> None:
        repo = SignalRepository(db)
        assert repo.update_state(str(new_id()), "approved") is False


class TestTradeRepository:
    def test_close_trade_updates_fields(self, db: Database) -> None:
        repo = TradeRepository(db)
        row_id = repo.insert({"symbol": "XAUUSD", "direction": "buy", "open_price": 2650.0})
        closed = repo.close_trade(
            row_id,
            {
                "close_time": now_iso(),
                "close_price": 2655.0,
                "profit": 50.0,
                "commission": -2.0,
                "swap": 0.0,
                "net_profit": 48.0,
                "r_multiple": 1.6,
                "outcome": "win",
                "exit_reason": "tp",
                "duration_sec": 3600.0,
            },
        )
        assert closed
        row = repo.get(row_id)
        assert row["outcome"] == "win"
        assert row["net_profit"] == 48.0
        assert row["open_price"] == 2650.0  # untouched

    def test_open_vs_closed_lists(self, db: Database) -> None:
        repo = TradeRepository(db)
        open_id = repo.insert({"symbol": "EURUSD"})
        closed_id = repo.insert({"symbol": "GBPUSD"})
        repo.close_trade(closed_id, {"close_time": now_iso(), "outcome": "win"})
        assert [r["id"] for r in repo.open_trades()] == [open_id]
        assert [r["id"] for r in repo.closed_trades()] == [closed_id]

    def test_import_row_idempotent_by_deterministic_id(self, db: Database) -> None:
        """Re-importing the same deal ticket never duplicates (G3-4 ✓)."""
        repo = TradeRepository(db)
        row = {"ticket": 987654, "position_id": 111, "symbol": "EURUSD", "profit": 3.2}
        assert repo.import_row(dict(row)) is True
        assert repo.import_row(dict(row)) is False
        assert repo.count() == 1
        row_out = repo.recent(1)[0]
        assert row_out["id"] == deterministic_id("trade", "987654", "111")
        assert row_out["source"] == "import"

    def test_import_row_enqueues_outbox(self, db: Database, outbox: OutboxRepository) -> None:
        repo = TradeRepository(db)
        repo.import_row({"ticket": 1, "symbol": "EURUSD"})
        entries = outbox.claim_batch(5)
        assert len(entries) == 1 and entries[0]["table_name"] == "trades"


class TestOutboxRepository:
    def test_claim_batch_marks_in_flight(self, db: Database, outbox: OutboxRepository) -> None:
        for i in range(3):
            outbox.enqueue("audit_log", f"id{i}", {"i": i})
        claimed = outbox.claim_batch(2)
        assert len(claimed) == 2
        counts = outbox.counts()
        assert counts == {"in_flight": 2, "pending": 1}

    def test_claim_respects_next_attempt_at(self, db: Database, outbox: OutboxRepository) -> None:
        outbox.enqueue("audit_log", "x", {})
        entry_id = outbox.claim_batch(5)[0]["id"]
        outbox.mark_retry(entry_id, 1, "2099-01-01T00:00:00.000Z", "server down")
        assert outbox.claim_batch(5) == []  # not due yet
        outbox.mark_retry(entry_id, 2, "2000-01-01T00:00:00.000Z", "server down")
        assert len(outbox.claim_batch(5)) == 1  # due

    def test_mark_synced_and_last_synced_at(self, db: Database, outbox: OutboxRepository) -> None:
        outbox.enqueue("audit_log", "x", {})
        entry = outbox.claim_batch(5)[0]
        outbox.mark_synced([str(entry["id"])])
        counts = outbox.counts()
        assert counts == {"synced": 1}
        assert outbox.last_synced_at() is not None

    def test_mark_dead_records_error(self, db: Database, outbox: OutboxRepository) -> None:
        outbox.enqueue("audit_log", "x", {})
        entry = outbox.claim_batch(5)[0]
        outbox.mark_dead(str(entry["id"]), "schema mismatch")
        row = outbox.counts()
        assert row == {"dead": 1}
        recent = db.query("SELECT last_error FROM outbox")
        assert recent[0]["last_error"] == "schema mismatch"

    def test_payload_roundtrip_preserves_unicode(
        self, db: Database, outbox: OutboxRepository
    ) -> None:
        outbox.enqueue("journal", "x", {"narrative": "معامله خوب بود"})
        entry = outbox.claim_batch(5)[0]
        assert json.loads(str(entry["payload"]))["narrative"] == "معامله خوب بود"


class TestSmallRepositories:
    def test_decision_trace(self, db: Database) -> None:
        repo = DecisionTraceRepository(db)
        row_id = repo.insert_trace("sig1", [{"name": "prob", "pass": True}], "APPROVED")
        row = repo.get(row_id)
        assert json.loads(str(row["steps_json"]))[0]["name"] == "prob"
        assert row["final_decision"] == "APPROVED"

    def test_audit_log_record(self, db: Database) -> None:
        repo = AuditLogRepository(db)
        repo.record("settings.mt5.saved", before={"server": "A"}, after={"server": "B"})
        row = repo.recent(1)[0]
        assert row["action"] == "settings.mt5.saved"
        assert json.loads(str(row["before_json"]))["server"] == "A"

    def test_health_check(self, db: Database) -> None:
        repo = HealthCheckRepository(db)
        repo.record("mt5", "fail", "terminal not reachable")
        row = repo.recent(1)[0]
        assert row["status"] == "fail" and row["component"] == "mt5"

    def test_mt5_request(self, db: Database) -> None:
        repo = Mt5RequestRepository(db)
        repo.record("order_send", retcode=10009, retcode_text="done", latency_ms=12.5)
        row = repo.recent(1)[0]
        assert row["action"] == "order_send"
        assert row["retcode"] == 10009
        assert row["latency_ms"] == 12.5

    def test_account_snapshot(self, db: Database) -> None:
        repo = AccountSnapshotRepository(db)
        repo.record({"balance": 10_000.0, "equity": 10_050.0, "daily_pnl": 50.0})
        assert repo.recent(1)[0]["equity"] == 10_050.0

    def test_risk_event(self, db: Database) -> None:
        repo = RiskEventRepository(db)
        repo.record("daily_limit", {"pnl": -210.0, "limit": -200.0})
        row = repo.recent(1)[0]
        assert row["type"] == "daily_limit"
        assert json.loads(str(row["details_json"]))["limit"] == -200.0

    def test_session_start_end(self, db: Database) -> None:
        repo = SessionRepository(db)
        session_id = repo.start("0.4.0", mode="paper", profile="normal")
        assert repo.get(session_id)["ended_at"] is None
        repo.end(session_id)
        assert repo.get(session_id)["ended_at"] is not None

    def test_app_logs_bulk(self, db: Database) -> None:
        repo = AppLogRepository(db)
        n = repo.bulk_insert(
            [
                {"level": "WARNING", "category": "mt5", "message": "reconnecting"},
                {"level": "ERROR", "category": "sync", "message": "mirror down"},
            ]
        )
        assert n == 2 and repo.count() == 2
