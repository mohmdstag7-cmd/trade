"""Correlation context: session id and trace ids (SPEC E3.1, E3.5).

- A *session id* identifies one run of the application. It is created once
  at startup and stamped on every log record.
- A *trace id* follows one logical unit of work (later: a signal from bar
  evaluation to close and to sync). Trace ids are stored in ``ContextVar``s
  so each thread / async task carries its own value, and they propagate
  into every log record via the logging patcher.

The helpers here are intentionally framework-free: engine code, brokers and
tests can use them without importing Qt or loguru.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_session_id: ContextVar[str] = ContextVar("mt5w_session_id", default="")
_trace_id: ContextVar[str] = ContextVar("mt5w_trace_id", default="")


def new_session_id() -> str:
    """Return a fresh session identifier (``s-<hex>``)."""
    return f"s-{uuid.uuid4().hex[:12]}"


def new_trace_id() -> str:
    """Return a fresh trace identifier (``t-<hex>``)."""
    return f"t-{uuid.uuid4().hex[:12]}"


def get_session_id() -> str:
    """Session id visible to the current context (may be empty)."""
    return _session_id.get()


def get_trace_id() -> str:
    """Trace id visible to the current context (may be empty)."""
    return _trace_id.get()


def set_session_id(value: str) -> Token[str]:
    """Set the session id for the current context (called once at startup)."""
    return _session_id.set(value)


@contextmanager
def trace(trace_id: str | None = None) -> Iterator[str]:
    """Bind the enclosed block to a trace id.

    ``trace()`` generates a new id; ``trace(existing)`` joins an existing
    trace (e.g. a strategy calling into risk and execution). Nesting is
    supported: the previous value is restored on exit.
    """
    tid = trace_id or new_trace_id()
    token = _trace_id.set(tid)
    try:
        yield tid
    finally:
        _trace_id.reset(token)
