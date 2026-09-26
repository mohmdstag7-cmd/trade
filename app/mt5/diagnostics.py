"""Connection diagnostics (SPEC C13, G3-3).

Two consumers share this module:

- ``--mt5-smoke-test`` (CLI): a full, read-only health report of the
  terminal on the user's PC, printed and written as JSON.
- the Settings page "Test connection" card (UI): :class:`ConnectionProbe`
  drives the same checks through a one-shot gateway while the UI polls with
  a QTimer — the UI thread never blocks.

Every step captures its own error; the report is JSON-serializable and
mask-safe (login tail only, no password).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.mt5.errors import MT5Error, friendly_message
from app.mt5.gateway import MT5Gateway
from app.mt5.models import ConnectionState, ConnectRequest, mask_login
from app.mt5.symbols import DEFAULT_WATCHLIST, SymbolResolver
from app.observability.logger import get_logger

log = get_logger("mt5")

_PING_SAMPLES = 3


@dataclass(slots=True)
class SmokeStep:
    """One diagnostic step's outcome."""

    name: str
    ok: bool
    detail: str
    duration_ms: float = 0.0
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "skipped": self.skipped,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 1),
        }


@dataclass(slots=True)
class SmokeTestReport:
    """Ordered steps plus the overall verdict."""

    steps: list[SmokeStep] = field(default_factory=list)
    total_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(step.ok or step.skipped for step in self.steps)

    @property
    def failure(self) -> str:
        """First failing step name + detail ('' when healthy)."""
        for step in self.steps:
            if not step.ok and not step.skipped:
                return f"{step.name}: {step.detail}"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failure": self.failure,
            "total_ms": round(self.total_ms, 1),
            "steps": [step.to_dict() for step in self.steps],
        }

    def summary_lines(self, *, lang: str = "en") -> list[str]:
        """Human-readable checklist for the console / dialog."""
        lines = [f"[{_mark(step)}] {step.name}: {step.detail}" for step in self.steps]
        if self.ok:
            lines.append("SMOKE TEST PASSED" if lang == "en" else "تست اتصال با موفقیت انجام شد")
        else:
            failure = friendly_message_for(self.failure) if self.failure else "unknown"
            lines.append(
                f"SMOKE TEST FAILED — {failure}"
                if lang == "en"
                else f"تست اتصال ناموفق بود — {failure}"
            )
        return lines


def friendly_message_for(failure: str) -> str:
    """Best-effort friendly wrapper for a ``'step: detail'`` failure."""
    return failure


def _mark(step: SmokeStep) -> str:
    if step.ok:
        return "OK  "
    return "SKIP" if step.skipped else "FAIL"


