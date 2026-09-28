"""Storage log sink — WARNING+ records land in ``app_logs`` (SPEC E1).

Supabase receives business data and WARNING+ logs; DEBUG/TRACE stay
local-only. This loguru sink buffers qualifying records and flushes them
in batches from a small background thread, so logging never blocks the
caller (the same contract as the file sinks). Values are already masked
by the global redaction filter; the payload we build only reads fields,
never extras we do not control.

The outbox automatically mirrors these rows to the cloud when enabled.
"""

from __future__ import annotations

import threading
from typing import Any

from app.observability.logger import get_logger
from app.observability.masking import mask_text
from app.storage.db import Database
from app.storage.repositories import AppLogRepository, new_id, now_iso

log = get_logger("sync")

#: Minimum level mirrored into storage (SPEC E1: WARNING+).
DEFAULT_MIN_LEVEL = "WARNING"
_LEVEL_ORDER = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 25,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


def _opt_str(value: Any) -> str | None:
    """``None``/empty strings become SQL NULL; everything else a str."""
    if value is None:
        return None
    text = str(value)
    return text or None


class StorageLogSink:
    """Buffered WARNING+ loguru sink writing to the ``app_logs`` table."""

    def __init__(
        self,
        db: Database,
        *,
        min_level: str = DEFAULT_MIN_LEVEL,
        flush_interval_s: float = 5.0,
        max_buffer: int = 500,
    ) -> None:
        self._db = db
        self._repo = AppLogRepository(db)
        self._min_level = _LEVEL_ORDER.get(min_level.upper(), 30)
        self._flush_interval = flush_interval_s
        self._max_buffer = max_buffer
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- loguru sink ------------------------------------------------------------
    def __call__(self, message: Any) -> None:
        """Loguru sink entry point (called on the emitting thread).

        ``message`` is the loguru ``Message`` TypedDict; typed as ``Any``
        because loguru only exports it from a private module.
        """
        record = message.record
        level_no = int(record["level"].no)
        if level_no < self._min_level:
            return
        extra = record["extra"]
        # The global patcher replaces raw exceptions with a pre-masked
        # traceback (key "exception") so secrets never reach this sink —
        # and through it, the Supabase mirror. Fall back defensively.
        exc_masked = extra.get("exception")
        exc_type = extra.get("exception_type")
        if exc_masked is None and record["exception"] is not None:
            raw = record["exception"]
            exc_type = raw.type.__name__
            exc_masked = mask_text(str(raw))
        row: dict[str, Any] = {
            "id": new_id(),
            "level": str(record["level"].name),
            "category": str(extra.get("category", "app")),
            "module": str(record["name"] or ""),
            "function": str(record["function"] or ""),
            "message": str(record["message"])[:2000],
            "trace_id": _opt_str(extra.get("trace_id")),
            "signal_id": _opt_str(extra.get("signal_id")),
            "trade_id": _opt_str(extra.get("trade_id")),
            "symbol": _opt_str(extra.get("symbol")),
            "exception_type": exc_type,
            "stack_trace": None if exc_masked is None else str(exc_masked)[:4000],
            "created_at": now_iso(),
        }
        with self._lock:
            self._buffer.append(row)
            overflow = len(self._buffer) > self._max_buffer
        if overflow:
            self.flush()

    # -- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="log-sink", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None
        self.flush()

    # -- flushing ---------------------------------------------------------------
    def flush(self) -> int:
        """Write buffered rows in one batch; returns the row count."""
        with self._lock:
            rows, self._buffer = self._buffer, []
        if not rows:
            return 0
        try:
            return self._repo.bulk_insert(rows)
        except Exception:
            log.opt(exception=True).warning("storage: app_logs flush failed")
            return 0

    def _loop(self) -> None:
        try:
            while not self._stop.wait(self._flush_interval):
                self.flush()
        finally:
            # sqlite connections are thread-bound: close our own on exit
            self._db.close_thread_connection()
