"""Tests for the crash handler (SPEC E3.8): forced exception → crash report."""

from __future__ import annotations

import json
import pathlib
import sys
import threading

import pytest

from app.observability.crash_handler import (
    CrashReporter,
    install_crash_handler,
)
from app.observability.logger import LogRing, get_logger, init_logging, shutdown_logging


@pytest.fixture
def crash_env(tmp_path: pathlib.Path):
    state = init_logging(logs_dir=tmp_path / "logs")
    reports_dir = tmp_path / "crash_reports"
    notifications: list[tuple[str, pathlib.Path]] = []
    reporter = install_crash_handler(
        reports_dir=reports_dir,
        ring=state.ring,
        on_report=lambda kind, path: notifications.append((kind, path)),
    )
    yield reporter, reports_dir, notifications, state
    # restore hooks so later tests are unaffected
    sys.excepthook = reporter._previous_sys_hook
    threading.excepthook = reporter._previous_threading_hook
    shutdown_logging()


class TestSysHook:
    def test_forced_exception_writes_report(self, crash_env) -> None:
        reporter, reports_dir, notifications, state = crash_env

        try:
            raise ValueError("boom: the pipeline exploded")
        except ValueError:
            reporter._sys_hook(*sys.exc_info())

        reports = list(reports_dir.glob("crash_*_sys.json"))
        assert len(reports) == 1
        payload = json.loads(reports[0].read_text(encoding="utf-8"))
        assert payload["kind"] == "sys"
        assert payload["exception_type"] == "ValueError"
        assert "pipeline exploded" in payload["exception_message"]
        assert "Traceback" in payload["stack_trace"] and "ValueError" in payload["stack_trace"]
        assert payload["session_id"] == state.session_id
        assert payload["app_version"]
        assert payload["python_version"]
        assert isinstance(payload["last_logs"], list)
        assert notifications and notifications[0][0] == "sys"

    def test_report_masks_secrets(self, crash_env) -> None:
        reporter, reports_dir, _notifications, _state = crash_env
        try:
            raise RuntimeError("login failed with password=top-secret-pass")
        except RuntimeError:
            reporter._sys_hook(*sys.exc_info())
        payload = json.loads(next(reports_dir.glob("crash_*_sys.json")).read_text("utf-8"))
        assert "top-secret-pass" not in payload["exception_message"]
        assert "[REDACTED]" in payload["exception_message"]
        assert "top-secret-pass" not in payload["stack_trace"]

    def test_last_logs_embedded(self, crash_env) -> None:
        reporter, _reports_dir, _notifications, _state = crash_env
        get_logger("risk").warning("risk engine noticed something")
        get_logger("mt5").error("gateway ping failed")
        try:
            raise RuntimeError("fatal")
        except RuntimeError:
            reporter._sys_hook(*sys.exc_info())
        payload = json.loads(next(crash_env[1].glob("crash_*_sys.json")).read_text("utf-8"))
        joined = "\n".join(payload["last_logs"])
        assert "risk engine noticed something" in joined
        assert "gateway ping failed" in joined


class TestThreadingHook:
    def test_thread_exception_report(self, crash_env) -> None:
        reporter, reports_dir, _notifications, _state = crash_env
        exc = RuntimeError("worker thread died")
        args = threading.ExceptHookArgs(
            (type(exc), exc, exc.__traceback__, threading.current_thread())
        )
        reporter._threading_hook(args)
        reports = list(reports_dir.glob("crash_*_threading.json"))
        assert len(reports) == 1
        payload = json.loads(reports[0].read_text("utf-8"))
        assert payload["kind"] == "threading"
        assert payload["exception_message"] == "worker thread died"
        assert payload["thread_name"] == threading.current_thread().name


class TestReportWriter:
    def test_report_direct_call(self, tmp_path: pathlib.Path) -> None:
        ring = LogRing()
        reporter = CrashReporter(tmp_path, ring=ring)
        path = reporter.report("sys", RuntimeError("direct"), "worker-1")
        payload = json.loads(path.read_text("utf-8"))
        assert payload["kind"] == "sys"
        assert payload["thread_name"] == "worker-1"
        assert payload["last_logs"] == []  # empty ring → empty list, not None

    def test_install_returns_reporter(self, tmp_path: pathlib.Path) -> None:
        reporter = install_crash_handler(tmp_path)
        assert isinstance(reporter, CrashReporter)
        assert sys.excepthook == reporter._sys_hook
        sys.excepthook = reporter._previous_sys_hook
