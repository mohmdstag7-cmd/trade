"""One-shot Supabase reachability probe for the Settings page.

Same contract as the MT5 :class:`~app.mt5.diagnostics.ConnectionProbe`:
``start()`` once, then ``poll()`` from a QTimer until ``finished`` — the
blocking HTTP round-trip happens on a worker thread, so the UI never
freezes (SPEC C3, I-8).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from app.observability.logger import get_logger
from app.storage.mirror import MirrorError, SupabaseMirror

log = get_logger("sync")


@dataclass(slots=True)
class CloudProbeState:
    """Immutable poll result."""

    finished: bool
    ok: bool = False
    detail: str = ""


class CloudProbe:
    """Probe a (url, key) pair against the ``health_checks`` table."""

    def __init__(self, url: str, key: str, *, timeout_s: float = 15.0) -> None:
        self._url = url
        self._key = key
        self._timeout_s = timeout_s
        self._thread: threading.Thread | None = None
        self._done = threading.Event()
        self._ok = False
        self._detail = ""

    # -- public ----------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="cloud-probe", daemon=True)
        self._thread.start()

    def poll(self) -> CloudProbeState:
        """Non-blocking status read (safe from the UI thread)."""
        if not self._done.is_set():
            return CloudProbeState(finished=False)
        return CloudProbeState(finished=True, ok=self._ok, detail=self._detail)

    def wait(self, timeout_s: float | None = None) -> CloudProbeState:
        """Blocking variant for CLI/tests."""
        self._done.wait(timeout_s if timeout_s is not None else self._timeout_s)
        return self.poll()

    # -- internals -----------------------------------------------------------------
    def _run(self) -> None:
        try:
            latency_ms = SupabaseMirror(self._url, self._key).probe()
            self._ok = True
            self._detail = f"{latency_ms:.0f} ms"
            log.info("storage: cloud probe OK ({})", self._detail)
        except MirrorError as exc:
            self._ok = False
            self._detail = str(exc)
            log.warning("storage: cloud probe failed: {}", self._detail)
        except Exception as exc:  # pragma: no cover - defensive
            self._ok = False
            self._detail = f"unexpected error: {exc}"
            log.opt(exception=True).warning("storage: cloud probe crashed")
        finally:
            self._done.set()
