"""Typed snapshots produced by the MT5 gateway (SPEC C2, C3).

The real MetaTrader5 package returns module-level namedtuples. The gateway
converts them into these frozen dataclasses inside its worker thread so the
rest of the application (UI, engine, storage) never depends on the MT5
package — keeping the domain layer independent of the broker (SPEC C2).

Fields the application does not need are dropped deliberately; anything
missing on a future build defaults via ``getattr`` conversions in
:mod:`app.mt5.gateway`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Connection state machine
# ---------------------------------------------------------------------------


class ConnectionState(Enum):
    """Lifecycle of the gateway's terminal session."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_CONTEST = 1
ACCOUNT_TRADE_MODE_REAL = 2

_ACCOUNT_MODE_NAMES = {
    ACCOUNT_TRADE_MODE_DEMO: "demo",
    ACCOUNT_TRADE_MODE_CONTEST: "contest",
    ACCOUNT_TRADE_MODE_REAL: "real",
}


def account_mode_name(trade_mode: int) -> str:
    """Human-readable account mode: demo / contest / real."""
    return _ACCOUNT_MODE_NAMES.get(int(trade_mode), f"unknown({trade_mode})")


def is_demo_account(trade_mode: int) -> bool:
    """True when the account is a demo account (trade test guard)."""
    return int(trade_mode) == ACCOUNT_TRADE_MODE_DEMO


def is_real_account(trade_mode: int) -> bool:
    """True when the account trades real money."""
    return int(trade_mode) == ACCOUNT_TRADE_MODE_REAL


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Key fields of ``mt5.account_info()``."""

    login: int
    name: str
    server: str
    company: str
    currency: str
    trade_mode: int
    leverage: int
    balance: float
    equity: float
    margin_free: float

    @property
    def mode_name(self) -> str:
        return account_mode_name(self.trade_mode)

    @property
    def is_real(self) -> bool:
        return is_real_account(self.trade_mode)

    @property
    def is_demo(self) -> bool:
        return is_demo_account(self.trade_mode)


@dataclass(frozen=True, slots=True)
class TerminalSnapshot:
    """Key fields of ``mt5.terminal_info()``."""

    name: str
    path: str
    data_path: str
    build: int
    connected: bool
    trade_allowed: bool


@dataclass(frozen=True, slots=True)
class SymbolSnapshot:
    """Key fields of ``mt5.symbol_info()`` (per resolved broker symbol)."""

    name: str
    description: str
    visible: bool
    trade_mode: int
    digits: int
    point: float
    spread: int
    volume_min: float
    volume_max: float
    volume_step: float
    trade_tick_value: float
    trade_tick_size: float
    trade_contract_size: float
    currency_profit: str
    #: Fill-policy bitmask from the broker (1 = FOK, 2 = IOC, 4 = RETURN).
    fill_mode: int = 0


@dataclass(frozen=True, slots=True)
class TickSnapshot:
    """Latest quote for one symbol (``mt5.symbol_info_tick()``)."""

    time: int  # epoch seconds (UTC) of the tick
    bid: float
    ask: float
    last: float
    volume: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0 if self.bid and self.ask else 0.0


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Key fields of an open position (``mt5.positions_get()`` element)."""

    ticket: int
    identifier: int
    symbol: str
    type: int
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float
    comment: str
    magic: int = 0


@dataclass(frozen=True, slots=True)
class RateBar:
    """One OHLCV bar (structured row of ``copy_rates_from_pos``)."""

    time: int  # epoch seconds (UTC, bar open)
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int = 0


@dataclass(frozen=True, slots=True)
class ConnectRequest:
    """Credentials for one terminal session.

    The password is carried in memory only and never logged, exported or
    persisted outside Windows Credential Manager (SPEC C11, I-6).
    """

    login: int
    password: str
    server: str
    terminal_path: str = ""

    def masked(self) -> dict[str, str]:
        """Log-safe representation: password dropped, login tail-shown."""
        return {
            "login": mask_login(self.login),
            "server": self.server,
            "terminal_path": self.terminal_path or "<default>",
        }


def mask_login(login: int | str) -> str:
    """Mask an account number: keep the last 3 digits only."""
    text = str(login)
    visible = text[-3:] if len(text) > 3 else "***"
    return f"***{visible}"


@dataclass(frozen=True, slots=True)
class OrderResultSnapshot:
    """Result of ``order_send`` / ``order_check`` (typed, log-safe)."""

    retcode: int
    order_ticket: int
    deal_ticket: int
    volume: float
    price: float
    comment: str

    @property
    def succeeded(self) -> bool:
        from app.mt5.errors import RETCODE_DONE, RETCODE_DONE_PARTIAL, RETCODE_PLACED

        return self.retcode in (RETCODE_DONE, RETCODE_PLACED, RETCODE_DONE_PARTIAL)


@dataclass(slots=True)
class GatewayStats:
    """Counters for the health page and diagnostics."""

    commands_total: int = 0
    commands_failed: int = 0
    reconnects: int = 0
    last_latency_ms: float = 0.0
    state_history: list[str] = field(default_factory=list)
