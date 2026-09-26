"""FakeMetaTrader5 — an in-memory emulator of the official package.

The real package is Windows-only and needs a running terminal, so the test
suite (Linux CI included) drives the gateway through this fake instead. It
mimics the subset of the module API the application uses:

- namedtuples with the real field names (snapshots convert via getattr)
- module constants (TIMEFRAME_*, TRADE_ACTION_*, ORDER_*, TRADE_RETCODE_*)
- a mini order engine: market opens create positions, closes remove them
- call recording with thread ids — proves the gateway's single-thread
  contract (SPEC C3)
- scriptable failures: ``fail_initialize``, ``fail_order_send``,
  ``fail_calls_with`` (connection loss) for reconnect tests
"""

from __future__ import annotations

import threading
from collections import namedtuple
from dataclasses import dataclass
from typing import Any

# -- constants mirroring the real module -------------------------------------
ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_CONTEST = 1
ACCOUNT_TRADE_MODE_REAL = 2

TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_REMOVE = 8

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1

ORDER_TIME_GTC = 0

ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

TIMEFRAME_M1 = 1
TIMEFRAME_M5 = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 16385
TIMEFRAME_H4 = 16388
TIMEFRAME_D1 = 16408

TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_NO_MONEY = 10019
TRADE_RETCODE_MARKET_CLOSED = 10018
TRADE_RETCODE_CONNECTION = 10031
TRADE_RETCODE_INVALID_FILL = 10030

__version__ = "5.0.9999-fake"

_AccountInfo = namedtuple(
    "AccountInfo",
    [
        "login",
        "trade_mode",
        "leverage",
        "currency",
        "balance",
        "equity",
        "margin_free",
        "name",
        "server",
        "company",
    ],
)
_TerminalInfo = namedtuple(
    "TerminalInfo",
    ["name", "path", "data_path", "build", "connected", "trade_allowed"],
)
_SymbolInfo = namedtuple(
    "SymbolInfo",
    [
        "name",
        "description",
        "visible",
        "select",
        "trade_mode",
        "digits",
        "point",
        "spread",
        "volume_min",
        "volume_max",
        "volume_step",
        "trade_tick_value",
        "trade_tick_size",
        "trade_contract_size",
        "currency_profit",
        "fill_mode",
    ],
)
_Tick = namedtuple("Tick", ["time", "bid", "ask", "last", "volume"])
_Deal = namedtuple(
    "Deal",
    [
        "ticket",
        "order",
        "time",
        "time_msc",
        "type",
        "entry",
        "magic",
        "position_id",
        "symbol",
        "volume",
        "price",
        "commission",
        "swap",
        "profit",
        "comment",
    ],
)
_TradePosition = namedtuple(
    "TradePosition",
    [
        "ticket",
        "identifier",
        "symbol",
        "type",
        "volume",
        "price_open",
        "sl",
        "tp",
        "profit",
        "comment",
        "magic",
    ],
)
_OrderSendResult = namedtuple(
    "OrderSendResult",
    [
        "retcode",
        "deal",
        "order",
        "volume",
        "price",
        "bid",
        "ask",
        "comment",
        "request_id",
        "retcode_external",
    ],
)
_OrderCheckResult = namedtuple(
    "OrderCheckResult",
    [
        "retcode",
        "comment",
        "balance",
        "equity",
        "margin",
        "margin_free",
        "margin_level",
        "volume",
        "price",
    ],
)


@dataclass
class FakeSymbolConfig:
    """Everything the fake needs to quote and trade one symbol."""

    name: str
    digits: int = 5
    point: float = 0.00001
    spread: int = 10
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    bid: float = 1.08000
    ask: float = 1.08010
    fill_mode: int = 2  # IOC allowed
    visible: bool = True
    trade_mode: int = 4  # SYMBOL_TRADE_MODE_FULL
    trade_contract_size: float = 100000.0
    currency_profit: str = "USD"

    def symbol_info(self) -> _SymbolInfo:
        return _SymbolInfo(
            name=self.name,
            description=f"Fake {self.name}",
            visible=self.visible,
            select=True,
            trade_mode=self.trade_mode,
            digits=self.digits,
            point=self.point,
            spread=self.spread,
            volume_min=self.volume_min,
            volume_max=self.volume_max,
            volume_step=self.volume_step,
            trade_tick_value=1.0,
            trade_tick_size=self.point,
            trade_contract_size=self.trade_contract_size,
            currency_profit=self.currency_profit,
            fill_mode=self.fill_mode,
        )


