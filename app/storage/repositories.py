"""Repositories — the only way business rows reach SQLite (SPEC E1/E2).

**Transactional outbox.** Every insert that should reach Supabase writes
the business row AND its outbox entry inside ONE transaction, so a row
either exists locally and is queued for the mirror, or neither. The
outbox worker (:mod:`app.storage.outbox`) drains the queue later; the
mirror upserts by the row's UUID, which makes offline-first sync
duplicate-free (SPEC G3-4 acceptance).

Rows are plain dictionaries of JSON-compatible values — the domain layer
(pydantic models) arrives with the engine phases and will wrap these.

Timestamps are UTC ISO-8601 strings; ids are UUID4 strings (idempotency
keys, SPEC I). Secrets never enter any repository (SPEC I-6).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from app.storage.db import Database

#: Tables mirrored to Supabase (SPEC E1: business data + WARNING+ logs;
#: high-frequency/perf rows stay local). ``performance_metrics`` is
#: deliberately absent.
MIRRORED_TABLES: frozenset[str] = frozenset(
    {
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
        "daily_reports",
        "calendar_events",
        "calibration_reports",
        "drift_reports",
    }
)


def new_id() -> str:
    """Fresh UUID4 idempotency key."""
    return str(uuid.uuid4())


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    from app.core.clock import utc_now

    return utc_now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def deterministic_id(*parts: str) -> str:
    """Stable UUID5 for rows that must never duplicate on re-import."""
    namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # RFC 4122 URL ns
    return str(uuid.uuid5(namespace, ":".join(parts)))


def _require(row: dict[str, Any], field: str, table: str) -> None:
    if not row.get(field):
        msg = f"{table}.{field} is required"
        raise ValueError(msg)


class BaseRepository:
    """CRUD with automatic outbox mirroring for business tables."""

    def __init__(self, db: Database, table: str, *, mirrored: bool | None = None) -> None:
        self._db = db
        self.table = table
        self._mirrored = mirrored if mirrored is not None else table in MIRRORED_TABLES

    # -- write -----------------------------------------------------------------
    def insert(self, row: dict[str, Any], *, mirror: bool | None = None) -> str:
        """Insert one row; enqueue the mirror atomically. Returns the id."""
        row = dict(row)
        if not row.get("id"):
            row["id"] = new_id()
        row.setdefault("created_at", now_iso())
        should_mirror = self._mirrored if mirror is None else mirror

        columns = sorted(row)
        placeholders = ", ".join("?" for _ in columns)
        quoted = ", ".join(f'"{c}"' for c in columns)
        sql = f'INSERT INTO "{self.table}" ({quoted}) VALUES ({placeholders})'  # noqa: S608

        with self._db.transaction():
            self._db.execute(sql, [row[c] for c in columns])
            if should_mirror:
                OutboxRepository(self._db).enqueue(self.table, str(row["id"]), row)
        return str(row["id"])

    def insert_many(self, rows: list[dict[str, Any]], *, mirror: bool | None = None) -> int:
        """Insert several rows in one transaction; returns the count."""
        for row in rows:
            self.insert(row, mirror=mirror)
        return len(rows)

    def update(self, row_id: str, changes: dict[str, Any]) -> bool:
        """Patch columns and re-enqueue the updated row for the mirror."""
        row = self.get(row_id)
        if row is None:
            return False
        row = {**row, **changes}
        if not changes:
            return True
        columns = sorted(changes)
        assignments = ", ".join(f'"{c}" = ?' for c in columns)
        sql = f'UPDATE "{self.table}" SET {assignments} WHERE id = ?'  # noqa: S608
        with self._db.transaction():
            self._db.execute(sql, [changes[c] for c in columns] + [row_id])
            if self._mirrored:
                OutboxRepository(self._db).enqueue(self.table, row_id, row)
        return True

    # -- read --------------------------------------------------------------------
    def get(self, row_id: str) -> dict[str, Any] | None:
        rows = self._db.query(f'SELECT * FROM "{self.table}" WHERE id = ?', (row_id,))  # noqa: S608
        return dict(rows[0]) if rows else None

    def exists(self, row_id: str) -> bool:
        return self.get(row_id) is not None

    def count(self) -> int:
        return self._db.row_count(self.table)

    def recent(self, limit: int = 50, order_by: str = "created_at") -> list[dict[str, Any]]:
        rows = self._db.query(  # noqa: S608
            f'SELECT * FROM "{self.table}" ORDER BY "{order_by}" DESC LIMIT ?',
            (limit,),
        )
        return [dict(r) for r in rows]

    def delete(self, row_id: str) -> None:
        self._db.execute(f'DELETE FROM "{self.table}" WHERE id = ?', (row_id,))  # noqa: S608


class OutboxRepository:
    """The mirror queue: claim → upsert → ack, with retry bookkeeping."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def enqueue(self, table_name: str, row_id: str, payload: dict[str, Any]) -> str:
        """Queue one row for the mirror (caller owns the transaction)."""
        entry_id = new_id()
        self._db.execute(
            "INSERT INTO outbox (id, table_name, row_id, payload, state,"
            " attempts, next_attempt_at, created_at)"
            " VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)",
            (
                entry_id,
                table_name,
                row_id,
                json.dumps(payload, ensure_ascii=False),
                now_iso(),
                now_iso(),
            ),
        )
        return entry_id

    def claim_batch(self, limit: int, *, now: str | None = None) -> list[dict[str, Any]]:
        """Atomically claim due pending entries and mark them ``in_flight``."""
        stamp = now or now_iso()
        claimed: list[dict[str, Any]] = []
        with self._db.transaction():
            rows = self._db.query(
                "SELECT * FROM outbox WHERE state = 'pending' AND next_attempt_at <= ?"
                " ORDER BY created_at LIMIT ?",
                (stamp, limit),
            )
            for row in rows:
                self._db.execute(
                    "UPDATE outbox SET state = 'in_flight', updated_at = ? WHERE id = ?",
                    (stamp, row["id"]),
                )
                claimed.append(dict(row))
        return claimed

    def mark_synced(self, entry_ids: list[str]) -> None:
        """Acknowledge successful upserts (state ``synced``)."""
        if not entry_ids:
            return
        with self._db.transaction():
            for entry_id in entry_ids:
                self._db.execute(
                    "UPDATE outbox SET state = 'synced', last_error = NULL, updated_at = ?"
                    " WHERE id = ?",
                    (now_iso(), entry_id),
                )

    def mark_retry(self, entry_id: str, attempts: int, next_attempt_at: str, error: str) -> None:
        """Schedule the next attempt with the failure reason (masked upstream)."""
        with self._db.transaction():
            self._db.execute(
                "UPDATE outbox SET state = 'pending', attempts = ?, next_attempt_at = ?,"
                " last_error = ?, updated_at = ? WHERE id = ?",
                (attempts, next_attempt_at, error[:500], now_iso(), entry_id),
            )

    def mark_dead(self, entry_id: str, error: str) -> None:
        """Exhausted retries: park the row for manual inspection."""
        with self._db.transaction():
            self._db.execute(
                "UPDATE outbox SET state = 'dead', last_error = ?, updated_at = ? WHERE id = ?",
                (error[:500], now_iso(), entry_id),
            )

    def counts(self) -> dict[str, int]:
        rows = self._db.query("SELECT state, COUNT(*) AS n FROM outbox GROUP BY state")
        return {str(r["state"]): int(r["n"]) for r in rows}

    def last_synced_at(self) -> str | None:
        row = self._db.query_one(
            "SELECT updated_at FROM outbox WHERE state = 'synced' ORDER BY updated_at DESC LIMIT 1"
        )
        return str(row["updated_at"]) if row else None

    def total(self) -> int:
        return self._db.row_count("outbox")


