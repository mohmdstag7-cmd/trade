"""StorageService — one façade composing the whole storage layer (SPEC E1).

Lifecycle (called from the composition root):
1. ``open()``   — migrate → backup (daily, keep 7) → start session row →
   start the outbox worker, the cleanup scheduler and the WARNING+ log sink.
2. ``close()``  — end the session row, stop all threads, flush, close DB.

Cloud sync is opt-in: without configuration a :class:`NullMirror` drops
outbox rows' network leg while the local queue still records everything
(status shows ``enabled=False``). ``enable_cloud`` swaps in a real
:class:`SupabaseMirror` built from the vault-backed credentials.
"""

from __future__ import annotations

import contextlib
import pathlib
import threading
from typing import Any

from loguru import logger

from app.__version__ import __version__
from app.observability.logger import get_logger
from app.storage.audit import AuditService
from app.storage.backup import BackupService
from app.storage.cleanup import CleanupScheduler, RetentionService
from app.storage.db import Database, DatabaseError
from app.storage.log_sink import StorageLogSink
from app.storage.migrations import MigrationRunner
from app.storage.mirror import (
    SUPABASE_KEY_ENTRY,
    MirrorSink,
    NullMirror,
    SupabaseMirror,
)
from app.storage.outbox import OutboxWorker
from app.storage.repositories import (
    AccountSnapshotRepository,
    AppLogRepository,
    AuditLogRepository,
    DecisionTraceRepository,
    HealthCheckRepository,
    Mt5RequestRepository,
    OutboxRepository,
    RiskEventRepository,
    SessionRepository,
    SignalRepository,
    TradeRepository,
)
from app.storage.vault import KeyringVault

log = get_logger("sync")