@dataclass
class FakeAccountConfig:
    login: int = 12345678
    trade_mode: int = ACCOUNT_TRADE_MODE_DEMO
    leverage: int = 100
    currency: str = "USD"
    balance: float = 10000.0
    equity: float = 10000.0
    margin_free: float = 9500.0
    name: str = "Test User"
    server: str = "FakeServer-Demo"
    company: str = "Fake Broker Ltd"


@dataclass
class FakeTerminalConfig:
    name: str = "MetaTrader 5"
    path: str = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    data_path: str = r"C:\Users\tester\AppData\Roaming\MetaQuotes\Terminal\ABC"
    build: int = 4885
    connected: bool = True
    trade_allowed: bool = True


_BAR_TIME = 1789728000  # fixed epoch: 2026-09-26 12:00:00 UTC (deterministic)


class FakeMetaTrader5:
    """Stateful fake of the ``MetaTrader5`` module."""

    def __init__(
        self,
        symbols: list[FakeSymbolConfig] | None = None,
        account: FakeAccountConfig | None = None,
        terminal: FakeTerminalConfig | None = None,
        deals: list[_Deal] | None = None,
    ) -> None:
        if symbols is None:
            symbols = [FakeSymbolConfig("EURUSD")]
        self.symbols = {s.name.upper(): s for s in symbols}
        self.account = account or FakeAccountConfig()
        self.terminal = terminal or FakeTerminalConfig()
        self.deals: list[_Deal] = list(deals or [])

        # module-level constants (the real package exposes them at module level)
        for const in (
            "ACCOUNT_TRADE_MODE_DEMO",
            "ACCOUNT_TRADE_MODE_CONTEST",
            "ACCOUNT_TRADE_MODE_REAL",
            "TRADE_ACTION_DEAL",
            "TRADE_ACTION_PENDING",
            "TRADE_ACTION_SLTP",
            "TRADE_ACTION_REMOVE",
            "ORDER_TYPE_BUY",
            "ORDER_TYPE_SELL",
            "ORDER_TIME_GTC",
            "ORDER_FILLING_FOK",
            "ORDER_FILLING_IOC",
            "ORDER_FILLING_RETURN",
            "TIMEFRAME_M1",
            "TIMEFRAME_M5",
            "TIMEFRAME_M15",
            "TIMEFRAME_M30",
            "TIMEFRAME_H1",
            "TIMEFRAME_H4",
            "TIMEFRAME_D1",
            "TRADE_RETCODE_DONE",
            "TRADE_RETCODE_PLACED",
            "TRADE_RETCODE_NO_MONEY",
            "TRADE_RETCODE_MARKET_CLOSED",
            "TRADE_RETCODE_CONNECTION",
        ):
            setattr(self, const, globals()[const])

        self.initialized = False
        self.initialize_kwargs: dict[str, Any] = {}
        self.positions: list[dict[str, Any]] = []
        self.order_requests: list[dict[str, Any]] = []
        self.next_ticket = 5001000
        self.calls: list[tuple[str, int]] = []
        self._error: tuple[int, str] = (0, "")

        # scriptable failure hooks
        self.fail_initialize: tuple[int, str] | None = None
        self.fail_order_send: tuple[int, str] | None = None
        self.fail_calls_with: tuple[int, str] | None = None  # e.g. (10031, "no connection")
        self.fail_after: int = 10**9  # count of *data* calls before fail_calls_with hits

        self._lock = threading.Lock()
        self._data_calls = 0

    # -- plumbing ----------------------------------------------------------
    def _record(self, name: str) -> None:
        with self._lock:
            self.calls.append((name, threading.get_ident()))

    def _maybe_fail(self) -> None:
        """Simulate connection loss on data calls after ``fail_after``."""
        if self.fail_calls_with is None:
            return
        with self._lock:
            self._data_calls += 1
            if self._data_calls > self.fail_after:
                code, desc = self.fail_calls_with
                self._error = (code, desc)
                raise ConnectionError(f"fake terminal failure {code}: {desc}")

    def _set_error(self, code: int, desc: str) -> None:
        self._error = (code, desc)

    def calls_by(self, name: str) -> list[int]:
        """Thread ids that executed ``name``."""
        return [tid for call_name, tid in self.calls if call_name == name]

    # -- connection ----------------------------------------------------------
    def initialize(self, **kwargs: Any) -> bool:
        self._record("initialize")
        self.initialize_kwargs = dict(kwargs)
        if self.fail_initialize is not None:
            code, desc = self.fail_initialize
            self._error = (code, desc)
            return False
        self.initialized = True
        self._error = (0, "")
        return True

    def login(self, login: int, password: str = "", server: str = "") -> bool:
        self._record("login")
        if not self.initialized:
            self._error = (-6, "Terminal: authorization failed")
            return False
        return True

    def shutdown(self) -> None:
        self._record("shutdown")
        self.initialized = False

    def last_error(self) -> tuple[int, str]:
        return self._error

    def version(self) -> tuple[int, int, int]:
        return (5, 0, 9999)

    # -- info ----------------------------------------------------------------
    def account_info(self) -> _AccountInfo | None:
        self._record("account_info")
        self._maybe_fail()
        if not self.initialized:
            self._error = (-6, "Terminal: authorization failed")
            return None
        a = self.account
        return _AccountInfo(
            login=a.login,
            trade_mode=a.trade_mode,
            leverage=a.leverage,
            currency=a.currency,
            balance=a.balance,
            equity=a.equity,
            margin_free=a.margin_free,
            name=a.name,
            server=a.server,
            company=a.company,
        )

    def terminal_info(self) -> _TerminalInfo | None:
        self._record("terminal_info")
        self._maybe_fail()
        if not self.initialized:
            self._error = (-5, "Terminal: failed to connect")
            return None
        t = self.terminal
        return _TerminalInfo(
            name=t.name,
            path=t.path,
            data_path=t.data_path,
            build=t.build,
            connected=t.connected,
            trade_allowed=t.trade_allowed,
        )

    # -- symbols ----------------------------------------------------------------
    def _symbol_config(self, symbol: str) -> FakeSymbolConfig | None:
        return self.symbols.get(symbol.upper())

    def symbols_get(self, group: str = "*") -> tuple[_SymbolInfo, ...] | None:
        self._record("symbols_get")
        self._maybe_fail()
        if not self.initialized:
            self._error = (-5, "Terminal: failed to connect")
            return None
        return tuple(config.symbol_info() for config in self.symbols.values())

    def symbol_info(self, symbol: str) -> _SymbolInfo | None:
        self._record("symbol_info")
        self._maybe_fail()
        config = self._symbol_config(symbol)
        if config is None or not self.initialized:
            self._error = (0, "symbol not found" if config is None else "not initialized")
            return None
        return config.symbol_info()

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        self._record("symbol_select")
        self._maybe_fail()
        if symbol.upper() not in self.symbols:
            self._error = (0, "symbol not found")
            return False
        return True

    def symbol_info_tick(self, symbol: str) -> _Tick | None:
        self._record("symbol_info_tick")
        self._maybe_fail()
        config = self._symbol_config(symbol)
        if config is None or not self.initialized:
            self._error = (0, "symbol not found")
            return None
        return _Tick(time=_BAR_TIME, bid=config.bid, ask=config.ask, last=0.0, volume=0.0)

    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> list[dict[str, Any]]:
        self._record("copy_rates_from_pos")
        self._maybe_fail()
        config = self._symbol_config(symbol)
        if config is None or not self.initialized:
            self._error = (0, "symbol not found" if config is None else "not initialized")
            return []
        bars: list[dict[str, Any]] = []
        price = config.bid
        for index in range(max(count, 1)):
            bars.append(
                {
                    "time": _BAR_TIME - index * 900,
                    "open": price,
                    "high": price * 1.0005,
                    "low": price * 0.9995,
                    "close": price * 1.0001,
                    "tick_volume": 100 + index,
                    "spread": config.spread,
                    "real_volume": 0,
                }
            )
        return bars

    # -- trading -------------------------------------------------------------------
    def order_check(self, request: dict[str, Any]) -> _OrderCheckResult:
        self._record("order_check")
        self._maybe_fail()
        if not self.initialized:
            self._error = (10031, "no connection")
            return _OrderCheckResult(10031, "no connection", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return _OrderCheckResult(
            TRADE_RETCODE_DONE,
            "ok",
            self.account.balance,
            self.account.equity,
            0.0,
            self.account.margin_free,
            0.0,
            request.get("volume", 0.0),
            request.get("price", 0.0),
        )

    def order_send(self, request: dict[str, Any]) -> _OrderSendResult:
        self._record("order_send")
        self._maybe_fail()
        with self._lock:
            self.order_requests.append(dict(request))
        if not self.initialized:
            self._error = (10031, "no connection")
            return _OrderSendResult(10031, 0, 0, 0.0, 0.0, 0.0, 0.0, "no connection", 0, 0)
        if self.fail_order_send is not None:
            code, comment = self.fail_order_send
            return _OrderSendResult(code, 0, 0, 0.0, 0.0, 0.0, 0.0, comment, 0, 0)
        config = self._symbol_config(str(request.get("symbol", "")))
        if config is None:
            return _OrderSendResult(10013, 0, 0, 0.0, 0.0, 0.0, 0.0, "invalid symbol", 0, 0)
        with self._lock:
            ticket = self.next_ticket
            self.next_ticket += 1
            action = request.get("action")
            if action == TRADE_ACTION_DEAL and "position" in request:
                # close: remove the position
                target = int(request["position"])
                self.positions = [p for p in self.positions if p["ticket"] != target]
                return _OrderSendResult(
                    TRADE_RETCODE_DONE,
                    ticket + 1,
                    ticket,
                    float(request.get("volume", 0.0)),
                    config.bid,
                    config.bid,
                    config.ask,
                    "close done",
                    0,
                    0,
                )
            self.positions.append(
                {
                    "ticket": ticket,
                    "identifier": ticket,
                    "symbol": config.name,
                    "type": request.get("type", ORDER_TYPE_BUY),
                    "volume": float(request.get("volume", 0.0)),
                    "price_open": float(request.get("price", config.ask)),
                    "sl": float(request.get("sl", 0.0)),
                    "tp": float(request.get("tp", 0.0)),
                    "profit": 0.0,
                    "comment": str(request.get("comment", "")),
                    "magic": int(request.get("magic", 0)),
                }
            )
        return _OrderSendResult(
            TRADE_RETCODE_DONE,
            ticket + 1,
            ticket,
            float(request.get("volume", 0.0)),
            float(request.get("price", config.ask)),
            config.bid,
            config.ask,
            "done",
            0,
            0,
        )

    def history_deals_get(
        self, date_from: Any = None, date_to: Any = None
    ) -> tuple[_Deal, ...]:
        self._record("history_deals_get")
        self._maybe_fail()
        if not self.initialized:
            self._error = (0, "not initialized")
            return ()
        if date_from is None or date_to is None:
            return tuple(self.deals)
        from_ts = int(date_from.timestamp()) if hasattr(date_from, "timestamp") else int(date_from)
        to_ts = int(date_to.timestamp()) if hasattr(date_to, "timestamp") else int(date_to)
        return tuple(d for d in self.deals if from_ts <= d.time < to_ts)

    def positions_get(self, symbol: str | None = None) -> tuple[_TradePosition, ...] | None:
        self._record("positions_get")
        self._maybe_fail()
        if not self.initialized:
            self._error = (10031, "no connection")
            return None
        with self._lock:
            rows = list(self.positions)
        if symbol is not None:
            rows = [p for p in rows if p["symbol"].upper() == symbol.upper()]
        return tuple(_TradePosition(**p) for p in rows)

    def orders_get(self, symbol: str | None = None) -> tuple[Any, ...] | None:
        self._record("orders_get")
        return ()


def make_fake_module(
    symbols: list[FakeSymbolConfig] | None = None,
    account: FakeAccountConfig | None = None,
    terminal: FakeTerminalConfig | None = None,
    **kwargs: Any,
) -> FakeMetaTrader5:
    """Convenience factory matching the gateway's ``mt5_factory`` protocol."""
    return FakeMetaTrader5(symbols=symbols, account=account, terminal=terminal, **kwargs)


class FakeKeyring:
    """Dict-backed keyring double for CredentialStore tests."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)