class SignalRepository(BaseRepository):
    """Signal rows and their state-machine transitions (SPEC C5)."""

    def __init__(self, db: Database) -> None:
        super().__init__(db, "signals")

    def insert_signal(self, row: dict[str, Any]) -> str:
        _require(row, "strategy", "signals")
        row.setdefault("state", "new")
        return self.insert(row)

    def update_state(
        self,
        signal_id: str,
        state: str,
        *,
        decision: str | None = None,
        reject_reason: str | None = None,
    ) -> bool:
        """Transition the state machine and mirror the new row version."""
        changes: dict[str, Any] = {"state": state}
        if decision is not None:
            changes["decision"] = decision
        if reject_reason is not None:
            changes["reject_reason"] = reject_reason
        return self.update(signal_id, changes)


class TradeRepository(BaseRepository):
    """Trades (bot, manual and imported history rows)."""

    def __init__(self, db: Database) -> None:
        super().__init__(db, "trades")

    def close_trade(self, trade_id: str, result: dict[str, Any]) -> bool:
        """Persist close data (price, costs, outcome) and mirror it."""
        allowed = (
            "close_time",
            "close_price",
            "profit",
            "commission",
            "swap",
            "net_profit",
            "r_multiple",
            "outcome",
            "exit_reason",
            "duration_sec",
            "mfe_r",
            "mae_r",
        )
        changes = {k: result[k] for k in allowed if k in result}
        return self.update(trade_id, changes)

    def open_trades(self) -> list[dict[str, Any]]:
        rows = self._db.query(
            "SELECT * FROM trades WHERE close_time IS NULL ORDER BY open_time DESC"
        )
        return [dict(r) for r in rows]

    def closed_trades(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._db.query(
            "SELECT * FROM trades WHERE close_time IS NOT NULL ORDER BY close_time DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def import_row(self, row: dict[str, Any]) -> bool:
        """Idempotent history import: existing ids are never overwritten.

        Returns ``True`` only when the row was newly created.
        """
        row = dict(row)
        if not row.get("id"):
            row["id"] = deterministic_id(
                "trade", str(row.get("ticket", "")), str(row.get("position_id", ""))
            )
        if self.exists(str(row["id"])):
            return False
        row.setdefault("source", "import")
        row.setdefault("created_at", now_iso())

        columns = sorted(row)
        placeholders = ", ".join("?" for _ in columns)
        quoted = ", ".join(f'"{c}"' for c in columns)
        with self._db.transaction():
            self._db.execute(
                f"INSERT INTO trades ({quoted}) VALUES ({placeholders})",  # noqa: S608
                [row[c] for c in columns],
            )
            OutboxRepository(self._db).enqueue("trades", str(row["id"]), row)
        return True


class DecisionTraceRepository(BaseRepository):
    def __init__(self, db: Database) -> None:
        super().__init__(db, "decision_traces")

    def insert_trace(self, signal_id: str, steps: list[dict[str, Any]], final_decision: str) -> str:
        return self.insert(
            {
                "signal_id": signal_id,
                "steps_json": json.dumps(steps, ensure_ascii=False),
                "final_decision": final_decision,
            }
        )


class AuditLogRepository(BaseRepository):
    """Every user/system/ai action (before → after), SPEC E3.13."""

    def __init__(self, db: Database) -> None:
        super().__init__(db, "audit_log")

    def record(
        self,
        action: str,
        *,
        source: str = "user",
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> str:
        return self.insert(
            {
                "source": source,
                "action": action,
                "before_json": json.dumps(before, ensure_ascii=False) if before else None,
                "after_json": json.dumps(after, ensure_ascii=False) if after else None,
            }
        )


class HealthCheckRepository(BaseRepository):
    def __init__(self, db: Database) -> None:
        super().__init__(db, "health_checks")

    def record(self, component: str, status: str, detail: str = "") -> str:
        return self.insert({"component": component, "status": status, "detail": detail})

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return super().recent(limit=limit)


class Mt5RequestRepository(BaseRepository):
    """Request/response audit for MT5 calls (SPEC E2)."""

    def __init__(self, db: Database) -> None:
        super().__init__(db, "mt5_requests")

    def record(
        self,
        action: str,
        *,
        trace_id: str | None = None,
        request_payload: dict[str, Any] | None = None,
        retcode: int | None = None,
        retcode_text: str | None = None,
        result: dict[str, Any] | None = None,
        last_error: str | None = None,
        latency_ms: float | None = None,
        attempt: int = 1,
    ) -> str:
        return self.insert(
            {
                "trace_id": trace_id,
                "action": action,
                "request_json": json.dumps(request_payload, ensure_ascii=False)
                if request_payload
                else None,
                "retcode": retcode,
                "retcode_text": retcode_text,
                "result_json": json.dumps(result, ensure_ascii=False) if result else None,
                "last_error": last_error,
                "latency_ms": latency_ms,
                "attempt": attempt,
            }
        )


class AccountSnapshotRepository(BaseRepository):
    def __init__(self, db: Database) -> None:
        super().__init__(db, "account_snapshots")

    def record(self, fields: dict[str, Any]) -> str:
        return self.insert(fields)


class RiskEventRepository(BaseRepository):
    def __init__(self, db: Database) -> None:
        super().__init__(db, "risk_events")

    def record(self, event_type: str, details: dict[str, Any] | None = None) -> str:
        return self.insert(
            {
                "type": event_type,
                "details_json": json.dumps(details, ensure_ascii=False) if details else None,
            }
        )


class SessionRepository(BaseRepository):
    def __init__(self, db: Database) -> None:
        super().__init__(db, "sessions")

    def start(self, app_version: str, mode: str = "analysis", profile: str = "") -> str:
        session_id = new_id()
        self.insert(
            {
                "id": session_id,
                "app_version": app_version,
                "started_at": now_iso(),
                "mode": mode,
                "profile": profile,
            },
            mirror=False,
        )
        return session_id

    def end(self, session_id: str) -> None:
        self.update(session_id, {"ended_at": now_iso()})


class AppLogRepository(BaseRepository):
    """WARNING+ log rows (SPEC E1: mirrored; DEBUG stays local-only)."""

    def __init__(self, db: Database) -> None:
        super().__init__(db, "app_logs")

    def bulk_insert(self, rows: list[dict[str, Any]]) -> int:
        for row in rows:
            self.insert(row)
        return len(rows)


def repository_for(db: Database, table: str) -> BaseRepository:
    """Generic typed access for tables without dedicated logic yet."""
    return BaseRepository(db, table)


def make_outbox_sink(db: Database) -> Callable[[str, str, dict[str, Any]], str]:
    """Convenience factory for tests / advanced callers."""
    return OutboxRepository(db).enqueue