def run_smoke_test(
    mt5_factory: Any,
    request: ConnectRequest,
    *,
    symbols: tuple[str, ...] = DEFAULT_WATCHLIST,
    timeframe: str = "M15",
    bar_count: int = 50,
    timeout_s: float = 20.0,
) -> SmokeTestReport:
    """Run the full read-only diagnostic suite. Blocking — CLI only."""
    started = time.monotonic()
    report = SmokeTestReport()
    gateway = MT5Gateway(mt5_factory=mt5_factory, request_timeout_s=timeout_s, name="smoke")
    gateway.start()

    def step(name: str) -> _StepRecorder:
        return _StepRecorder(report, name)

    def broken() -> bool:
        """True once any step failed — remaining steps are skipped."""
        return any(not s.ok and not s.skipped for s in report.steps)

    # 1. package import -------------------------------------------------------
    module: Any = None
    with step("package") as rec:
        module = mt5_factory()
        version = getattr(module, "__version__", "unknown")
        rec.ok(f"MetaTrader5 {version}")

    # 2. connect ----------------------------------------------------------------
    account: Any = None
    if broken():
        _skip_rest(report, "connect")
    else:
        with step("connect") as rec:
            account = gateway.wait_for_result(gateway.connect(request), "connect", timeout_s)
            rec.ok(
                f"logged in as {mask_login(account.login)} ({account.mode_name}) "
                f"on {account.server}"
            )

    # 3. account -----------------------------------------------------------------
    if broken():
        _skip_rest(report, "account")
    else:
        with step("account") as rec:
            rec.ok(
                f"balance {account.balance:.2f} {account.currency}, equity "
                f"{account.equity:.2f}, leverage 1:{account.leverage}"
            )

    # 4. terminal -------------------------------------------------------------------
    terminal: Any = None
    if broken():
        _skip_rest(report, "terminal")
    else:
        with step("terminal") as rec:
            terminal = gateway.wait_for_result(gateway.terminal_info(), "terminal_info", timeout_s)
            rec.ok(f"{terminal.name} build {terminal.build}, connected={terminal.connected}")

    # 5. symbol resolution ---------------------------------------------------------
    resolver = SymbolResolver()
    resolved: dict[str, Any] = {}
    if broken():
        _skip_rest(report, "symbols")
    else:
        with step("symbols") as rec:
            names = gateway.wait_for_result(gateway.symbol_names(), "symbol_names", timeout_s)
            resolved = resolver.resolve_all(symbols, names)
            if resolved:
                pairs = ", ".join(f"{c}->{r.broker_symbol}" for c, r in resolved.items())
                missing = [c for c in symbols if c not in resolved]
                extra = f" (missing: {', '.join(missing)})" if missing else ""
                rec.ok(f"{pairs}{extra}")
            else:
                rec.fail(f"none of {', '.join(symbols)} could be resolved")

    # 6. quotes / bars -------------------------------------------------------------
    if broken():
        _skip_rest(report, "quotes")
    else:
        with step("quotes") as rec:
            if resolved:
                first = next(iter(resolved.values())).broker_symbol
                gateway.wait_for_result(gateway.select_symbol(first), "select_symbol", timeout_s)
                bars = gateway.wait_for_result(
                    gateway.rates_from_pos(first, timeframe, 0, bar_count),
                    "rates_from_pos",
                    timeout_s,
                )
                if bars:
                    last_age_min = (time.time() - bars[-1].time) / 60.0
                    rec.ok(
                        f"{len(bars)} {timeframe} bars on {first}; newest bar "
                        f"{last_age_min:.1f} min old"
                    )
                else:
                    rec.fail(f"no {timeframe} bars returned for {first}")
            else:
                rec.skip("no resolved symbol to query")

    # 7. latency ----------------------------------------------------------------------
    if broken():
        _skip_rest(report, "latency")
    else:
        with step("latency") as rec:
            samples = [
                float(gateway.wait_for_result(gateway.ping(), "ping", timeout_s))
                for _ in range(_PING_SAMPLES)
            ]
            avg = sum(samples) / len(samples)
            rec.ok(f"terminal round-trip {avg:.1f} ms (avg of {_PING_SAMPLES})")

    # 8. disconnect -----------------------------------------------------------------------
    if broken():
        _skip_rest(report, "disconnect")
    else:
        with step("disconnect") as rec:
            gateway.wait_for_result(gateway.disconnect(), "disconnect", timeout_s)
            rec.ok("terminal session closed cleanly")

    gateway.stop()
    report.total_ms = (time.monotonic() - started) * 1000.0
    log.info("diagnostics: smoke test finished ok={} ({:.0f} ms)", report.ok, report.total_ms)
    return report


def _skip_rest(report: SmokeTestReport, current: str) -> None:
    """Append a SKIP marker for ``current`` after an earlier failure."""
    report.steps.append(
        SmokeStep(current, ok=True, detail="skipped — earlier step failed", skipped=True)
    )