class StorageService:
    """Owns the database and every storage-side worker."""

    def __init__(self, data_dir: str | pathlib.Path) -> None:
        self._data_dir = pathlib.Path(data_dir)
        self._db = Database(self._data_dir / "data" / "workstation.db")
        self._backups_dir = self._data_dir / "backups"
        self._backup = BackupService(self._db, self._backups_dir, keep=7)
        self._runner = MigrationRunner(self._db)
        self._outbox_repo = OutboxRepository(self._db)
        self._worker: OutboxWorker | None = None
        self._cleanup: CleanupScheduler | None = None
        self._log_sink: StorageLogSink | None = None
        self._loguru_sink_id: int | None = None
        self._last_integrity = True
        self._session_id: str | None = None
        self._cloud_enabled = False

    # -- repos (created lazily; all share the db) ------------------------------
    @property
    def db(self) -> Database:
        return self._db

    @property
    def signals(self) -> SignalRepository:
        return SignalRepository(self._db)

    @property
    def trades(self) -> TradeRepository:
        return TradeRepository(self._db)

    @property
    def decisions(self) -> DecisionTraceRepository:
        return DecisionTraceRepository(self._db)

    @property
    def audit(self) -> AuditService:
        return AuditService(self._db)

    @property
    def audit_repo(self) -> AuditLogRepository:
        return AuditLogRepository(self._db)

    @property
    def health(self) -> HealthCheckRepository:
        return HealthCheckRepository(self._db)

    @property
    def mt5_requests(self) -> Mt5RequestRepository:
        return Mt5RequestRepository(self._db)

    @property
    def account_snapshots(self) -> AccountSnapshotRepository:
        return AccountSnapshotRepository(self._db)

    @property
    def risk_events(self) -> RiskEventRepository:
        return RiskEventRepository(self._db)

    @property
    def app_logs(self) -> AppLogRepository:
        return AppLogRepository(self._db)

    @property
    def sessions(self) -> SessionRepository:
        return SessionRepository(self._db)

    @property
    def outbox(self) -> OutboxRepository:
        return self._outbox_repo

    # -- lifecycle ----------------------------------------------------------------
    def open(self) -> None:
        """Migrate, back up, start the session row and all workers."""
        applied = self._runner.run_all()
        if applied:
            log.info("storage: schema at version {:03d}", self._runner.current_version())
        if not self._runner.verify():
            msg = "SQLite integrity check failed"
            raise DatabaseError(msg)
        self._last_integrity = True
        self._backup.backup_if_due()
        self._session_id = self.sessions.start(__version__)

        self._worker = OutboxWorker(self._db, self._mirror_sink())
        self._worker.start()
        self._cleanup = CleanupScheduler(
            RetentionService(self._db),
            on_cycle=self.refresh_integrity,  # off-thread integrity refresh
        )
        self._cleanup.start()
        self._log_sink = StorageLogSink(self._db)
        self._log_sink.start()
        # Wire the sink into loguru: without this, ``__call__`` is never
        # invoked and app_logs stays empty in production (tests wire the
        # sink manually, which is why CI never noticed).
        self._loguru_sink_id = logger.add(
            self._log_sink,
            level="WARNING",
            filter=lambda record: record["extra"].get("category") != "sync",
            enqueue=False,
            catch=False,
        )
        log.info(
            "storage: ready (db={}, cloud={})",
            self._db.path.name,
            "on" if self._cloud_enabled else "off",
        )

    def close(self) -> None:
        """End the session and shut every worker down cleanly."""
        if self._session_id is not None:
            with contextlib.suppress(DatabaseError):  # shutdown is best effort
                self.sessions.end(self._session_id)
        if self._loguru_sink_id is not None:
            with contextlib.suppress(Exception):
                logger.remove(self._loguru_sink_id)
            self._loguru_sink_id = None
        if self._log_sink is not None:
            self._log_sink.stop()
        if self._cleanup is not None:
            self._cleanup.stop()
        if self._worker is not None:
            self._worker.stop()
        self._db.close_all()
        log.info("storage: closed")

    # -- cloud ---------------------------------------------------------------------
    def _mirror_sink(self) -> MirrorSink:
        """Build the mirror from vault-backed credentials when configured."""
        creds = self.load_cloud_credentials()
        if creds is not None:
            url, key = creds
            self._cloud_enabled = True
            return SupabaseMirror(url, key)
        self._cloud_enabled = False
        return NullMirror()

    def load_cloud_credentials(self) -> tuple[str, str] | None:
        """(url, key) from QSettings+vault, or None when not configured.

        A missing or broken OS vault is treated as "not configured" — the
        app keeps working in local-only mode (SPEC E1: no data loss offline).
        """
        from app.core.settings import load_cloud_url

        try:
            url = load_cloud_url()
            if not url:
                return None
            key = KeyringVault().get(SUPABASE_KEY_ENTRY)
        except Exception as exc:  # VaultError and broken backends
            log.warning("storage: cloud credentials unavailable ({}); local-only mode", exc)
            return None
        if not key:
            return None
        return url, key

    def cloud_enabled(self) -> bool:
        return self._cloud_enabled

    def apply_cloud_config(self) -> bool:
        """Re-evaluate credentials and (re)build the worker accordingly.

        Enforces single-worker invariant: the old worker is joined with a
        short timeout before the new one starts, preventing two workers
        from concurrently claiming the same outbox rows. If the old worker
        does not exit in time, the new worker start is deferred to a
        background thread after the old thread terminates.
        """
        old_worker = self._worker
        if old_worker is not None:
            old_worker.stop(timeout_s=2.0)
            if old_worker.running:
                log.warning("storage: old outbox worker did not stop in time; deferring new worker")

                def _deferred_start() -> None:
                    thread = old_worker._thread
                    if thread is not None:
                        thread.join(timeout=5.0)
                    self._worker = OutboxWorker(self._db, self._mirror_sink())
                    self._worker.start()

                threading.Thread(target=_deferred_start, name="outbox-restart", daemon=True).start()
                return self._cloud_enabled
        self._worker = OutboxWorker(self._db, self._mirror_sink())
        self._worker.start()
        return self._cloud_enabled

    def outbox_worker(self) -> OutboxWorker | None:
        return self._worker

    # -- status ----------------------------------------------------------------------
    def sync_status(self) -> dict[str, Any]:
        """Snapshot for the UI/CLI sync indicator."""
        if self._worker is None:
            return {"running": False, "enabled": self._cloud_enabled}
        return self._worker.snapshot(enabled=self._cloud_enabled).to_dict()

    def refresh_integrity(self) -> None:
        """Re-run PRAGMA integrity_check (call from a worker thread)."""
        self._last_integrity = self._db.integrity_ok()

    def stats(self, *, integrity: bool = False) -> dict[str, Any]:
        """Compact overview for ``--db-check`` and the Settings page.

        ``integrity`` runs ``PRAGMA integrity_check`` — an O(DB-size)
        full scan that must NOT run on the UI thread's 2 s poll (the
        Settings card shows the result of the LAST scheduled check).
        """
        counts = {
            t: self._db.row_count(t)
            for t in ("signals", "trades", "audit_log", "app_logs", "health_checks")
        }
        return {
            "version": int(self._runner.current_version()),
            "path": str(self._db.path),
            "size_bytes": self._db.size_bytes(),
            "wal_size_bytes": self._db.wal_size_bytes(),
            "integrity_ok": self._db.integrity_ok() if integrity else self._last_integrity,
            "cloud_enabled": self._cloud_enabled,
            "outbox": self._outbox_repo.counts(),
            "rows": counts,
            "backups": len(self._backup.snapshots()),
        }
