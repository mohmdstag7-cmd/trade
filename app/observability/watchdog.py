"""Watchdog: worker heartbeats and freeze detection (SPEC E3.9).

Every long-running component (later: the MT5 gateway thread, the engine,
sync workers) registers a heartbeat. The monitor thread checks each
registration once per interval; when a worker has not beat within its
timeout, the watchdog:

1. logs a CRITICAL entry,
2. calls ``notify`` (UI toast / Telegram in later phases),
3. calls the worker's ``restart`` callable, if one was provided.

A heartbeat clears the frozen flag and logs recovery. The wall clock is
injected (``time_fn``) so tests can simulate freezes deterministically.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.observability.logger import get_logger

log = get_logger("app")

NotifyCallback = Callable[[str], None]
RestartCallback = Callable[[str], None]


@dataclass(slots=True)
class _Registration:
    name: str
    timeout_s: float
    restart: RestartCallback | None
    last_beat: float
    frozen: bool = False


@dataclass(slots=True)
class WatchdogConfig:
    """Tuning knobs for the monitor loop."""

    check_interval_s: float = 1.0
    notify: NotifyCallback | None = None


class Watchdog:
    """Monitors registered heartbeats and detects freezes."""

    def __init__(
        self,
        check_interval_s: float = 1.0,
        notify: NotifyCallback | None = None,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = WatchdogConfig(check_interval_s=check_interval_s, notify=notify)
        self._time_fn = time_fn
        self._registrations: dict[str, _Registration] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Start the monitor thread (idempotent while already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the monitor thread and clear registrations."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._config.check_interval_s * 2)
            self._thread = None

    # -- registration ------------------------------------------------------------
    def register(
        self,
        name: str,
        timeout_s: float,
        restart: RestartCallback | None = None,
    ) -> None:
        """Register a worker with its heartbeat ``timeout_s``."""
        with self._lock:
            self._registrations[name] = _Registration(
                name=name,
                timeout_s=timeout_s,
                restart=restart,
                last_beat=self._time_fn(),
            )
        log.debug("watchdog: registered {!r} (timeout={}s)", name, timeout_s)

    def unregister(self, name: str) -> None:
        """Remove a registration (worker shut down cleanly)."""
        with self._lock:
            self._registrations.pop(name, None)

    def heartbeat(self, name: str) -> None:
        """Record a beat from ``name``; clears a frozen state (recovery)."""
        with self._lock:
            reg = self._registrations.get(name)
            if reg is None:
                return
            reg.last_beat = self._time_fn()
            if reg.frozen:
                reg.frozen = False
                log.warning("watchdog: {!r} recovered", name)

    # -- checking ---------------------------------------------------------------
    def check_once(self) -> tuple[str, ...]:
        """Run one check pass; return the names that froze in this pass."""
        now = self._time_fn()
        newly_frozen: list[str] = []
        with self._lock:
            for reg in self._registrations.values():
                elapsed = now - reg.last_beat
                if reg.frozen:
                    continue
                if elapsed > reg.timeout_s:
                    reg.frozen = True
                    newly_frozen.append(reg.name)
                    log.critical(
                        "watchdog: {!r} frozen for {:.1f}s (timeout={}s)",
                        reg.name,
                        elapsed,
                        reg.timeout_s,
                    )
                    restart = reg.restart
                    notify = self._config.notify
                    if notify is not None:
                        notify(reg.name)
                    if restart is not None:
                        try:
                            restart(reg.name)
                        except Exception:
                            log.opt(exception=True).critical(
                                "watchdog: restart of {!r} failed", reg.name
                            )
        return tuple(newly_frozen)

    # -- internals --------------------------------------------------------------
    def _loop(self) -> None:
        interval = self._config.check_interval_s
        while not self._stop_event.wait(interval):
            self.check_once()
