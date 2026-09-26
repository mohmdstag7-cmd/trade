"""Storage layer (Phase 4, SPEC E1/E2).

Local SQLite is the source of truth; Supabase is an async mirror fed by the
outbox pattern. See :mod:`app.storage.db`, :mod:`app.storage.migrations`,
:mod:`app.storage.repositories`, :mod:`app.storage.outbox` and
:mod:`app.storage.mirror`.
"""

from __future__ import annotations

from app.storage.db import Database, DatabaseError
from app.storage.migrations import Migration, MigrationRunner

__all__ = ["Database", "DatabaseError", "Migration", "MigrationRunner"]
