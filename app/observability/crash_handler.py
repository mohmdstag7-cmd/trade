"""Crash handler (SPEC E3.8).

Installs three hooks so an unexpected failure is always captured:

- ``sys.excepthook`` — uncaught exceptions on the main thread.
- ``threading.excepthook`` — uncaught exceptions in worker threads.
- the Qt message handler — Qt's own warnings/fatals, routed into loguru.

Every crash writes ``crash_reports/crash_<UTC timestamp>_<kind>.json``
containing: stack trace, the last 200 log lines (masked), versions,
platform, session/trace ids and the thread name. A friendly callback is
invoked afterwards (the GUI shows a dialog; the CLI prints a message).

Secrets in exception messages and stacks pass through the same masking
filter as regular logs, so a report can be shared safely.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from app.__version__ import __version__
from app.observability.context import get_session_id, get_trace_id
from app.observability.logger import LogRing, current_state, get_logger
from app.observability.masking import mask_text

#: Number of recent log lines embedded in every crash report (SPEC E3.8).
LAST_LOG_LINES = 200

ReportCallback = Callable[[str, Path], None]


class CrashReporter:
    """Writes crash reports and notifies a callback. Owns the hooks."""

    def __init__(
        self,
        reports_dir: Path,
        ring: LogRing | None = None,
        on_report: ReportCallback | None = None,
    ) -> None:
        self._reports_dir = Path(reports_dir)
        self._ring = ring
        self._on_report = on_report
        self._previous_sys_hook = sys.excepthook
        self._previous_threading_hook = threading.excepthook
        self._previous_qt_handler: object | None = None

    # -- installation ------------------------------------------------------
    def install(self) -> None:
        """Activate the hooks (idempotent for this reporter instance)."""
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        sys.excepthook = self._sys_hook
        threading.excepthook = self._threading_hook
        self._install_qt_handler()

    # -- hooks ---------------------------------------------------------------
    def _sys_hook(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        path = self.report("sys", exc_value, threading.current_thread().name)
        log = get_logger("app")
        log.opt(exception=(exc_type, exc_value, exc_tb)).critical(
            "uncaught exception on main thread; report={}", path.name
        )
        if self._on_report is not None:
            self._on_report("sys", path)

    def _threading_hook(self, args: threading.ExceptHookArgs) -> None:
        exc = args.exc_value
        if exc is None:  # pragma: no cover - defensive
            return
        thread_name = args.thread.name if args.thread is not None else "unknown"
        path = self.report("threading", exc, thread_name)
        get_logger("app").critical(
            "uncaught exception in thread {!r}; report={}", thread_name, path.name
        )
        if self._on_report is not None:
            self._on_report("threading", path)

    # -- Qt --------------------------------------------------------------------
    def _install_qt_handler(self) -> None:
        """Route Qt messages into loguru; fatals also produce a report.

        Installed only when PySide6 is importable; the observability layer
        itself stays Qt-free (tests on a plain interpreter still work).
        """
        try:
            from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        except ImportError:  # pragma: no cover - CI runs with PySide6 present
            return

        reporter = self

        def _qt_handler(msg_type: int, context: object, message: bytes | str) -> None:
            level_map = {
                QtMsgType.QtDebugMsg: "DEBUG",
                QtMsgType.QtInfoMsg: "INFO",
                QtMsgType.QtWarningMsg: "WARNING",
                QtMsgType.QtCriticalMsg: "ERROR",
                QtMsgType.QtFatalMsg: "CRITICAL",
            }
            level = level_map.get(QtMsgType(msg_type), "ERROR")
            text = message.decode(errors="replace") if isinstance(message, bytes) else message
            get_logger("ui").opt(raw=False).log(level, "qt: {}", mask_text(text))
            if msg_type == QtMsgType.QtFatalMsg:
                reporter.report("qt", RuntimeError(text), threading.current_thread().name)

        self._previous_qt_handler = qInstallMessageHandler(_qt_handler)

    # -- report writing ------------------------------------------------------------
    def report(self, kind: str, exc: BaseException, thread_name: str) -> Path:
        """Write one crash report JSON file and return its path."""
        payload = self._build_payload(kind, exc, thread_name)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")[:-3]
        path = self._reports_dir / f"crash_{stamp}_{kind}.json"
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:  # pragma: no cover - disk full / permissions
            fallback = self._reports_dir / f"crash_{stamp}_{kind}.txt"
            fallback.write_text(str(payload.get("stack_trace", "")), encoding="utf-8")
            path = fallback
        return path

    def _build_payload(self, kind: str, exc: BaseException, thread_name: str) -> dict[str, object]:
        stack = mask_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        payload: dict[str, object] = {
            "kind": kind,
            "time_utc": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "thread_name": thread_name,
            "exception_type": type(exc).__name__,
            "exception_message": mask_text(str(exc)),
            "stack_trace": stack,
            "app_version": __version__,
            "python_version": platform.python_version(),
            "platform": platform.platform(terse=True),
            "machine": platform.machine(),
            "pid": os.getpid(),
            "session_id": get_session_id(),
            "trace_id": get_trace_id(),
            "argv": list(sys.argv),
            "last_logs": self._last_logs(),
        }
        return payload

    def _last_logs(self) -> list[str]:
        ring: LogRing | None = self._ring
        if ring is None:
            state = current_state()
            ring = state.ring if state is not None else None
        if ring is None:
            return []
        return ring.lines(limit=LAST_LOG_LINES)


def show_crash_dialog(report_path: Path) -> None:
    """Show a friendly crash notification (GUI dialog when Qt is running)."""
    summary = (
        "An unexpected error occurred. The application kept the details in a "
        f"crash report:\n{report_path.name}\n\n"
        "You can continue working. If this keeps happening, send the file from "
        "the crash_reports folder with your next bug report."
    )
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        print(summary)
        return
    if QApplication.instance() is None:
        print(summary)
        return
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle("MT5 Trading Workstation")
    box.setText("Something went wrong")
    box.setInformativeText(summary)
    box.exec()


def install_crash_handler(
    reports_dir: Path,
    ring: LogRing | None = None,
    on_report: ReportCallback | None = None,
) -> CrashReporter:
    """Install all crash hooks and return the active :class:`CrashReporter`."""
    reporter = CrashReporter(reports_dir, ring=ring, on_report=on_report)
    reporter.install()
    return reporter
