"""MT5Gateway — the single door to the MetaTrader5 terminal (SPEC C3, C7).

**Threading contract.** The official ``MetaTrader5`` package is NOT
thread-safe. Every call into the package therefore happens on exactly one
dedicated worker thread owned by this class. All public methods enqueue a
command and immediately return a :class:`concurrent.futures.Future`; the
caller decides how to wait:

- CLI diagnostics block on ``.result(timeout)`` — safe, no UI there.
- The UI polls ``future.done()`` from a ``QTimer`` — the UI thread never
  blocks (SPEC I-8).

**Reconnect policy.** When a call reports the terminal/broker connection is
gone, the gateway transitions to :attr:`ConnectionState.RECONNECTING` and
re-establishes the session with exponential backoff (initial → max, factor
2). Commands submitted while reconnecting fail fast with
:class:`ConnectionLostError` instead of queueing up behind an unknown delay.

**Heartbeat.** Every loop iteration beats an injected callback so the
Phase-2 watchdog can detect a frozen gateway thread (SPEC E3.9).

The worker converts raw MT5 namedtuples into the typed snapshots from
:mod:`app.mt5.models` before handing results out, so nothing above this
module imports the MetaTrader5 package.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, InvalidStateError
from typing import Any

from app.mt5.errors import (
    RETCODE_CONNECTION,
    TERMINAL_CODE_CONNECT_FAILED,
    TERMINAL_CODE_NO_IPC,
    AuthError,
    ConnectionLostError,
    GatewayTimeoutError,
    InvalidSymbolError,
    MT5Error,
    TerminalError,
    raise_for_check_result,
    raise_for_order_result,
)
from app.mt5.models import (
    AccountSnapshot,
    ConnectionState,
    ConnectRequest,
    DealSnapshot,
    FillingConstants,
    GatewayStats,
    OrderResultSnapshot,
    PositionSnapshot,
    RateBar,
    SymbolSnapshot,
    TerminalSnapshot,
    TickSnapshot,
)
from app.observability.logger import get_logger

log = get_logger("mt5")

HeartbeatFn = Callable[[], None]
StateCallback = Callable[[ConnectionState], None]
#: Factory returning the MetaTrader5 module (or a test fake). Called lazily
#: on the worker thread so the import itself is part of the single-threaded
#: contract.
MT5Factory = Callable[[], Any]

_IDLE_POLL_S = 0.25
_DEFAULT_TIMEOUT_S = 10.0


class MT5Gateway:
    """Dedicated-thread wrapper around the MetaTrader5 package."""

    def __init__(
        self,
        *,
        mt5_factory: MT5Factory | None = None,
        request_timeout_s: float = _DEFAULT_TIMEOUT_S,
        auto_reconnect: bool = True,
        reconnect_initial_s: float = 1.0,
        reconnect_max_s: float = 30.0,
        reconnect_factor: float = 2.0,
        heartbeat: HeartbeatFn | None = None,
        on_state_change: StateCallback | None = None,
        time_fn: Callable[[], float] = time.monotonic,
        name: str = "",
    ) -> None:
        self._mt5_factory = mt5_factory or _default_mt5_factory
        # Disambiguates log lines when several gateways live in one process
        # (the app shell's shared gateway + Settings-page one-shot probes).
        self._name = name or "gw"
        self._request_timeout_s = request_timeout_s
        self._auto_reconnect = auto_reconnect
        self._reconnect_initial_s = reconnect_initial_s
        self._reconnect_max_s = reconnect_max_s
        self._reconnect_factor = reconnect_factor
        self._heartbeat_fn = heartbeat
        self._on_state_change = on_state_change
        self._time_fn = time_fn

        self._queue: queue.Queue[tuple[str, Callable[[], Any], Future[Any]] | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._worker_exited = threading.Event()
        self._thread: threading.Thread | None = None

        self._state = ConnectionState.DISCONNECTED
        self._state_lock = threading.RLock()
        self._module: Any = None
        self._request: ConnectRequest | None = None
        self._account: AccountSnapshot | None = None
        self._stats = GatewayStats()

        # reconnect bookkeeping (worker thread only, guarded by _state_lock for cross-thread reads)
        self._reconnect_delay_s = reconnect_initial_s
        self._next_reconnect_at = 0.0

    # ------------------------------------------------------------------ #
    # lifecycle                                                           #
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Start the worker thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_exited.clear()
        self._thread = threading.Thread(target=self._run, name="mt5-gateway", daemon=True)
        self._thread.start()
        log.info("gateway[{}]: started", self._name)

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop the worker, release the terminal session, fail pending commands.

        When a slow terminal call outlives ``timeout_s``, the thread
        reference is deliberately KEPT: a following ``start()`` must not
        spawn a second worker while the first one is still draining —
        two workers race the non-thread-safe MT5 module (duplicate
        ``started`` lines, ghost ``session closed`` after ``stopped``,
        interleaved initialize/shutdown). Re-run ``stop()`` to keep
        waiting, or ``start()`` once the drained thread has exited.
        """
        if self._thread is None:
            return
        self._stop_event.set()
        self._queue.put(None)  # wake the worker so it sees the stop flag
        self._thread.join(timeout=timeout_s)
        if self._thread.is_alive():
            log.warning(
                "gateway[{}]: worker did not exit within {:.0f}s — a slow "
                "terminal call is still draining; start() is disabled until "
                "the thread exits",
                self._name,
                timeout_s,
            )
            return
        self._thread = None
        log.info("gateway[{}]: stopped", self._name)

    # ------------------------------------------------------------------ #
    # public API — enqueue only, never touch MT5 on the calling thread     #
    # ------------------------------------------------------------------ #
    def connect(self, request: ConnectRequest) -> Future[AccountSnapshot]:
        """Open a terminal session and log in. Returns the account snapshot."""
        return self._submit("connect", lambda: self._establish(request))

    def disconnect(self) -> Future[None]:
        """Close the terminal session and return to DISCONNECTED.

        Works in every state — including RECONNECTING, where it cancels
        the pending reconnection instead of failing fast (a user who
        closes the session must always be able to stop the loop).
        """
        return self._submit("disconnect", self._teardown_session, bypass_reconnect_guard=True)

    def account_info(self) -> Future[AccountSnapshot]:
        """Fetch the current account snapshot."""
        return self._submit("account_info", self._fetch_account)

    def terminal_info(self) -> Future[TerminalSnapshot]:
        """Fetch the current terminal snapshot."""
        return self._submit("terminal_info", self._fetch_terminal)

    def symbol_names(self) -> Future[tuple[str, ...]]:
        """All symbol names known to the terminal (for suffix resolution)."""
        return self._submit("symbol_names", self._fetch_symbol_names)

    def symbol_info(self, symbol: str) -> Future[SymbolSnapshot]:
        """Fetch one symbol's trading metadata (exact broker-side name)."""
        return self._submit("symbol_info", lambda: self._fetch_symbol(symbol))

    def tick(self, symbol: str) -> Future[TickSnapshot]:
        """Fetch the latest quote for one symbol."""
        return self._submit("tick", lambda: self._fetch_tick(symbol))

    def order_check(self, request: dict[str, Any]) -> Future[OrderResultSnapshot]:
        """Validate a trade request against the server without placing it."""
        return self._submit("order_check", lambda: self._check_order(request))

    def select_symbol(self, symbol: str) -> Future[bool]:
        """Make a symbol visible in Market Watch (required before quoting)."""
        return self._submit("select_symbol", lambda: self._select_symbol(symbol))

    def rates_from_pos(
        self, symbol: str, timeframe: str, start_pos: int, count: int
    ) -> Future[tuple[RateBar, ...]]:
        """Closed/known bars counting back from the newest (Phase 5 uses this)."""
        return self._submit(
            "rates_from_pos",
            lambda: self._fetch_rates(symbol, timeframe, start_pos, count),
        )

    def positions(self, symbol: str | None = None) -> Future[tuple[PositionSnapshot, ...]]:
        """Open positions, optionally filtered by broker-side symbol."""
        return self._submit("positions", lambda: self._fetch_positions(symbol))

    def history_deals(
        self, date_from_epoch: int, date_to_epoch: int
    ) -> Future[tuple[DealSnapshot, ...]]:
        """Historical deals in ``[from, to)`` UTC epoch seconds (Phase 4)."""
        return self._submit(
            "history_deals",
            lambda: self._fetch_deals(date_from_epoch, date_to_epoch),
        )

    def order_send(self, request: dict[str, Any]) -> Future[OrderResultSnapshot]:
        """Send one trade request dict (low-level primitive for Phase 7+)."""
        return self._submit("order_send", lambda: self._send_order(request))

    def filling_constants(self) -> Future[FillingConstants]:
        """Capture the module's order/ filling constants on the worker thread.

        Lets CLI tools (demo trade test) build request dicts without
        touching the private module from a foreign thread.
        """
        return self._submit("filling_constants", self._capture_filling_constants)

    def ping(self) -> Future[float]:
        """Round-trip latency of a terminal_info() call, in milliseconds."""
        return self._submit("ping", self._ping)

    # ------------------------------------------------------------------ #
    # state / stats (thread-safe reads)                                    #
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> ConnectionState:
        """Current connection state (atomic enum read, safe from any thread)."""
        with self._state_lock:
            return self._state

    @property
    def account(self) -> AccountSnapshot | None:
        """Snapshot captured at connect time, or ``None``."""
        return self._account

    @property
    def stats(self) -> GatewayStats:
        return self._stats

    def wait_for_result(
        self, future: Future[Any], operation: str, timeout_s: float | None = None
    ) -> Any:
        """Blocking wait helper for CLI code (never use on the UI thread)."""
        effective = self._request_timeout_s if timeout_s is None else timeout_s
        try:
            return future.result(timeout=effective)
        except TimeoutError as exc:
            raise GatewayTimeoutError(operation, effective) from exc

    # ------------------------------------------------------------------ #
    # internals — worker thread only from here on                          #
    # ------------------------------------------------------------------ #
    def _submit(
        self, operation: str, fn: Callable[[], Any], *, bypass_reconnect_guard: bool = False
    ) -> Future[Any]:
        future: Future[Any] = Future()
        if self._stop_event.is_set() or self._worker_exited.is_set():
            future.set_exception(MT5Error(code=0, description="gateway is stopped"))
            return future
        # RECONNECTING guard must be atomic with queue put: the worker mutates
        # _state and _reconnect_delay_s without holding the GIL across the
        # check, so a plain read+put races and can queue a command that should
        # have failed fast or report a stale delay.
        with self._state_lock:
            if self._state is ConnectionState.RECONNECTING and not bypass_reconnect_guard:
                # Fail fast instead of queueing behind an unknown backoff delay.
                future.set_exception(
                    ConnectionLostError(
                        RETCODE_CONNECTION,
                        f"gateway is reconnecting (next attempt in ~{self._reconnect_delay_s:.1f}s)",
                    )
                )
                return future
            self._queue.put((operation, fn, future))
        if self._worker_exited.is_set():
            # Raced the worker's final drain — the queued item will never
            # run; fail the future here so the caller never hangs.
            with contextlib.suppress(InvalidStateError):  # drain beat us to it
                future.set_exception(MT5Error(code=0, description="gateway is stopped"))
        return future

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._beat()
                if self.state is ConnectionState.RECONNECTING:
                    # While reconnecting, only control commands are
                    # meaningful: execute a queued disconnect (cancels the
                    # loop) and fail everything else queued before the
                    # outage instead of letting futures hang forever.
                    self._drain_while_reconnecting()
                    if self.state is not ConnectionState.RECONNECTING:
                        continue
                    self._poll_reconnect()
                    continue
                try:
                    item = self._queue.get(timeout=_IDLE_POLL_S)
                except queue.Empty:
                    continue
                if item is None:
                    break
                operation, fn, future = item
                self._execute(operation, fn, future)
            except Exception:
                # Umbrella guard: one unexpected exception must never kill
                # the worker silently (state would stay stuck, queued
                # futures would never resolve, nothing would notice).
                log.opt(exception=True).critical(
                    "gateway[{}]: worker loop error — recovering", self._name
                )
        self._drain_queue()
        self._worker_exited.set()
        self._teardown_session()
        log.debug("gateway[{}]: worker exited", self._name)

    def _drain_while_reconnecting(self) -> None:
        """Handle queued commands while RECONNECTING (worker thread only)."""
        while self.state is ConnectionState.RECONNECTING and not self._stop_event.is_set():
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is None:
                continue
            operation, fn, future = item
            if operation == "disconnect":
                self._execute(operation, fn, future)
            elif not future.done():
                future.set_exception(
                    ConnectionLostError(
                        RETCODE_CONNECTION,
                        "gateway is reconnecting — command dropped",
                    )
                )

    def _drain_queue(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is None:
                continue
            _operation, _fn, future = item
            if not future.done():
                future.set_exception(MT5Error(code=0, description="gateway is stopped"))

    def _execute(self, operation: str, fn: Callable[[], Any], future: Future[Any]) -> None:
        self._beat()
        self._stats.commands_total += 1
        started = self._time_fn()
        try:
            result = fn()
        except ConnectionLostError as exc:
            self._stats.commands_failed += 1
            future.set_exception(exc)
            self._enter_reconnecting(exc)
            return
        except ConnectionError as exc:
            # Transport-level failure raised by the module layer (e.g. IPC drop).
            self._stats.commands_failed += 1
            wrapped = ConnectionLostError(RETCODE_CONNECTION, str(exc))
            future.set_exception(wrapped)
            self._enter_reconnecting(wrapped)
            return
        except Exception as exc:
            self._stats.commands_failed += 1
            future.set_exception(exc)
            return
        self._stats.last_latency_ms = (self._time_fn() - started) * 1000.0
        future.set_result(result)

    def _beat(self) -> None:
        if self._heartbeat_fn is not None:
            try:
                self._heartbeat_fn()
            except Exception:  # pragma: no cover - heartbeat must never kill the worker
                log.opt(exception=True).warning(
                    "gateway[{}]: heartbeat callback failed", self._name
                )

    # -- connection management ---------------------------------------------
    def _set_state(self, new_state: ConnectionState) -> None:
        with self._state_lock:
            if new_state is self._state:
                return
            self._state = new_state
        self._stats.state_history.append(f"{new_state.value}@{int(self._time_fn())}")
        log.info("gateway[{}]: state -> {}", self._name, new_state.value)
        callback = self._on_state_change
        if callback is not None:
            try:
                callback(new_state)
            except Exception:  # pragma: no cover - defensive
                log.opt(exception=True).warning("gateway[{}]: state callback failed", self._name)

    def _module_or_fail(self) -> Any:
        """Lazily import/obtain the MT5 module on the worker thread."""
        if self._module is None:
            try:
                self._module = self._mt5_factory()
            except Exception as exc:
                raise MT5Error(
                    code=0,
                    description=f"MetaTrader5 package unavailable: {exc}",
                ) from exc
        return self._module

    def _establish(self, request: ConnectRequest) -> AccountSnapshot:
        """initialize + login + verify. Runs on the worker thread."""
        module = self._module_or_fail()
        try:
            requested_login = int(request.login)
        except (TypeError, ValueError) as exc:
            raise MT5Error(code=0, description=f"invalid login {request.login!r}") from exc
        self._set_state(ConnectionState.CONNECTING)
        kwargs: dict[str, Any] = {
            "login": requested_login,
            "password": request.password,
            "server": request.server,
        }
        if request.terminal_path:
            kwargs["path"] = request.terminal_path
        log.info("gateway[{}]: connecting {}", self._name, request.masked())
        try:
            ok = bool(module.initialize(**kwargs))
            if not ok:
                code, description = module.last_error()
                # Release whatever half-open IPC state initialize() left.
                with contextlib.suppress(Exception):
                    module.shutdown()
                self._set_state(ConnectionState.DISCONNECTED)
                raise _terminal_exception(int(code), str(description or ""))
            account_raw = module.account_info()
            if account_raw is None:
                code, description = module.last_error()
                module.shutdown()
                self._set_state(ConnectionState.DISCONNECTED)
                raise _terminal_exception(int(code), str(description or ""))
            snapshot = _to_account_snapshot(account_raw)
        except ConnectionLostError:
            raise
        except MT5Error:
            raise
        except Exception as exc:
            self._set_state(ConnectionState.DISCONNECTED)
            raise MT5Error(code=0, description=f"terminal connection failed: {exc}") from exc

        if snapshot.login != requested_login:
            # The terminal was already logged into a DIFFERENT account than
            # the one requested — a silent account mismatch would corrupt
            # every downstream trade/history decision.
            module.shutdown()
            self._set_state(ConnectionState.DISCONNECTED)
            raise MT5Error(
                code=0,
                description=(
                    f"terminal logged into account ***{str(snapshot.login)[-3:]} "
                    f"instead of requested ***{str(requested_login)[-3:]}"
                ),
            )
        self._request = request
        self._account = snapshot
        with self._state_lock:
            self._reconnect_delay_s = self._reconnect_initial_s
        self._set_state(ConnectionState.CONNECTED)
        log.info(
            "gateway[{}]: connected login={} server={} mode={} balance={:.2f} {}",
            self._name,
            _mask_login(snapshot.login),
            snapshot.server,
            snapshot.mode_name,
            snapshot.balance,
            snapshot.currency,
        )
        return snapshot

    def _teardown_session(self) -> None:
        """shutdown() the module and go DISCONNECTED (idempotent, worker only)."""
        if self._module is not None:
            try:
                self._module.shutdown()
            except Exception:  # pragma: no cover - shutdown must never raise
                log.opt(exception=True).debug("gateway[{}]: shutdown() raised", self._name)
        was_connected = self.state is ConnectionState.CONNECTED
        self._account = None
        self._request = None
        self._set_state(ConnectionState.DISCONNECTED)
        if was_connected:
            log.info("gateway[{}]: session closed", self._name)

    def _enter_reconnecting(self, exc: ConnectionLostError) -> None:
        if not self._auto_reconnect:
            log.warning(
                "gateway[{}]: connection lost, auto-reconnect disabled ({})", self._name, exc
            )
            self._teardown_session()
            return
        if self._request is None:
            # Never had a session (command fired before connect) — nothing to re-establish.
            log.warning("gateway[{}]: command rejected while disconnected ({})", self._name, exc)
            return
        with self._state_lock:
            self._reconnect_delay_s = self._reconnect_initial_s
            self._next_reconnect_at = self._time_fn() + self._reconnect_delay_s
        self._set_state(ConnectionState.RECONNECTING)
        log.warning("gateway[{}]: connection lost ({}), reconnecting…", self._name, exc)

    def _poll_reconnect(self) -> None:
        request = self._request
        if request is None:
            # Nothing to re-establish (never connected, or a queued
            # disconnect already cancelled the loop).
            self._teardown_session()
            return
        now = self._time_fn()
        with self._state_lock:
            next_at = self._next_reconnect_at
        if now < next_at:
            remaining = min(next_at - now, _IDLE_POLL_S)
            if self._stop_event.wait(remaining):
                return
            self._beat()
            return
        try:
            self._stats.reconnects += 1  # counts every reconnect attempt
            self._establish(request)
        except AuthError as exc:
            # Permanent failure (wrong password / changed credentials):
            # retrying every 30 s cannot succeed and would lock the
            # account. Give up; the user must reconnect manually.
            log.error(
                "gateway[{}]: reconnect aborted — permanent auth failure ({})", self._name, exc
            )
            self._teardown_session()
        except MT5Error as exc:
            with self._state_lock:
                self._reconnect_delay_s = min(
                    self._reconnect_delay_s * self._reconnect_factor,
                    self._reconnect_max_s,
                )
                self._next_reconnect_at = self._time_fn() + self._reconnect_delay_s
                delay = self._reconnect_delay_s
            # _establish() failure flipped the state through CONNECTING/DISCONNECTED;
            # re-arm RECONNECTING so the loop keeps trying.
            self._set_state(ConnectionState.RECONNECTING)
            log.warning(
                "gateway[{}]: reconnect attempt failed ({}) — next try in {:.1f}s",
                self._name,
                exc,
                delay,
            )
        except Exception as exc:
            # Unexpected (non-MT5Error) failure — treat as retryable so the
            # umbrella loop guard above is never the only line of defense.
            with self._state_lock:
                self._reconnect_delay_s = min(
                    self._reconnect_delay_s * self._reconnect_factor,
                    self._reconnect_max_s,
                )
                self._next_reconnect_at = self._time_fn() + self._reconnect_delay_s
                delay = self._reconnect_delay_s
            self._set_state(ConnectionState.RECONNECTING)
            log.opt(exception=True).warning(
                "gateway[{}]: reconnect crashed ({}) — next try in {:.1f}s",
                self._name,
                exc,
                delay,
            )

    # -- raw call helpers ----------------------------------------------------
    def _require(self, result: Any, what: str) -> Any:
        """Fail loudly when a data call returned None/False; map disconnects."""
        if result is None or result is False:
            code, description = self._last_error()
            if code in (RETCODE_CONNECTION, TERMINAL_CODE_CONNECT_FAILED, TERMINAL_CODE_NO_IPC):
                raise ConnectionLostError(code, description)
            raise TerminalError(code or -1, description or f"{what} returned nothing")
        return result

    def _last_error(self) -> tuple[int, str]:
        try:
            code, description = self._module_or_fail().last_error()
            return int(code), str(description or "")
        except Exception:  # pragma: no cover - defensive
            return 0, ""

    # -- typed fetchers --------------------------------------------------------
    def _fetch_account(self) -> AccountSnapshot:
        self._require_connected("account_info")
        return _to_account_snapshot(self._require(self._module.account_info(), "account_info"))

    def _fetch_terminal(self) -> TerminalSnapshot:
        self._require_connected("terminal_info")
        return _to_terminal_snapshot(self._require(self._module.terminal_info(), "terminal_info"))

    def _fetch_symbol_names(self) -> tuple[str, ...]:
        self._require_connected("symbol_names")
        symbols = self._require(self._module.symbols_get(), "symbols_get")
        return tuple(str(item.name) for item in symbols)

    def _fetch_symbol(self, symbol: str) -> SymbolSnapshot:
        self._require_connected("symbol_info")
        raw = self._module.symbol_info(symbol)
        if raw is None:
            # ``symbol_info`` returns None BOTH for unknown symbols and for
            # IPC/terminal failures — consult last_error() and map disconnect
            # codes so a mid-session drop enters the reconnect path instead
            # of being misreported as a data problem.
            code, description = self._last_error()
            if code in (RETCODE_CONNECTION, TERMINAL_CODE_CONNECT_FAILED, TERMINAL_CODE_NO_IPC):
                raise ConnectionLostError(code, description or f"symbol_info failed: {symbol}")
            raise InvalidSymbolError(
                symbol,
                code=code,  # keep 0/unmapped so friendly_message() falls
                # back to the description instead of a generic table entry
                description=description,
            )
        return _to_symbol_snapshot(raw)

    def _fetch_tick(self, symbol: str) -> TickSnapshot:
        self._require_connected("tick")
        raw = self._require(self._module.symbol_info_tick(symbol), "symbol_info_tick")
        return TickSnapshot(
            time=int(getattr(raw, "time", 0)),
            bid=float(getattr(raw, "bid", 0.0)),
            ask=float(getattr(raw, "ask", 0.0)),
            last=float(getattr(raw, "last", 0.0)),
            volume=float(getattr(raw, "volume", 0.0)),
        )

    def _check_order(self, request: dict[str, Any]) -> OrderResultSnapshot:
        self._require_connected("order_check")
        result = self._module.order_check(request)
        if result is None:
            code, description = self._last_error()
            raise TerminalError(code or -1, description or "order_check returned nothing")
        # order_check mirrors MqlTradeCheckResult — success is commonly
        # retcode 0, not the TRADE_RETCODE_* values order_send returns.
        raise_for_check_result(result, self._module)
        return _to_order_result_snapshot(result)

    def _fetch_rates(
        self, symbol: str, timeframe: str, start_pos: int, count: int
    ) -> tuple[RateBar, ...]:
        self._require_connected("rates_from_pos")
        tf = timeframe_value(self._module, timeframe)
        raw = self._require(
            self._module.copy_rates_from_pos(symbol, tf, int(start_pos), int(count)),
            "copy_rates_from_pos",
        )
        return tuple(
            RateBar(
                time=int(row["time"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                tick_volume=int(row["tick_volume"]),
                spread=int(row["spread"]),
                # Real copy_rates arrays always carry this field (0 for FX).
                real_volume=int(row["real_volume"]),
            )
            for row in raw
        )

    def _fetch_deals(self, date_from_epoch: int, date_to_epoch: int) -> tuple[DealSnapshot, ...]:
        self._require_connected("history_deals")
        import datetime as _dt

        date_from = _dt.datetime.fromtimestamp(int(date_from_epoch), tz=_dt.UTC)
        date_to = _dt.datetime.fromtimestamp(int(date_to_epoch), tz=_dt.UTC)
        raw = self._require(
            self._module.history_deals_get(date_from, date_to),
            "history_deals_get",
        )
        return tuple(_to_deal_snapshot(deal) for deal in raw)

    def _fetch_positions(self, symbol: str | None) -> tuple[PositionSnapshot, ...]:
        self._require_connected("positions")
        raw = self._module.positions_get(symbol=symbol) if symbol else self._module.positions_get()
        if raw is None:
            code, _description = self._last_error()
            if code == 0:  # no positions at all — normal, not an error
                return ()
            self._require(raw, "positions_get")
        return tuple(_to_position_snapshot(item) for item in raw or ())

    def _send_order(self, request: dict[str, Any]) -> OrderResultSnapshot:
        self._require_connected("order_send")
        result = self._module.order_send(request)
        if result is None:
            code, description = self._last_error()
            raise TerminalError(code or -1, description or "order_send returned nothing")
        raise_for_order_result(result, self._module)
        return _to_order_result_snapshot(result)

    def _ping(self) -> float:
        self._require_connected("ping")
        started = self._time_fn()
        self._require(self._module.terminal_info(), "terminal_info")
        return (self._time_fn() - started) * 1000.0

    def _require_connected(self, operation: str) -> None:
        if self.state is not ConnectionState.CONNECTED:
            raise ConnectionLostError(
                RETCODE_CONNECTION,
                f"{operation} requested while {self.state.value}",
            )

    def _select_symbol(self, symbol: str) -> bool:
        """Worker-side select with the standard connected-state contract."""
        self._require_connected("select_symbol")
        selected = self._require(bool(self._module.symbol_select(symbol, True)), "symbol_select")
        return bool(selected)

    def _capture_filling_constants(self) -> FillingConstants:
        """Snapshot the module's order constants (worker thread only)."""
        module = self._module_or_fail()
        return FillingConstants(
            trade_action_deal=int(getattr(module, "TRADE_ACTION_DEAL", 1)),
            order_time_gtc=int(getattr(module, "ORDER_TIME_GTC", 0)),
            order_filling_fok=int(getattr(module, "ORDER_FILLING_FOK", 0)),
            order_filling_ioc=int(getattr(module, "ORDER_FILLING_IOC", 1)),
            order_filling_return=int(getattr(module, "ORDER_FILLING_RETURN", 2)),
        )


# --------------------------------------------------------------------------- #
# pure helpers (no gateway state)                                              #
# --------------------------------------------------------------------------- #
_TIMEFRAME_ATTRS: dict[str, str] = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
    "W1": "TIMEFRAME_W1",
    "MN1": "TIMEFRAME_MN1",
}


def timeframe_value(module: Any, timeframe: str) -> int:
    """Map ``"M15"``-style names to the module's TIMEFRAME_* constant."""
    attr = _TIMEFRAME_ATTRS.get(timeframe.upper())
    if attr is None:
        msg = f"unknown timeframe: {timeframe!r}"
        raise ValueError(msg)
    return int(getattr(module, attr))


def _default_mt5_factory() -> Any:
    """Import the real MetaTrader5 package (Windows builds only)."""
    import importlib

    return importlib.import_module("MetaTrader5")


def _terminal_exception(code: int, description: str) -> MT5Error:
    from app.mt5.errors import TERMINAL_CODE_AUTH_FAILED

    if code == TERMINAL_CODE_AUTH_FAILED:
        return AuthError(code, description)
    if code in (TERMINAL_CODE_CONNECT_FAILED, TERMINAL_CODE_NO_IPC):
        return ConnectionLostError(code, description)
    return TerminalError(code, description)


def _mask_login(login: int | str) -> str:
    text = str(login)
    return f"***{text[-3:]}" if len(text) > 3 else "***"


def _to_account_snapshot(raw: Any) -> AccountSnapshot:
    return AccountSnapshot(
        login=int(getattr(raw, "login", 0)),
        name=str(getattr(raw, "name", "")),
        server=str(getattr(raw, "server", "")),
        company=str(getattr(raw, "company", "")),
        currency=str(getattr(raw, "currency", "")),
        trade_mode=int(getattr(raw, "trade_mode", 0)),
        leverage=int(getattr(raw, "leverage", 0)),
        balance=float(getattr(raw, "balance", 0.0)),
        equity=float(getattr(raw, "equity", 0.0)),
        margin_free=float(getattr(raw, "margin_free", 0.0)),
    )


def _to_terminal_snapshot(raw: Any) -> TerminalSnapshot:
    return TerminalSnapshot(
        name=str(getattr(raw, "name", "")),
        path=str(getattr(raw, "path", "")),
        data_path=str(getattr(raw, "data_path", "")),
        build=int(getattr(raw, "build", 0)),
        connected=bool(getattr(raw, "connected", False)),
        trade_allowed=bool(getattr(raw, "trade_allowed", False)),
    )


def _to_symbol_snapshot(raw: Any) -> SymbolSnapshot:
    return SymbolSnapshot(
        name=str(getattr(raw, "name", "")),
        description=str(getattr(raw, "description", "")),
        visible=bool(getattr(raw, "visible", False)),
        trade_mode=int(getattr(raw, "trade_mode", 0)),
        digits=int(getattr(raw, "digits", 0)),
        point=float(getattr(raw, "point", 0.0)),
        spread=int(getattr(raw, "spread", 0)),
        volume_min=float(getattr(raw, "volume_min", 0.0)),
        volume_max=float(getattr(raw, "volume_max", 0.0)),
        volume_step=float(getattr(raw, "volume_step", 0.0)),
        trade_tick_value=float(getattr(raw, "trade_tick_value", 0.0)),
        trade_tick_size=float(getattr(raw, "trade_tick_size", 0.0)),
        trade_contract_size=float(getattr(raw, "trade_contract_size", 0.0)),
        currency_profit=str(getattr(raw, "currency_profit", "")),
        fill_mode=int(getattr(raw, "fill_mode", 0)),
    )


def _to_order_result_snapshot(result: Any) -> OrderResultSnapshot:
    return OrderResultSnapshot(
        retcode=int(getattr(result, "retcode", 0)),
        order_ticket=int(getattr(result, "order", 0) or 0),
        deal_ticket=int(getattr(result, "deal", 0) or 0),
        volume=float(getattr(result, "volume", 0.0) or 0.0),
        price=float(getattr(result, "price", 0.0) or 0.0),
        comment=str(getattr(result, "comment", "") or ""),
    )


def _to_deal_snapshot(raw: Any) -> DealSnapshot:
    return DealSnapshot(
        ticket=int(getattr(raw, "ticket", 0)),
        order=int(getattr(raw, "order", 0)),
        time=int(getattr(raw, "time", 0)),
        time_msc=int(getattr(raw, "time_msc", 0)),
        type=int(getattr(raw, "type", 0)),
        entry=int(getattr(raw, "entry", 0)),
        magic=int(getattr(raw, "magic", 0)),
        position_id=int(getattr(raw, "position_id", 0)),
        symbol=str(getattr(raw, "symbol", "") or ""),
        volume=float(getattr(raw, "volume", 0.0) or 0.0),
        price=float(getattr(raw, "price", 0.0) or 0.0),
        commission=float(getattr(raw, "commission", 0.0) or 0.0),
        swap=float(getattr(raw, "swap", 0.0) or 0.0),
        profit=float(getattr(raw, "profit", 0.0) or 0.0),
        comment=str(getattr(raw, "comment", "") or ""),
    )


def _to_position_snapshot(raw: Any) -> PositionSnapshot:
    return PositionSnapshot(
        ticket=int(getattr(raw, "ticket", 0)),
        identifier=int(getattr(raw, "identifier", 0)),
        symbol=str(getattr(raw, "symbol", "")),
        type=int(getattr(raw, "type", 0)),
        volume=float(getattr(raw, "volume", 0.0)),
        price_open=float(getattr(raw, "price_open", 0.0)),
        sl=float(getattr(raw, "sl", 0.0)),
        tp=float(getattr(raw, "tp", 0.0)),
        profit=float(getattr(raw, "profit", 0.0)),
        comment=str(getattr(raw, "comment", "")),
        magic=int(getattr(raw, "magic", 0)),
    )
