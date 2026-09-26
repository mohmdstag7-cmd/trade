"""SQLite database access — the local source of truth (SPEC E1).

**WAL mode.** Readers never block the writer and crash recovery is cheap,
which matters for a long-running desktop app that writes on every bar,
request and health check.

**Threading contract.** ``sqlite3`` connections are not shared across
threads by default. Every thread that touches the database gets its OWN
connection through a :class:`threading.local`; closing happens explicitly
on shutdown via :meth:`Database.close_all`. This mirrors the MT5 gateway
philosophy: each layer owns its threading story and documents it.

**Rows.** Every business table follows SPEC I: a UUID string primary key
(idempotency key) and ``created_at`` in UTC ISO-8601. Repositories build
such rows; this module stays generic.

The module never stores or logs secrets (SPEC I-6).
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.observability.logger import get_logger

log = get_logger("sync")

BUSY_TIMEOUT_MS = 5_000


class DatabaseError(Exception):
    """Raised when the database cannot be opened or a pragma fails."""


class Database:
    """A WAL-mode SQLite database with per-thread connections."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []
        self._all_conns_lock = threading.Lock()
        self._closed = False

    # -- properties ----------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    # -- connection management -------------------------------------------------
    def _connect_raw(self) -> sqlite3.Connection:
        """Open a new raw connection and apply pragmas."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._path),
                timeout=BUSY_TIMEOUT_MS / 1000.0,
                isolation_level=None,  # explicit transaction control
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            return conn
        except sqlite3.Error as exc:
            msg = f"could not open the database at {self._path}: {exc}"
            raise DatabaseError(msg) from exc

    def connection(self) -> sqlite3.Connection:
        """Return the calling thread's connection (created on first use)."""
        if self._closed:
            msg = "database is closed"
            raise DatabaseError(msg)
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect_raw()
            self._local.conn = conn
            with self._all_conns_lock:
                self._all_conns.append(conn)
        return conn

    # -- helpers ---------------------------------------------------------------
    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        """Run a single statement on the calling thread's connection."""
        return self.connection().execute(sql, params)

    def executescript(self, script: str) -> None:
        """Run a multi-statement script (DDL migrations)."""
        self.connection().executescript(script)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        """Fetch all rows for a SELECT."""
        return list(self.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        """Fetch a single row or ``None``."""
        row: sqlite3.Row | None = self.execute(sql, params).fetchone()
        return row

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Explicit transaction: BEGIN IMMEDIATE → COMMIT / ROLLBACK.

        Nested calls reuse the outer transaction so repositories can be
        composed inside one atomic write (the outbox pattern relies on
        this: business row + outbox row commit together or not at all).
        """
        conn = self.connection()
        if getattr(self._local, "in_transaction", False):
            yield conn
            return
        self._local.in_transaction = True
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            self._local.in_transaction = False

    # -- lifecycle ----------------------------------------------------------------
    def close_thread_connection(self) -> None:
        """Close the calling thread's connection (e.g. a worker shutting down)."""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):  # close is best effort
                conn.close()
            self._local.conn = None
            with self._all_conns_lock:
                if conn in self._all_conns:
                    self._all_conns.remove(conn)

    def close_all(self) -> None:
        """Close every connection opened by any thread (shutdown path)."""
        with self._all_conns_lock:
            conns = list(self._all_conns)
        for conn in conns:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
        with self._all_conns_lock:
            self._all_conns.clear()
        self._local = threading.local()
        self._closed = True

    # -- introspection ---------------------------------------------------------
    def table_names(self) -> list[str]:
        """Names of all user tables (migrations applied or not)."""
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [str(r["name"]) for r in rows]

    def row_count(self, table: str) -> int:
        """Row count of ``table`` (0 when the table does not exist)."""
        if table not in self.table_names():
            return 0
        row = self.query_one(f'SELECT COUNT(*) AS n FROM "{table}"')
        return int(row["n"]) if row is not None else 0

    def size_bytes(self) -> int:
        """Size of the main database file (0 when missing)."""
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def wal_size_bytes(self) -> int:
        """Size of the WAL sidecar file (0 when missing/checkpointed)."""
        try:
            return self._path.with_name(self._path.name + "-wal").stat().st_size
        except OSError:
            return 0

    def integrity_ok(self) -> bool:
        """Run the SQLite integrity check and report the verdict."""
        row = self.query_one("PRAGMA integrity_check")
        return bool(row is not None and str(row[0]) == "ok")

    def journal_mode(self) -> str:
        """Current journal mode (``wal`` after pragmas applied)."""
        row = self.query_one("PRAGMA journal_mode")
        return str(row[0]) if row is not None else "unknown"
