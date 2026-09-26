"""Tests for the watchdog (SPEC E3.9): heartbeats and freeze detection."""

from __future__ import annotations

import threading
import time

from app.observability.watchdog import Watchdog


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestFreezeDetection:
    def test_no_freeze_within_timeout(self) -> None:
        clock = FakeClock()
        notified: list[str] = []
        wd = Watchdog(check_interval_s=1.0, notify=notified.append, time_fn=clock)
        wd.register("gateway", timeout_s=5.0)
        clock.advance(3)
        assert wd.check_once() == ()
        assert notified == []

    def test_freeze_detected_after_timeout(self) -> None:
        clock = FakeClock()
        notified: list[str] = []
        wd = Watchdog(check_interval_s=1.0, notify=notified.append, time_fn=clock)
        wd.register("gateway", timeout_s=5.0)
        clock.advance(5.5)
        frozen = wd.check_once()
        assert frozen == ("gateway",)
        assert notified == ["gateway"]

    def test_heartbeat_prevents_freeze(self) -> None:
        clock = FakeClock()
        wd = Watchdog(check_interval_s=1.0, time_fn=clock)
        wd.register("engine", timeout_s=5.0)
        for _ in range(10):
            clock.advance(1)
            wd.heartbeat("engine")
        assert wd.check_once() == ()

    def test_recovery_after_heartbeat(self) -> None:
        clock = FakeClock()
        wd = Watchdog(check_interval_s=1.0, time_fn=clock)
        wd.register("engine", timeout_s=2.0)
        clock.advance(3)
        assert wd.check_once() == ("engine",)
        assert wd.check_once() == ()  # still frozen, not re-reported
        clock.advance(1)
        wd.heartbeat("engine")  # recovers
        clock.advance(0.5)
        assert wd.check_once() == ()
        clock.advance(3)
        assert wd.check_once() == ("engine",)  # can freeze again later

    def test_restart_callback_invoked(self) -> None:
        clock = FakeClock()
        restarted: list[str] = []
        wd = Watchdog(check_interval_s=1.0, time_fn=clock)
        wd.register("gateway", timeout_s=1.0, restart=restarted.append)
        clock.advance(2)
        assert wd.check_once() == ("gateway",)
        assert restarted == ["gateway"]

    def test_restart_failure_does_not_raise(self) -> None:
        clock = FakeClock()

        def bad_restart(_name: str) -> None:
            raise RuntimeError("restart exploded")

        wd = Watchdog(check_interval_s=1.0, time_fn=clock)
        wd.register("gateway", timeout_s=1.0, restart=bad_restart)
        clock.advance(2)
        assert wd.check_once() == ("gateway",)  # error swallowed + logged


class TestRegistration:
    def test_unregister_stops_checks(self) -> None:
        clock = FakeClock()
        wd = Watchdog(check_interval_s=1.0, time_fn=clock)
        wd.register("x", timeout_s=1.0)
        wd.unregister("x")
        clock.advance(10)
        assert wd.check_once() == ()

    def test_heartbeat_unknown_name_ignored(self) -> None:
        clock = FakeClock()
        wd = Watchdog(time_fn=clock)
        wd.heartbeat("ghost")  # must not raise

    def test_start_stop_thread(self) -> None:
        clock = FakeClock()
        wd = Watchdog(check_interval_s=0.05, time_fn=clock)
        wd.register("x", timeout_s=100.0)
        wd.start()
        time.sleep(0.12)
        wd.stop()
        wd.stop()  # idempotent
        assert not wd._thread or not wd._thread.is_alive()

    def test_monitor_thread_reports_freeze(self) -> None:
        """The real monitor thread (not just check_once) reports freezes."""
        clock = FakeClock()
        notified: list[str] = []
        started = threading.Event()

        # simulate the worker's clock advancing on another timeline: the
        # monitor uses time_fn each pass, so we advance it from a timer.
        wd = Watchdog(check_interval_s=0.05, notify=notified.append, time_fn=clock)
        wd.register("slow-worker", timeout_s=1.0)

        def advance_later() -> None:
            started.wait(1)
            clock.advance(5)

        t = threading.Thread(target=advance_later)
        t.start()
        wd.start()
        started.set()
        deadline = time.monotonic() + 3
        while not notified and time.monotonic() < deadline:
            time.sleep(0.02)
        wd.stop()
        t.join()
        assert notified == ["slow-worker"]
