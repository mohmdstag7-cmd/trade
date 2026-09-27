"""Qt worker threads wrapping the synchronous :class:`UpdateService`.

Three workers cover the update lifecycle:

- :class:`UpdateWorker` — ``check`` (version probe) or ``update``
  (check → download+stage with progress).
- :class:`ElevateWorker` — start the ``--apply-update`` installer
  elevated via PowerShell UAC, reporting whether the user actually
  approved it. Never a silent no-op: a declined UAC prompt must land
  as a visible message, not an app that just quits.
"""

from __future__ import annotations

import subprocess
from typing import Any

from PySide6.QtCore import QThread, Signal

from app.observability.logger import get_logger
from app.updater.service import CheckResult, UpdateService

# "app" (not a dedicated category): update events belong in the main app log
# the user already collects for diagnostics.
log = get_logger("app")


class UpdateWorker(QThread):
    """Run a check or a full download-and-stage pipeline in the background."""

    #: Check finished: (result, error_message)
    check_finished = Signal(object, str)
    #: Download progress: (received_bytes, total_bytes_or_None)
    progress = Signal(int, object)
    #: Staging finished: (ok, message, staging_path_or_None)
    stage_finished = Signal(bool, str, object)

    def __init__(self, service: UpdateService, mode: str, parent: Any = None) -> None:
        super().__init__(parent)
        self._service = service
        self._mode = mode

    def run(self) -> None:
        try:
            result: CheckResult = self._service.check()
        except Exception as exc:  # defensive: never kill the thread
            # Startup checks used to fail totally silently — the user could
            # not tell "no update" from "GitHub unreachable".
            log.warning("updates: check failed: {}", repr(exc))
            self.check_finished.emit(None, repr(exc))
            return
        if result.available and result.plan is not None:
            log.info(
                "updates: {} available (running {})",
                result.plan.new_version,
                self._service.current_version,
            )
        else:
            log.info("updates: up to date ({})", self._service.current_version)
        self.check_finished.emit(result, "")
        if self._mode != "update" or not result.available or result.plan is None:
            return
        try:
            plan = result.plan
            log.info("updates: downloading {} …", plan.new_version)
            staging = self._service.download_and_stage(
                plan,
                progress=lambda done, total: self.progress.emit(done, total),
            )
            log.info("updates: {} staged at {}", plan.new_version, staging)
            self.stage_finished.emit(True, plan.new_version, staging)
        except Exception as exc:
            log.opt(exception=True).error("updates: download/stage failed")
            self.stage_finished.emit(False, str(exc), None)


class ElevateWorker(QThread):
    """Start the ``--apply-update`` installer elevated (UAC), report outcome.

    Runs PowerShell ``Start-Process -Verb RunAs -PassThru`` synchronously
    and reports whether the elevated process was actually CREATED —
    ``Start-Process`` raises when the user declines the UAC prompt, which
    previously meant a silently aborted install.
    """

    #: (started, error_message)
    started = Signal(bool, str)

    def __init__(self, command: list[str], parent: Any = None) -> None:
        super().__init__(parent)
        self._command = command

    @staticmethod
    def _ps_quote(value: str) -> str:
        """Single-quote for PowerShell; embedded quotes doubled."""
        return "'" + value.replace("'", "''") + "'"

    def _build_script(self) -> str:
        exe = self._ps_quote(self._command[0])
        args = ", ".join(self._ps_quote(arg) for arg in self._command[1:])
        return (
            "try { "
            f"$p = Start-Process -FilePath {exe} -ArgumentList {args} "
            "-Verb RunAs -PassThru -ErrorAction Stop; "
            "if ($p) { exit 0 } else { exit 1 } "
            "} catch { exit 1 }"
        )

    def run(self) -> None:
        script = self._build_script()
        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("updates: elevation spawn failed: {}", repr(exc))
            self.started.emit(False, str(exc))
            return
        ok = completed.returncode == 0
        if not ok:
            log.warning(
                "updates: elevation declined or failed (exit {})",
                completed.returncode,
            )
        self.started.emit(ok, "" if ok else f"exit {completed.returncode}")
