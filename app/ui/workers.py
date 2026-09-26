"""Background workers for Settings-page actions.

``ConnectWorker`` drives the *shared* gateway's persistent connect /
disconnect off the UI thread. The gateway itself never blocks: its
``connect()`` returns a ``Future``; here we block the worker thread on
``wait_for_result`` and only the verdict travels back through a signal.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal


class ConnectWorker(QThread):
    """Connect (or disconnect) the shared gateway in the background."""

    #: (ok, detail) — detail is a human-readable server/mode or error string.
    result_ready = Signal(bool, str)

    def __init__(
        self,
        gateway: Any,
        request: Any,
        parent: Any = None,
        *,
        mode: str = "connect",
        timeout_s: float = 90.0,
    ) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self._request = request
        self._mode = mode
        self._timeout_s = timeout_s

    def run(self) -> None:
        try:
            if self._mode == "connect":
                future = self._gateway.connect(self._request)
                account = self._gateway.wait_for_result(future, "connect", self._timeout_s)
                detail = f"{account.server} · {account.mode_name}"
                self.result_ready.emit(True, detail)
            else:
                future = self._gateway.disconnect()
                self._gateway.wait_for_result(future, "disconnect", 15.0)
                self.result_ready.emit(True, "")
        except Exception as exc:  # MT5Error, timeouts, shutdown races
            self.result_ready.emit(False, str(exc))
