"""Qt worker thread wrapping the synchronous :class:`UpdateService`.

One worker instance runs one pipeline:

- ``mode="check"`` — version check only (startup + button).
- ``mode="update"`` — check → download+stage (with progress) → ready.

All results travel through signals; the UI thread only reacts.
"""

from __future__ import annotations

import traceback
from typing import Any

from PySide6.QtCore import QThread, Signal

from app.updater.service import CheckResult, UpdateService


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
            self.check_finished.emit(None, repr(exc))
            return
        self.check_finished.emit(result, "")
        if self._mode != "update" or not result.available or result.plan is None:
            return
        try:
            plan = result.plan
            staging = self._service.download_and_stage(
                plan,
                progress=lambda done, total: self.progress.emit(done, total),
            )
            self.stage_finished.emit(True, plan.new_version, staging)
        except Exception as exc:
            traceback.print_exc()
            self.stage_finished.emit(False, str(exc), None)
