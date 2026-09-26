"""Tests for vault, mirror and the outbox worker (all offline/faked)."""

from __future__ import annotations

import pathlib
import time
from typing import Any

import pytest

from app.storage.db import Database
from app.storage.migrations import MigrationRunner
from app.storage.mirror import MirrorError, NullMirror, SupabaseMirror
from app.storage.outbox import OutboxConfig, OutboxWorker, SyncStatus
from app.storage.repositories import OutboxRepository
from app.storage.vault import KeyringVault, VaultError


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeKeyring:
    """In-memory keyring with the fail-backend escape hatch."""

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.fail = fail

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.store[f"{service}/{username}"] = password

    def get_password(self, service: str, username: str) -> str | None:
        if self.fail:
            raise RuntimeError("boom")
        return self.store.get(f"{service}/{username}")

    def delete_password(self, service: str, username: str) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.store.pop(f"{service}/{username}", None)

    def get_keyring(self) -> object:  # non-fail backend marker
        return self


class FailKeyring:
    """Simulates keyring.fail.Keyring (no OS vault)."""

    def get_keyring(self):
        class _Fail:
            __module__ = "keyring.fail.Keyring"

        return _Fail()

    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("no backend")

    def get_password(self, service: str, username: str) -> str | None:
        raise RuntimeError("no backend")

    def delete_password(self, service: str, username: str) -> None:
        raise RuntimeError("no backend")


class FakeClient:
    """Records upserts; scripted to raise when ``error`` is set."""

    def __init__(self) -> None:
        self.upserts: list[tuple[str, list[dict[str, Any]]]] = []
        self.error: Exception | None = None

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def _record(self, table: str, rows: list[dict[str, Any]]) -> None:
        if self.error is not None:
            raise self.error
        self.upserts.append((table, rows))


class FakeQuery:
    def __init__(self, client: FakeClient, table: str) -> None:
        self._client = client
        self._table = table

    def upsert(self, rows: list[dict[str, Any]], on_conflict: str) -> FakeQuery:
        assert on_conflict == "id"
        self._client._record(self._table, rows)
        return self

    def select(self, _columns: str) -> FakeQuery:
        return self

    def limit(self, _n: int) -> FakeQuery:
        return self

    def execute(self) -> None:
        if self._client.error is not None:
            raise self._client.error


class FlakySink:
    """Mirror sink with a scripted failure sequence."""

    def __init__(self, errors: list[MirrorError | None]) -> None:
        self.errors = list(errors)
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    def upsert(self, table: str, rows: list[dict[str, Any]]) -> None:
        self.calls.append((table, rows))
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def db(tmp_path: pathlib.Path) -> Database:
    database = Database(tmp_path / "w.db")
    MigrationRunner(database).run_all()
    yield database
    database.close_all()


@pytest.fixture
def repo(db: Database) -> OutboxRepository:
    return OutboxRepository(db)


# ---------------------------------------------------------------------------
# KeyringVault
# ---------------------------------------------------------------------------
class TestKeyringVault:
    def test_set_get_delete_roundtrip(self) -> None:
        vault = KeyringVault(FakeKeyring())
        vault.set("supabase.service_key", "s3cret")
        assert vault.get("supabase.service_key") == "s3cret"
        assert vault.has("supabase.service_key") is True
        vault.delete("supabase.service_key")
        assert vault.has("supabase.service_key") is False

    def test_missing_entry_is_none(self) -> None:
        vault = KeyringVault(FakeKeyring())
        assert vault.get("absent") is None
        assert vault.has("absent") is False

    def test_empty_secret_rejected(self) -> None:
        vault = KeyringVault(FakeKeyring())
        with pytest.raises(VaultError, match="empty secret"):
            vault.set("x", "")

    def test_fail_backend_friendly_error(self) -> None:
        vault = KeyringVault(FailKeyring())
        with pytest.raises(VaultError, match="No OS credential vault"):
            vault.set("x", "y")

    def test_broken_backend_wrapped(self) -> None:
        vault = KeyringVault(FakeKeyring(fail=True))
        with pytest.raises(VaultError, match="could not save"):
            vault.set("x", "y")
        with pytest.raises(VaultError, match="could not read"):
            vault.get("x")


