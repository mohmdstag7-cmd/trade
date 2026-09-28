"""Structured, non-blocking logging (SPEC E3.1-E3.4).

Design (ADR-009 in ``docs/ARCHITECTURE.md``):

- One loguru *sink per category* writing ``logs/<category>/<date>.jsonl``
  (structured JSON, one object per line). Sinks are created lazily on the
  first record of a category so an idle category costs nothing.
- One readable ``logs/all.log`` (all categories, human format) plus a
  colored stderr sink for development.
- Every record carries: UTC time, level, category, module, function, line,
  thread, session id, trace id — and, when bound, signal/trade/ticket/
  symbol/strategy ids (see :mod:`app.observability.context` and
  ``logger.bind``).
- All messages and string extras pass through the masking filter first, so
  secrets never reach disk (SPEC C-security).
- Rotation (size), daily files, zip compression, retention and a total size
  cap with an oldest-first prune.
- Categories have runtime-changeable levels; a debug window raises every
  category to DEBUG for N minutes and auto-reverts by expiry.
- A bounded in-memory ring buffer mirrors recent entries for crash reports
  and the Logs page.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.observability.context import get_session_id, get_trace_id, new_session_id, set_session_id
from app.observability.masking import mask_extras, mask_text

if TYPE_CHECKING:
    from collections.abc import Callable

#: Logging categories (SPEC E3.2) — each gets its own JSONL file.
CATEGORIES: tuple[str, ...] = (
    "app",
    "mt5",
    "market_data",
    "analysis",
    "strategy",
    "ml",
    "risk",
    "execution",
    "position",
    "sync",
    "backtest",
    "ui",
    "notify",
    "llm",
    "audit",
    "perf",
)

VALID_LEVELS: tuple[str, ...] = (
    "TRACE",
    "DEBUG",
    "INFO",
    "SUCCESS",
    "WARNING",
    "ERROR",
    "CRITICAL",
)

_LEVEL_NO: dict[str, int] = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 25,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

DEFAULT_LEVEL = "INFO"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_ROTATION_MB = 10
DEFAULT_TOTAL_CAP_MB = 500
SIZE_CHECK_INTERVAL_S = 3600

_READABLE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{extra[category]: <12} | {name}:{function}:{line} | {extra[trace_id]} | {message}"
)

_CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | <cyan>{extra[category]}</cyan> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


@dataclass(slots=True)
class RingEntry:
    """One recent log entry mirrored in memory (oldest first)."""

    ts: str
    level: str
    category: str
    thread: str
    message: str

    def as_line(self) -> str:
        """Readable one-line rendering (used by crash reports and the UI)."""
        return f"{self.ts} | {self.level: <8} | {self.category} | {self.thread} | {self.message}"


class LogRing:
    """Thread-safe bounded buffer of recent log entries."""

    def __init__(self, maxlen: int = 500) -> None:
        self._entries: deque[RingEntry] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def sink(self, message: Any) -> None:
        """Loguru sink: mirror the record into the ring."""
        record = message.record
        entry = RingEntry(
            ts=record["time"].astimezone(UTC).isoformat(timespec="milliseconds"),
            level=record["level"].name,
            category=str(record["extra"].get("category", "app")),
            thread=record["thread"].name,
            message=mask_text(str(record["message"])),
        )
        with self._lock:
            self._entries.append(entry)

    def snapshot(self) -> tuple[RingEntry, ...]:
        """Return the buffered entries, oldest first."""
        with self._lock:
            return tuple(self._entries)

    def lines(self, limit: int = 200) -> list[str]:
        """Return the last ``limit`` entries as readable lines."""
        entries = self.snapshot()
        return [entry.as_line() for entry in entries[-limit:]]


@dataclass(slots=True)
class LoggingState:
    """Runtime state of the logging subsystem."""

    logs_dir: Path
    session_id: str
    ring: LogRing
    levels: dict[str, str] = field(default_factory=dict)
    retention_days: int = DEFAULT_RETENTION_DAYS
    rotation_mb: int = DEFAULT_ROTATION_MB
    total_cap_mb: int = DEFAULT_TOTAL_CAP_MB
    debug_until: float = 0.0
    _category_sink_ids: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop_event: threading.Event = field(default_factory=threading.Event)

    def debug_active(self) -> bool:
        """True while the temporary debug window is open."""
        return time.monotonic() < self.debug_until

    def effective_level(self, category: str) -> int:
        """Numeric level a record of ``category`` must reach to be written."""
        if self.debug_active():
            return _LEVEL_NO["DEBUG"]
        name = self.levels.get(category, DEFAULT_LEVEL)
        return _LEVEL_NO.get(name, _LEVEL_NO[DEFAULT_LEVEL])


_state: LoggingState | None = None


def current_state() -> LoggingState | None:
    """The active logging state, or ``None`` before :func:`init_logging`."""
    return _state


def get_logger(category: str = "app") -> Any:
    """Return the loguru logger bound to ``category``."""
    if category not in CATEGORIES:
        msg = f"unknown logging category: {category!r}"
        raise ValueError(msg)
    return logger.bind(category=category)


# --------------------------------------------------------------------------
# record shaping: context ids + masking (runs for every record)
# --------------------------------------------------------------------------
def _patch_record(record: Any) -> None:
    extra = record["extra"]
    extra.setdefault("category", "app")
    extra["session_id"] = get_session_id() or (_state.session_id if _state else "")
    extra["trace_id"] = get_trace_id()
    record["message"] = mask_text(str(record["message"]))
    exc = record["exception"]
    if exc is not None:
        # Capture a MASKED traceback and drop the raw one. Loguru would
        # otherwise append the raw traceback to every static-format sink
        # (all.log, console) and exception messages routinely embed secrets
        # (URLs, connection strings) — SPEC G4 forbids that.
        extra["exception_type"] = exc.type.__name__
        extra["exception"] = mask_text("".join(traceback.format_exception(*exc)))
        record["exception"] = None
    mask_extras(extra)
    if _state is not None:
        _ensure_category_sink(str(extra["category"]))


def _utc_stamp(record_time: datetime) -> str:
    return record_time.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_line(record: Any) -> str:
    """Render one record as compact structured JSON (SPEC E3.1)."""
    extra = record["extra"]
    payload: dict[str, Any] = {
        "ts": _utc_stamp(record["time"]),
        "level": record["level"].name,
        "category": extra.get("category", "app"),
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
        "thread": record["thread"].name,
        "session_id": extra.get("session_id", ""),
        "trace_id": extra.get("trace_id", ""),
        "message": record["message"],
    }
    for key in ("signal_id", "trade_id", "ticket", "symbol", "strategy"):
        value = extra.get(key)
        if value is not None:
            payload[key] = value
    if extra.get("exception") is not None:  # pre-masked by the patcher
        payload["exception_type"] = extra.get("exception_type", "")
        payload["exception"] = extra["exception"]
    return json.dumps(payload, ensure_ascii=False, default=str)


def _json_format(record: Any) -> str:
    """Loguru format callable: pre-render the JSON line into the record.

    Loguru expects a *format template* to be returned, so the finished JSON
    line is stored in the record and the template simply emits it.
    """
    record["extra"]["_line"] = _json_line(record)
    return "{extra[_line]}\n"


# --------------------------------------------------------------------------
# filters (consulted at emit time, so level changes apply immediately)
# --------------------------------------------------------------------------
def _level_passes(record: Any) -> bool:
    if _state is None:
        return True
    category = str(record["extra"].get("category", "app"))
    level_no: int = record["level"].no
    return level_no >= _state.effective_level(category)


def _all_log_filter(record: Any) -> bool:
    """Filter for all.log / stderr / ring: level check only.

    Loguru passes the record dict itself (not a Message) to filters.
    """
    return _level_passes(record)


def _category_filter(category: str) -> Callable[[Any], bool]:
    def _filter(record: Any) -> bool:
        if record["extra"].get("category") != category:
            return False
        return _level_passes(record)

    return _filter


def _ensure_category_sink(category: str) -> None:
    assert _state is not None
    if category in _state._category_sink_ids:
        return
    with _state._lock:
        if category in _state._category_sink_ids:
            return
        sink_id = logger.add(
            _state.logs_dir / category / "{time:YYYY-MM-DD}.jsonl",
            filter=_category_filter(category),
            format=_json_format,
            level="TRACE",
            rotation="00:00",
            retention=f"{_state.retention_days} days",
            compression="zip",
            enqueue=True,
            encoding="utf-8",
            catch=True,
        )
        _state._category_sink_ids[category] = sink_id


# --------------------------------------------------------------------------
# total size cap (SPEC E3.3)
# --------------------------------------------------------------------------
def enforce_total_size_cap(logs_dir: Path, cap_mb: float) -> int:
    """Delete oldest files under ``logs_dir`` until the folder fits the cap.

    Returns the number of files removed. The live sink (``all.log`` and
    rotated variants) and the dated file for today per category are
    excluded, so a small cap can never delete a file the logger is still
    holding open. Everything else prunes oldest-mtime-first.
    """
    cap_bytes = cap_mb * 1024 * 1024
    files = [path for path in logs_dir.rglob("*") if path.is_file()]
    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    over = total - cap_bytes
    if over <= 0:
        return 0
    removed = 0

    def _mtime(path: Path) -> float:
        # Files can vanish between rglob() and the sort (rotation/zip
        # worker); treat unreadable files as oldest so they prune first.
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    # Exclude active files: all.log (and rotated variants) and the dated
    # file for *today* per category, identified by its filename (e.g.
    # ``2026-09-28.jsonl``). Older-dated files are prunable even if their
    # mtime is recent (a test or a manual touch), because the filename —
    # not the mtime — is what tells the pruner which file the logger is
    # still appending to.
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    candidates: list[Path] = []
    for path in files:
        # all.log is the live readable sink; never delete it
        if path.name == "all.log" or path.name.startswith("all.log."):
            continue
        # today's JSONL files are still being written to
        if path.name == f"{today_str}.jsonl":
            continue
        candidates.append(path)

    for path in sorted(candidates, key=_mtime):
        if over <= 0:
            break
        try:
            size = path.stat().st_size
            path.unlink()
            removed += 1
            over -= size
        except OSError:
            continue
    return removed


def _size_cap_loop(state: LoggingState) -> None:
    while not state._stop_event.wait(SIZE_CHECK_INTERVAL_S):
        enforce_total_size_cap(state.logs_dir, state.total_cap_mb)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def init_logging(
    logs_dir: Path | str | None = None,
    debug: bool = False,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    rotation_mb: int = DEFAULT_ROTATION_MB,
    total_cap_mb: int = DEFAULT_TOTAL_CAP_MB,
) -> LoggingState:
    """(Re)configure the logging subsystem and return its state.

    Safe to call multiple times: previous sinks and the maintenance thread
    are torn down first. ``logs_dir`` defaults to the platform data
    directory; tests pass a temporary path.
    """
    global _state
    if _state is not None:
        shutdown_logging()

    resolved_dir = Path(logs_dir) if logs_dir is not None else _default_logs_dir()
    resolved_dir.mkdir(parents=True, exist_ok=True)

    session = new_session_id()
    set_session_id(session)

    state = LoggingState(
        logs_dir=resolved_dir,
        session_id=session,
        ring=LogRing(),
        retention_days=retention_days,
        rotation_mb=rotation_mb,
        total_cap_mb=total_cap_mb,
    )
    if debug:
        state.debug_until = time.monotonic() + 24 * 3600

    logger.remove()
    logger.configure(
        extra={
            "category": "app",
            "session_id": "",
            "trace_id": "",
            "signal_id": None,
            "trade_id": None,
            "ticket": None,
            "symbol": None,
            "strategy": None,
        },
        patcher=_patch_record,
    )

    logger.add(
        sys.stderr,
        level="DEBUG" if debug else "INFO",
        format=_CONSOLE_FORMAT,
        filter=_all_log_filter,
        enqueue=False,
        backtrace=False,
        diagnose=False,
    )

    logger.add(
        resolved_dir / "all.log",
        level="TRACE",
        format=_READABLE_FORMAT,
        filter=_all_log_filter,
        rotation=f"{rotation_mb} MB",
        retention=f"{retention_days} days",
        compression="zip",
        enqueue=True,
        encoding="utf-8",
        catch=True,
    )

    logger.add(
        state.ring.sink,
        level="TRACE",
        filter=_all_log_filter,
        enqueue=False,
        catch=True,
    )

    cap_thread = threading.Thread(
        target=_size_cap_loop,
        args=(state,),
        name="log-size-cap",
        daemon=True,
    )
    cap_thread.start()

    _state = state
    logger.complete()
    return state


def shutdown_logging() -> None:
    """Flush, stop background work and release sinks (called at exit)."""
    global _state
    if _state is None:
        return
    _state._stop_event.set()
    try:
        logger.complete()
        logger.remove()
    except Exception:  # pragma: no cover - never raise during shutdown
        pass
    _state = None


def set_category_level(category: str, level: str) -> None:
    """Change the minimum level of ``category`` at runtime (SPEC E3.2)."""
    if _state is None:
        msg = "logging is not initialised"
        raise RuntimeError(msg)
    if level not in VALID_LEVELS:
        msg = f"unknown level: {level!r}"
        raise ValueError(msg)
    if category not in CATEGORIES:
        msg = f"unknown logging category: {category!r}"
        raise ValueError(msg)
    _state.levels[category] = level


def category_levels() -> dict[str, str]:
    """Copy of the current per-category level overrides."""
    if _state is None:
        return {}
    return dict(_state.levels)


def enable_debug_mode(minutes: int = 15) -> None:
    """Raise every category to DEBUG for ``minutes``, then auto-revert."""
    if _state is None:
        msg = "logging is not initialised"
        raise RuntimeError(msg)
    _state.debug_until = time.monotonic() + max(0, minutes) * 60


def debug_mode_active() -> bool:
    """Whether the temporary debug window is currently open."""
    return _state.debug_active() if _state is not None else False


def log_startup() -> None:
    """Startup log with versions and identity (SPEC E3.12, pre-MT5 part)."""
    import platform

    from app.__version__ import __version__

    get_logger("app").info(
        "startup: app={} | python={} | os={} | machine={} | session={}",
        __version__,
        platform.python_version(),
        platform.platform(terse=True),
        platform.machine(),
        _state.session_id if _state else "",
    )


def _default_logs_dir() -> Path:
    from app.observability.paths import default_logs_dir

    return default_logs_dir()
