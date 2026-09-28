"""Tests for StorageService (facade) and the --db-check / self-check gates."""

from __future__ import annotations

import pathlib
import uuid

import pytest

from app.main import run_db_check, run_self_check
from app.storage.db import Database, DatabaseError
from app.storage.service import StorageService


@pytest.fixture
def data_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    # pytest reuses numbered tmp dirs across runs — stay unique inside them.
    return tmp_path / f"appdata-{uuid.uuid4().hex[:8]}"


class TestStorageService:
    def test_open_migrates_and_starts(self, data_dir: pathlib.Path) -> None:
        service = StorageService(data_dir)
        service.open()
        try:
            assert (data_dir / "data" / "workstation.db").exists()
            assert service.db.journal_mode() == "wal"
            assert service.sessions.count() == 1  # startup session row
            status = service.sync_status()
            assert status["running"] is True
            assert status["enabled"] is False  # no credentials → local-only
        finally:
            service.close()

    def test_close_ends_session(self, data_dir: pathlib.Path) -> None:
        service = StorageService(data_dir)
        service.open()
        service.close()
        # closed db — verify through a fresh handle at the same path
        check = Database(data_dir / "data" / "workstation.db")
        row = check.query_one("SELECT ended_at FROM sessions ORDER BY started_at DESC")
        assert row is not None and row["ended_at"] is not None
        check.close_all()

    def test_backup_created_on_open(self, data_dir: pathlib.Path) -> None:
        service = StorageService(data_dir)
        service.open()
        service.close()
        assert len(list((data_dir / "backups").glob("workstation_*.db"))) == 1

    def test_repositories_write_through_facade(self, data_dir: pathlib.Path) -> None:
        service = StorageService(data_dir)
        service.open()
        try:
            trade_id = service.trades.insert({"symbol": "EURUSD", "direction": "buy"})
            assert service.trades.get(trade_id) is not None
            service.health.record("db", "ok")
            assert service.db.row_count("health_checks") == 1
            # mirrored rows queued for the cloud automatically
            assert service.outbox.counts().get("pending", 0) >= 1
        finally:
            service.close()

    def test_stats_shape(self, data_dir: pathlib.Path) -> None:
        service = StorageService(data_dir)
        service.open()
        try:
            stats = service.stats()
            assert stats["integrity_ok"] is True
            assert stats["version"] == 2  # migration 002: outbox maintenance index
            assert stats["cloud_enabled"] is False
            assert set(stats["rows"]) == {
                "signals",
                "trades",
                "audit_log",
                "app_logs",
                "health_checks",
            }
        finally:
            service.close()

    def test_second_open_is_idempotent(self, data_dir: pathlib.Path) -> None:
        for _ in range(2):
            service = StorageService(data_dir)
            service.open()
            service.close()

    def test_apply_cloud_config_without_credentials_stays_local(
        self, data_dir: pathlib.Path
    ) -> None:
        service = StorageService(data_dir)
        service.open()
        try:
            assert service.apply_cloud_config() is False
            assert service.sync_status()["enabled"] is False
        finally:
            service.close()


class _Args:
    """argparse.Namespace stand-in (explicit ctor: class bodies cannot
    resolve the enclosing test's `data_dir` fixture parameter)."""

    def __init__(self, data_dir: str, json: bool = False) -> None:
        self.data_dir = data_dir
        self.json = json


class TestDbCheckCli:
    def test_db_check_fresh_dir_succeeds(
        self, data_dir: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run_db_check(_Args(str(data_dir))) == 0
        out = capsys.readouterr().out
        assert "Integrity: OK" in out
        assert "Schema version: 2" in out
        assert "local-only" in out

    def test_db_check_json_mode(
        self, data_dir: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import json

        assert run_db_check(_Args(str(data_dir), json=True)) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["integrity_ok"] is True
        assert payload["version"] == 2
        assert payload["cloud_enabled"] is False

    def test_db_check_twice_is_idempotent(self, data_dir: pathlib.Path) -> None:
        assert run_db_check(_Args(str(data_dir))) == 0
        assert run_db_check(_Args(str(data_dir))) == 0

    def test_db_check_failure_exit_one(self, tmp_path: pathlib.Path) -> None:
        # A directory where the DB file should be → open fails.
        (tmp_path / "blocker" / "data").mkdir(parents=True)
        (tmp_path / "blocker" / "data" / "workstation.db").mkdir()
        assert run_db_check(_Args(str(tmp_path / "blocker"))) == 1


class TestSelfCheckStorageGate:
    def test_self_check_reports_storage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The MT5 gate still dominates; storage probe runs afterwards."""
        import types

        fake = types.ModuleType("MetaTrader5")
        fake.__version__ = "5.0.6180"
        result = run_self_check(import_mt5=lambda: fake)
        assert result == 0

    def test_self_check_fails_when_mt5_missing(self) -> None:
        def boom() -> object:
            raise ImportError("no MetaTrader5")

        assert run_self_check(import_mt5=boom) == 1


class TestCloudUrlSettings:
    def test_cloud_url_roundtrip(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        from app.core import settings as settings_mod

        ini = tmp_path / "settings.ini"
        monkeypatch.setattr(settings_mod, "_ORG", "test-org-cloud")
        monkeypatch.setattr(settings_mod, "_APP", f"test-app-cloud-{ini}")

        settings_mod.save_cloud_url("https://example.supabase.co/")
        assert settings_mod.load_cloud_url() == "https://example.supabase.co"
        settings_mod.save_cloud_url("")
        assert settings_mod.load_cloud_url() == ""


def test_close_without_open_is_safe(data_dir: pathlib.Path) -> None:
    service = StorageService(data_dir)
    service.close()  # no session row, no workers — must not raise


def test_open_failure_raises_database_error(tmp_path: pathlib.Path) -> None:
    # A directory where the DB file should be makes sqlite fail on open.
    (tmp_path / "data" / "workstation.db").mkdir(parents=True)
    service = StorageService(tmp_path)
    with pytest.raises(DatabaseError):
        service.open()