# ---------------------------------------------------------------------------
# SupabaseMirror
# ---------------------------------------------------------------------------
class TestSupabaseMirror:
    def test_upsert_batches_by_id_conflict(self) -> None:
        client = FakeClient()
        mirror = SupabaseMirror("https://x.supabase.co", "key", client_factory=lambda u, k: client)
        mirror.upsert("trades", [{"id": "1", "symbol": "EURUSD"}])
        assert client.upserts == [("trades", [{"id": "1", "symbol": "EURUSD"}])]

    def test_upsert_empty_is_noop(self) -> None:
        client = FakeClient()
        mirror = SupabaseMirror("https://x.supabase.co", "key", client_factory=lambda u, k: client)
        mirror.upsert("trades", [])
        assert client.upserts == []

    def test_missing_credentials_raise_auth(self) -> None:
        mirror = SupabaseMirror("", "")
        with pytest.raises(MirrorError) as exc:
            mirror.upsert("trades", [{"id": "1"}])
        assert exc.value.kind == "auth"

    def test_client_factory_failure_is_client_error(self) -> None:
        def broken(_u: str, _k: str) -> object:
            raise RuntimeError("bad url")

        mirror = SupabaseMirror("https://x", "key", client_factory=broken)
        with pytest.raises(MirrorError, match="client"):
            mirror.upsert("trades", [{"id": "1"}])

    @pytest.mark.parametrize(
        ("message", "kind"),
        [
            ("HTTP 401 Unauthorized: Invalid API key", "auth"),
            ("connection timed out", "network"),
            ("getaddrinfo failed", "network"),
            ("HTTP 503 service unavailable", "server"),
            ('relation "trades" does not exist', "client"),
            ("HTTP 400 Bad Request column mismatch", "client"),
            ("weird failure", "network"),
        ],
    )
    def test_error_classification(self, message: str, kind: str) -> None:
        client = FakeClient()
        client.error = RuntimeError(message)
        mirror = SupabaseMirror("https://x", "key", client_factory=lambda u, k: client)
        with pytest.raises(MirrorError) as exc:
            mirror.upsert("trades", [{"id": "1"}])
        assert exc.value.kind == kind

    def test_probe_returns_latency(self) -> None:
        client = FakeClient()
        mirror = SupabaseMirror("https://x", "key", client_factory=lambda u, k: client)
        assert mirror.probe() >= 0.0

    def test_probe_error_propagates(self) -> None:
        client = FakeClient()
        client.error = RuntimeError("HTTP 401")
        mirror = SupabaseMirror("https://x", "key", client_factory=lambda u, k: client)
        with pytest.raises(MirrorError, match="credentials"):
            mirror.probe()

    def test_client_is_cached(self) -> None:
        calls: list[int] = []

        def factory(_u: str, _k: str) -> object:
            calls.append(1)
            return FakeClient()

        mirror = SupabaseMirror("https://x", "key", client_factory=factory)
        mirror.upsert("t", [{"id": "1"}])
        mirror.upsert("t", [{"id": "2"}])
        assert len(calls) == 1

    def test_null_mirror_accepts_everything(self) -> None:
        NullMirror().upsert("t", [{"id": "1"}])  # no exception


# ---------------------------------------------------------------------------
# OutboxWorker
# ---------------------------------------------------------------------------
def _make_config(**overrides: Any) -> OutboxConfig:
    base = {
        "batch_size": 10,
        "flush_interval_s": 0.0,
        "max_attempts": 3,
        "backoff_base_s": 5.0,
        "backoff_max_s": 60.0,
        "cooldown_s": 900.0,
    }
    base.update(overrides)
    return OutboxConfig(**base)