class _StepRecorder:
    """Context manager appending one :class:`SmokeStep` to the report."""

    def __init__(self, report: SmokeTestReport, name: str) -> None:
        self._report = report
        self._name = name
        self._start = 0.0

    def __enter__(self) -> _StepRecorder:
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        duration = (time.monotonic() - self._start) * 1000.0
        if exc_type is None:
            return True  # recorder methods already appended the step
        if isinstance(exc, MT5Error):
            detail = friendly_message(exc.code, str(exc))
        else:
            detail = f"{exc_type.__name__}: {exc}"
        self._report.steps.append(
            SmokeStep(self._name, ok=False, detail=detail, duration_ms=duration)
        )
        return True  # swallow: the report carries the failure

    def ok(self, detail: str) -> None:
        self._report.steps.append(
            SmokeStep(self._name, ok=True, detail=detail, duration_ms=self._elapsed())
        )

    def fail(self, detail: str) -> None:
        self._report.steps.append(
            SmokeStep(self._name, ok=False, detail=detail, duration_ms=self._elapsed())
        )

    def skip(self, detail: str) -> None:
        self._report.steps.append(
            SmokeStep(self._name, ok=True, detail=detail, duration_ms=0.0, skipped=True)
        )

    def _elapsed(self) -> float:
        return (time.monotonic() - self._start) * 1000.0


# --------------------------------------------------------------------------- #
# UI probe: step machine polled from a QTimer                                  #
# --------------------------------------------------------------------------- #
PROBE_STEPS: tuple[str, ...] = (
    "connect",
    "account",
    "terminal",
    "symbols",
    "latency",
    "disconnect",
)


@dataclass(slots=True)
class ProbeState:
    """Immutable snapshot of the probe's progress for the UI."""

    step: str
    index: int
    total: int
    finished: bool
    ok: bool
    detail: str

    @property
    def progress(self) -> float:
        return (self.index + 1) / self.total if self.total else 0.0


