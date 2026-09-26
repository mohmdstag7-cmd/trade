"""Daily database backups — keep the last 7 (SPEC E1).

Uses SQLite's online backup API (``Connection.backup``), which produces a
consistent snapshot while writers are active — no locks, no downtime.
``keep`` rotates the oldest files away. Trades and signals are irreplaceable;
this is the last line of defense against disk corruption and bad migrations.
"""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.observability.logger import get_logger
from app.storage.db import Database

log = get_logger("sync")


class BackupService:
    """Daily, rotating SQLite snapshots."""

    def __init__(self, db: Database, backups_dir: Path, keep: int = 7) -> None:
        self._db = db
        self._dir = backups_dir
        self._keep = keep

    # -- API -----------------------------------------------------------------
    def backup_if_due(self, *, today: str | None = None) -> Path | None:
        """Create today's snapshot when it does not exist yet; else ``None``."""
        stamp = today or datetime.now(UTC).strftime("%Y%m%d")
        target = self._dir / f"workstation_{stamp}.db"
        if target.exists():
            return None
        self._dir.mkdir(parents=True, exist_ok=True)
        source = self._db.connection()
        try:
            # NOTE: sqlite3 connections must be closed explicitly — the
            # context manager only manages transactions. On Windows an open
            # handle would block file rotation below.
            dest = sqlite3.connect(str(target))
            try:
                source.backup(dest)
            finally:
                dest.close()
        except (sqlite3.Error, OSError) as exc:
            log.warning("storage: backup failed: {}", exc)
            return None
        self._rotate()
        log.info("storage: backup created ({})", target.name)
        return target

    def _rotate(self) -> None:
        """Delete the oldest snapshots beyond ``keep``."""
        backups = sorted(self._dir.glob("workstation_*.db"))
        for old in backups[: max(0, len(backups) - self._keep)]:
            with contextlib.suppress(OSError):
                old.unlink()

    def snapshots(self) -> list[Path]:
        """Existing backup files, oldest first."""
        return sorted(self._dir.glob("workstation_*.db"))