class TestOutboxWorker:
    def test_flush_once_syncs_claimed_batch(self, db: Database, repo: OutboxRepository) -> None:
        repo.enqueue("trades", "t1", {"id": "t1", "symbol": "EURUSD"})
        repo.enqueue("audit_log", "a1", {"id": "a1", "action": "x"})
        sink = FlakySink([None])
        worker = OutboxWorker(db, sink, config=_make_config())
        assert worker.flush_once() == 2
        status = worker.snapshot()
        assert status.synced == 2
        assert status.pending == 0
        assert status.healthy

    def test_flush_once_empty_queue_is_zero(self, db: Database) -> None:
        worker = OutboxWorker(db, FlakySink([]), config=_make_config())
        assert worker.flush_once() == 0

    def test_retry_with_exponential_backoff(self, db: Database, repo: OutboxRepository) -> None:
        repo.enqueue("trades", "t1", {"id": "t1"})
        sink = FlakySink([MirrorError("connection lost", "network")])
        worker = OutboxWorker(db, sink, config=_make_config())

        worker.flush_once()
        entry = repo.claim_batch(5, now="2099-01-01T00:00:00.000Z")[0]  # not due yet
        assert int(entry["attempts"]) == 1
        next_at = str(entry["next_attempt_at"])
        assert "T" in next_at and next_at > "2000-01-01"  # future-scheduled

    def test_attempts_increase_then_dead(self, db: Database, repo: OutboxRepository) -> None:
        repo.enqueue("trades", "t1", {"id": "t1"})
        sink = FlakySink([MirrorError("down", "network")] * 10)
        worker = OutboxWorker(db, sink, config=_make_config())

        for _ in range(3):
            # Force the row due again (the real backoff schedules it in the future).
            db.execute("UPDATE outbox SET next_attempt_at = '2000-01-01T00:00:00.000Z'")
            worker.flush_once()

        status = worker.snapshot()
        assert status.dead == 1
        assert status.last_error is not None

    def test_auth_error_triggers_cooldown(self, db: Database, repo: OutboxRepository) -> None:
        repo.enqueue("trades", "t1", {"id": "t1"})
        sink = FlakySink([MirrorError("HTTP 401 invalid api key", "auth")])
        worker = OutboxWorker(db, sink, config=_make_config())
        worker.flush_once()
        status = worker.snapshot()
        assert status.cooldown_remaining_s > 0
        # A flush during cooldown is skipped entirely.
        assert worker.flush_once() == 0

    def test_flush_all_drains_batches(self, db: Database, repo: OutboxRepository) -> None:
        for i in range(7):
            repo.enqueue("trades", f"t{i}", {"id": f"t{i}"})
        worker = OutboxWorker(db, FlakySink([None] * 10), config=_make_config(batch_size=3))
        assert worker.flush_all() == 7
        assert worker.snapshot().pending == 0

    def test_flush_all_stops_on_failure(self, db: Database, repo: OutboxRepository) -> None:
        for i in range(5):
            repo.enqueue("trades", f"t{i}", {"id": f"t{i}"})
        worker = OutboxWorker(
            db, FlakySink([None, MirrorError("down", "network")]), config=_make_config(batch_size=2)
        )
        pushed = worker.flush_all()
        assert pushed == 2  # first batch ok, second failed → stop

    def test_unreadable_payload_becomes_dead(self, db: Database, repo: OutboxRepository) -> None:
        db.execute(
            "INSERT INTO outbox (id, table_name, row_id, payload, state, attempts,"
            " next_attempt_at, created_at) VALUES ('x', 'trades', 't', 'not-json{',"
            " 'pending', 0, ?, ?)",
            ("2026-01-01T00:00:00.000Z", "2026-01-01T00:00:00.000Z"),
        )
        worker = OutboxWorker(db, FlakySink([]), config=_make_config())
        worker.flush_once()
        status = worker.snapshot()
        assert status.dead == 1

    def test_start_recovers_stale_in_flight(self, db: Database, repo: OutboxRepository) -> None:
        """Crashed worker leftovers return to pending and sync again."""
        entry_id = repo.enqueue("trades", "t1", {"id": "t1"})
        repo.claim_batch(5)  # → in_flight, then "crash"
        worker = OutboxWorker(db, FlakySink([None]), config=_make_config())
        worker.start()
        try:
            worker.flush_all()
        finally:
            worker.stop()
        counts = repo.counts()
        assert counts.get("synced") == 1
        assert counts.get("in_flight", 0) == 0
        assert entry_id

    def test_thread_lifecycle(self, db: Database) -> None:
        worker = OutboxWorker(db, NullMirror(), config=_make_config(flush_interval_s=0.05))
        assert not worker.running
        worker.start()
        assert worker.running
        worker.stop(timeout_s=3.0)
        assert not worker.running

    def test_snapshot_reports_last_error(self, db: Database, repo: OutboxRepository) -> None:
        repo.enqueue("trades", "t1", {"id": "t1"})
        worker = OutboxWorker(
            db,
            FlakySink([MirrorError("Supabase is temporarily unavailable.", "server")]),
            config=_make_config(),
        )
        worker.flush_once()
        status: SyncStatus = worker.snapshot()
        assert status.healthy is False
        assert "unavailable" in (status.last_error or "")
        assert status.cooldown_remaining_s > 0  # server error → global pause
        assert status.to_dict()["dead"] == 0

    def test_worker_flushes_on_background_thread(
        self, db: Database, repo: OutboxRepository
    ) -> None:
        for i in range(3):
            repo.enqueue("trades", f"t{i}", {"id": f"t{i}"})
        worker = OutboxWorker(db, FlakySink([None] * 5), config=_make_config(flush_interval_s=0.1))
        worker.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if worker.snapshot().synced == 3:
                break
            time.sleep(0.05)
        worker.stop()
        assert worker.snapshot().synced == 3