class ConnectionProbe:
    """One-shot connect→info→disconnect sequence.

    Two modes:

    - *owned* (default): a private one-shot gateway is created, driven
      through the full cycle and stopped — same as the original Phase-3
      probe.
    - *borrowed* (``shared_gateway=…``): the app shell's persistent
      gateway is inspected instead. The MetaTrader5 module is a
      process-global singleton — a second ``initialize()``/``shutdown()``
      from a private probe gateway silently kills the shared session
      underneath it (the UI keeps saying "connected" while every call
      fails). So a borrowed probe NEVER re-connects a live session
      (read-only steps) and NEVER stops the shared gateway; when the
      session is idle it runs the full cycle and the trailing disconnect
      restores the pre-probe state.
    """

    def __init__(
        self,
        request: ConnectRequest,
        mt5_factory: Any = None,
        *,
        symbols: tuple[str, ...] = DEFAULT_WATCHLIST,
        timeout_s: float = 15.0,
        shared_gateway: MT5Gateway | None = None,
    ) -> None:
        self._request = request
        self._mt5_factory = mt5_factory
        self._symbols = symbols
        self._timeout_s = timeout_s
        self._shared = shared_gateway
        self._own_gateway = shared_gateway is None
        self._steps: tuple[str, ...] = PROBE_STEPS
        self._gateway: MT5Gateway | None = None
        self._index = 0
        self._finished = False
        self._ok = True
        self._detail_parts: dict[str, str] = {}
        self._future: Any = None
        self._account: Any = None

    # -- public -----------------------------------------------------------------
    def start(self) -> None:
        """Bind the gateway and submit the first step."""
        if self._own_gateway:
            self._gateway = MT5Gateway(
                mt5_factory=self._mt5_factory,
                request_timeout_s=self._timeout_s,
                name="probe",
            )
            self._gateway.start()
        else:
            assert self._shared is not None  # own_gateway False implies shared set
            self._gateway = self._shared
            if self._shared.state is ConnectionState.CONNECTED:
                # Live persistent session — inspect read-only, touch nothing.
                self._steps = tuple(
                    step for step in PROBE_STEPS if step not in ("connect", "disconnect")
                )
        self._advance()

    def poll(self) -> ProbeState:
        """Advance the step machine; safe to call from the UI thread."""
        if self._finished or self._gateway is None:
            return self._state()
        step = self._steps[self._index]
        if self._future is not None and not self._future.done():
            return self._state(step)
        if self._future is not None:
            try:
                self._collect(step, self._future.result(timeout=0))
            except Exception as exc:
                return self._finish(ok=False, detail=_friendly(exc))
        if self._index >= len(self._steps) - 1:
            return self._finish(ok=self._ok, detail=self._summary())
        self._index += 1
        self._advance()
        return self._state(self._steps[self._index])

    def cancel(self) -> None:
        """Abort mid-flight (user closed the dialog)."""
        if self._gateway is not None and self._own_gateway:
            self._gateway.stop()
        self._gateway = None
        self._finished = True
        self._ok = False
        self._detail_parts = {"cancelled": "yes"}

    # -- internals ------------------------------------------------------------------
    def _advance(self) -> None:
        if self._gateway is None:
            return
        step = self._steps[self._index]
        if step == "connect":
            self._future = self._gateway.connect(self._request)
        elif step == "account":
            self._future = self._gateway.account_info()
        elif step == "terminal":
            self._future = self._gateway.terminal_info()
        elif step == "symbols":
            self._future = self._gateway.symbol_names()
        elif step == "latency":
            self._future = self._gateway.ping()
        elif step == "disconnect":
            self._future = self._gateway.disconnect()

    def _collect(self, step: str, result: Any) -> None:
        if step == "connect":
            self._account = result
            self._detail_parts["connect"] = (
                f"{mask_login(result.login)} ({result.mode_name}) on {result.server}"
            )
        elif step == "account":
            self._detail_parts["account"] = (
                f"balance {result.balance:.2f} {result.currency}, equity {result.equity:.2f}"
            )
        elif step == "terminal":
            self._detail_parts["terminal"] = f"{result.name} build {result.build}"
        elif step == "symbols":
            resolver = SymbolResolver()
            resolved = resolver.resolve_all(self._symbols, tuple(result))
            found = ", ".join(f"{c}->{r.broker_symbol}" for c, r in resolved.items())
            self._detail_parts["symbols"] = found if found else "no watchlist symbol found"
            if not found:
                self._ok = False
        elif step == "latency":
            self._detail_parts["latency"] = f"round-trip {result:.1f} ms"
        elif step == "disconnect":
            self._detail_parts["disconnect"] = "session closed"

    def _summary(self) -> str:
        return "; ".join(
            self._detail_parts.get(step, "") for step in self._steps if self._detail_parts.get(step)
        )

    def _finish(self, *, ok: bool, detail: str) -> ProbeState:
        if self._gateway is not None and self._own_gateway:
            self._gateway.stop()
        self._gateway = None
        self._finished = True
        self._ok = ok
        self._detail_parts = {"result": detail}
        return ProbeState(
            step=self._steps[-1],
            index=len(self._steps) - 1,
            total=len(self._steps),
            finished=True,
            ok=ok,
            detail=detail,
        )

    def _state(self, step: str | None = None) -> ProbeState:
        if self._finished:
            return ProbeState(
                step=self._steps[-1],
                index=len(self._steps) - 1,
                total=len(self._steps),
                finished=True,
                ok=self._ok,
                detail=self._summary(),
            )
        step = step or self._steps[max(self._index, 0)]
        return ProbeState(
            step=step,
            index=max(self._index, 0),
            total=len(self._steps),
            finished=False,
            ok=True,
            detail=self._detail_parts.get(step, ""),
        )


def _friendly(exc: Exception) -> str:
    if isinstance(exc, MT5Error):
        return friendly_message(exc.code, str(exc))
    return str(exc)
