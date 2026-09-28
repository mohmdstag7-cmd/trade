"""Outbox worker — drains the mirror queue in the background (SPEC E1).

**Retries.** Every failed batch is re-scheduled per row with exponential
backoff (base → max). Rows exceeding ``max_attempts`` become ``dead`` and
are reported in the sync status instead of being lost or silently
retried forever.

**Free-tier awareness.** The worker flushes at most once per
``flush_interval_s`` and caps the batch size. When Supabase reports an
auth/server problem (e.g. a paused free-tier project), the worker enters
a global cooldown instead of hammering the API; local writes keep working
— nothing is ever lost offline.

**Clocks.** Wall time for ``next_attempt_at`` comes from the repository
layer (UTC strings); monotonic scheduling (sleep/interval) is injected so
tests run deterministically without real waiting.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from app.observability.logger import get_logger
from app.observability.masking import mask_text
from app.storage.db import Database
from app.storage.mirror import MirrorError, MirrorSink
from app.storage.repositories import OutboxRepository, now_iso

log = get_logger("sync")

MONOTONIC = Callable[[], float]
SLEEP = Callable[[float], None]


@dataclass(slots=True)
class OutboxConfig:
    """Tuning knobs (defaults are deliberately conservative)."""

    batch_size: int = 100
    flush_interval_s: float = 30.0
    max_attempts: int = 10
    backoff_base_s: float = 5.0
    backoff_max_s: float = 3600.0
    #: Global pause after auth/server errors (paused free-tier project).
    cooldown_s: float = 900.0


@dataclass(frozen=True, slots=True)
class SyncStatus:
    """Snapshot for the UI sync indicator (SPEC E1)."""

    running: bool
    enabled: bool
    pending: int
    in_flight: int
    synced: int
    dead: int
    last_synced_at: str | None
    last_error: str | None
    cooldown_remaining_s: float

    @property
    def healthy(self) -> bool:
        return self.enabled and self.dead == 0 and self.last_error is None

    def to_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "enabled": self.enabled,
            "pending": self.pending,
            "in_flight": self.in_flight,
            "synced": self.synced,
            "dead": self.dead,
            "last_synced_at": self.last_synced_at,
            "last_error": self.last_error,
            "cooldown_remaining_s": round(self.cooldown_remaining_s, 1),
        }


class OutboxWorker:
    """Background thread that flushes the outbox to a :class:`MirrorSink`."""

    def __init__(
        self,
        db: Database,
        sink: MirrorSink,
        *,
        config: OutboxConfig | None = None,
        monotonic_fn: MONOTONIC = time.monotonic,
        sleep_fn: SLEEP = time.sleep,
    ) -> None:
        self._db = db
        self._sink = sink
        self._config = config or OutboxConfig()
        self._monotonic = monotonic_fn
        self._sleep = sleep_fn
        self._repo = OutboxRepository(db)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_error: str | None = None
        self._cooldown_until: float = 0.0

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        """Start the worker thread and recover stale in-flight rows."""
        self._requeue_in_flight()
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="outbox-worker", daemon=True)
        self._thread.start()
        log.info(
            "storage: outbox worker started (batch={}, interval={}s)",
            self._config.batch_size,
            self._config.flush_interval_s,
        )

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop the loop. In-flight rows return to pending on next start."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- flushing ---------------------------------------------------------------
    def _in_cooldown(self) -> bool:
        with self._lock:
            return self._monotonic() < self._cooldown_until

    def flush_once(self) -> int:
        """Claim one batch and push it to the mirror. Returns pushed count.

        Synchronous on purpose: the worker thread calls this, tests call
        it directly, and ``--db-check`` uses it for a one-shot drain.

        Failures are isolated PER TABLE: one persistently failing table
        (e.g. a schema drift on ``mt5_requests``) must not bump the retry
        budget of unrelated tables whose upserts actually succeed —
        previously healthy rows could be parked ``dead`` by someone
        else's error.
        """
        if self._in_cooldown():
            return 0  # honor the global cooldown on the one-shot path too
        claimed = self._repo.claim_batch(self._config.batch_size)
        if not claimed:
            return 0

        by_table: dict[str, list[dict[str, object]]] = {}
        valid_entries: list[dict[str, object]] = []
        for entry in claimed:
            entry_id = str(entry["id"])
            table = str(entry["table_name"])
            try:
                payload = json.loads(str(entry["payload"]))
            except (TypeError, ValueError) as exc:
                self._poison(entry_id, f"unreadable payload: {exc}")
                continue
            entry["payload"] = payload  # parsed payload for the upsert
            by_table.setdefault(table, []).append(entry)
            valid_entries.append(entry)

        synced_ids: list[str] = []
        pushed = 0
        for table, entries in by_table.items():
            rows = cast("list[dict[str, Any]]", [e["payload"] for e in entries])
            try:
                self._sink.upsert(table, rows)
            except MirrorError as exc:
                self._handle_mirror_error(exc, entries)
                continue
            except Exception as exc:  # unexpected sink bug — treat as transient
                log.opt(exception=True).warning(
                    "storage: mirror sink crashed on table {}: {}", table, mask_text(str(exc))
                )
                self._handle_mirror_error(MirrorError(mask_text(str(exc)), "network"), entries)
                continue
            synced_ids.extend(str(e["id"]) for e in entries)
            pushed += len(entries)

        if synced_ids:
            self._repo.mark_synced(synced_ids)
        if pushed and len(synced_ids) == len(valid_entries):
            with self._lock:
                self._last_error = None
        if pushed:
            log.debug("storage: synced {} row(s) to the mirror", pushed)
        return pushed

    def flush_all(self, *, max_batches: int = 100) -> int:
        """Drain every due entry (batch after batch). Returns total rows."""
        total = 0
        for _ in range(max_batches):
            if self._stop_event.is_set():
                break
            pushed = self.flush_once()
            if pushed == 0:
                break
            total += pushed
        return total

    # -- status ---------------------------------------------------------------
    def snapshot(self, *, enabled: bool = True) -> SyncStatus:
        """Current status for the UI (cheap queries, no network)."""
        counts = self._repo.counts()
        with self._lock:
            last_error = self._last_error
            cooldown_until = self._cooldown_until
        now = self._monotonic()
        return SyncStatus(
            running=self.running,
            enabled=enabled,
            pending=counts.get("pending", 0),
            in_flight=counts.get("in_flight", 0),
            synced=counts.get("synced", 0),
            dead=counts.get("dead", 0),
            last_synced_at=self._repo.last_synced_at(),
            last_error=last_error,
            cooldown_remaining_s=max(0.0, cooldown_until - now),
        )

    # -- internals -----------------------------------------------------------------
    def _poison(self, entry_id: str, reason: str) -> None:
        """A payload that can never sync is dead, not retried."""
        log.warning("storage: outbox entry {} parked (dead): {}", entry_id, reason)
        self._repo.mark_dead(entry_id, reason)

    def _handle_mirror_error(
        self,
        exc: MirrorError,
        claimed: list[dict[str, object]],
    ) -> None:
        """Classify the failure: cooldown, per-row backoff, or dead.

        Only the entries of the FAILED request are passed in — the retry
        budget of healthy rows must never be consumed by someone else's
        error. The error text is masked before it reaches status/UI.
        """
        masked = mask_text(str(exc))
        with self._lock:
            self._last_error = masked
        if exc.kind in ("auth", "server"):
            # Paused project or rejected key: back off globally.
            with self._lock:
                self._cooldown_until = self._monotonic() + self._config.cooldown_s
            log.warning("storage: mirror cooldown {}s ({})", self._config.cooldown_s, masked)
        for entry in claimed:
            entry_id = str(entry["id"])
            attempts = int(str(entry["attempts"])) + 1
            error_text = masked
            if attempts >= self._config.max_attempts:
                self._repo.mark_dead(entry_id, error_text)
                log.error("storage: outbox entry dead after {} attempts", attempts)
                continue
            delay = min(
                self._config.backoff_base_s * (2 ** (attempts - 1)),
                self._config.backoff_max_s,
            )
            from datetime import timedelta

            from app.core.clock import utc_now

            next_at = (utc_now() + timedelta(seconds=delay)).strftime("%Y-%m-%dT%H:%M:%S.%f")[
                :-3
            ] + "Z"
            self._repo.mark_retry(entry_id, attempts, next_at, error_text)

    def _requeue_in_flight(self) -> None:
        """Crash recovery: in-flight rows return to pending.

        Only one worker instance owns a database, so at startup no row can
        legitimately be mid-flight — anything left over is leftover from a
        crashed or stopped previous worker.
        """
        with self._db.transaction():
            cursor = self._db.execute(
                "UPDATE outbox SET state = 'pending', updated_at = ? WHERE state = 'in_flight'",
                (now_iso(),),
            )
            if cursor.rowcount:
                log.info("storage: re-queued {} stale in-flight row(s)", cursor.rowcount)

    def _loop(self) -> None:
        """Periodic flush with a 1 s scheduling granularity."""
        next_flush = self._monotonic()
        try:
            while not self._stop_event.wait(1.0):
                now = self._monotonic()
                with self._lock:
                    cooldown_until = self._cooldown_until
                if now < next_flush or now < cooldown_until:
                    continue
                try:
                    self.flush_once()
                except Exception:  # pragma: no cover - flush_once catches everything
                    log.opt(exception=True).error("storage: outbox loop error")
                next_flush = self._monotonic() + self._config.flush_interval_s
        finally:
            # sqlite connections are thread-bound: close our own on exit
            self._db.close_thread_connection()
