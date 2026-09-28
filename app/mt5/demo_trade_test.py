"""Demo trade test — proves the *order path* works end-to-end (SPEC C13).

``--mt5-trade-test`` opens one minimal market order and closes it again.
It exists because a green smoke test proves reading works, not that the
broker accepts orders from this terminal (fill policy, volume steps,
trade permissions…).

**Hard guards (SPEC I-3, I-8):**

1. The account MUST be a demo account (``trade_mode == 0``). Real and
   contest accounts are refused *before any order construction*.
2. Volume is capped at ``0.05`` lots and defaults to the symbol's minimum.
3. A server-side stop loss (≈2% away) is attached to every order.
4. The position is closed before returning; any leftover triggers a
   loud failure so the user can close it manually.

This module runs on the CLI thread and blocks — it is never used by the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.mt5.errors import TradeError
from app.mt5.gateway import MT5Gateway
from app.mt5.models import FillingConstants, SymbolSnapshot
from app.observability.logger import get_logger

log = get_logger("mt5")

#: Bot identity on the demo order (visible in the terminal's comment field).
DEMO_TEST_MAGIC = 20260926
DEMO_TEST_COMMENT = "workstation demo-trade-test"

#: Hard cap for the test order, even if the user passes a bigger value.
MAX_TEST_VOLUME = 0.05
#: Stop-loss distance as a fraction of entry price (guard against runaway).
SL_DISTANCE_FRACTION = 0.02
#: Order type constant for BUY — mirrors MetaTrader5.ORDER_TYPE_BUY.
_ORDER_TYPE_BUY = 0
_ORDER_TYPE_SELL = 1
_ACTION_DEAL = 1


class DemoGuardError(Exception):
    """The trade test refuses to run (not demo / unsafe parameters)."""


@dataclass(slots=True)
class DemoTradeOutcome:
    """Result of one demo trade test run."""

    opened: bool = False
    closed: bool = False
    order_ticket: int = 0
    position_ticket: int = 0
    volume: float = 0.0
    entry_price: float = 0.0
    exit_price: float = 0.0
    symbol: str = ""
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []

    @property
    def ok(self) -> bool:
        return self.opened and self.closed

    def summary(self) -> str:
        if self.ok:
            return (
                f"demo trade test passed: {self.symbol} {self.volume} lots "
                f"opened at {self.entry_price:.5f} and closed at {self.exit_price:.5f}"
            )
        if self.opened:
            return (
                f"demo trade test INCOMPLETE: position {self.position_ticket} on "
                f"{self.symbol} is still open — close it manually in the terminal"
            )
        return "demo trade test failed before any order was sent"


def _select_fill_policy(symbol: SymbolSnapshot, constants: FillingConstants) -> int:
    """Pick an allowed filling mode from the symbol's bitmask.

    Falls back to RETURN when neither FOK nor IOC is advertised; on
    Instant/Market-execution retail brokers that request can be rejected
    with retcode 10030 — acceptable for a demo-only smoke test.
    """
    fill_mode = symbol.fill_mode
    if fill_mode & 2:
        return constants.order_filling_ioc
    if fill_mode & 1:
        return constants.order_filling_fok
    return constants.order_filling_return


def _validate_volume(symbol: SymbolSnapshot, requested: float | None) -> float:
    """Resolve and validate the order volume against broker rules."""
    step = symbol.volume_step or 0.01
    minimum = symbol.volume_min or step
    volume = minimum if requested is None else requested
    if volume > MAX_TEST_VOLUME:
        raise DemoGuardError(f"volume {volume} exceeds the demo test cap of {MAX_TEST_VOLUME} lots")
    if volume < minimum:
        raise DemoGuardError(f"volume {volume} is below the broker minimum of {minimum}")
    steps = round(volume / step)
    if abs(steps * step - volume) > 1e-8:
        raise DemoGuardError(f"volume {volume} is not a multiple of the broker step {step}")
    return round(steps * step, 8)


def _round_price(price: float, digits: int) -> float:
    return round(price, digits or 5)


def _base_request(
    symbol: SymbolSnapshot, constants: FillingConstants, volume: float
) -> dict[str, Any]:
    return {
        "action": constants.trade_action_deal,
        "symbol": symbol.name,
        "volume": volume,
        "deviation": 50,
        "magic": DEMO_TEST_MAGIC,
        "comment": DEMO_TEST_COMMENT,
        "type_time": constants.order_time_gtc,
        "type_filling": _select_fill_policy(symbol, constants),
    }


def _find_position(gateway: MT5Gateway, order_ticket: int) -> tuple[Any, ...]:
    positions = gateway.wait_for_result(gateway.positions(), "positions", 15.0)
    return tuple(p for p in positions if p.ticket == order_ticket or p.identifier == order_ticket)


def run_demo_trade_test(
    gateway: MT5Gateway,
    broker_symbol: str,
    *,
    volume: float | None = None,
    timeout_s: float = 20.0,
) -> DemoTradeOutcome:
    """Open and immediately close one minimal order on a DEMO account.

    ``gateway`` must already be connected. Raises :class:`DemoGuardError`
    for non-demo accounts before anything is sent to the broker.
    """
    outcome = DemoTradeOutcome(symbol=broker_symbol)

    # -- guard: demo only ------------------------------------------------------
    account = gateway.wait_for_result(gateway.account_info(), "account_info", timeout_s)
    if not account.is_demo:
        raise DemoGuardError(
            f"REFUSED: account {account.login} is a {account.mode_name.upper()} account. "
            "The trade test only runs on demo accounts."
        )
    outcome.notes.append(f"account ***{str(account.login)[-3:]} ({account.mode_name})")

    # -- symbol metadata ---------------------------------------------------------
    symbol = gateway.wait_for_result(gateway.symbol_info(broker_symbol), "symbol_info", timeout_s)
    volume = _validate_volume(symbol, volume)
    outcome.volume = volume
    outcome.notes.append(
        f"volume {volume} (min {symbol.volume_min}, step {symbol.volume_step}), "
        f"digits {symbol.digits}"
    )

    constants = gateway.wait_for_result(gateway.filling_constants(), "filling_constants", timeout_s)
    tick = gateway.wait_for_result(gateway.tick(broker_symbol), "tick", timeout_s)
    if tick.ask <= 0:
        raise DemoGuardError(f"no valid ask price for {broker_symbol} — market closed?")

    request = _base_request(symbol, constants, volume)
    request.update(
        type=_ORDER_TYPE_BUY,
        price=_round_price(tick.ask, symbol.digits),
        sl=_round_price(tick.ask * (1 - SL_DISTANCE_FRACTION), symbol.digits),
        tp=0.0,
    )

    # -- validate against the server first ---------------------------------------
    gateway.wait_for_result(gateway.order_check(request), "order_check", timeout_s)
    outcome.notes.append("order_check passed")

    # -- open -----------------------------------------------------------------------
    result = gateway.wait_for_result(gateway.order_send(request), "order_send", timeout_s)
    if not result.succeeded:
        raise TradeError(result.retcode, result.comment)
    outcome.opened = True
    outcome.order_ticket = result.order_ticket
    outcome.entry_price = result.price or tick.ask
    outcome.notes.append(f"opened order #{result.order_ticket} at {outcome.entry_price}")
    log.info("demo-trade-test: opened #{} on {}", result.order_ticket, broker_symbol)

    # -- close ---------------------------------------------------------------------
    matching = _find_position(gateway, result.order_ticket)
    if not matching:
        outcome.notes.append("position vanished immediately (possible instant SL/TP)")
        log.warning("demo-trade-test: no position found for order {}", result.order_ticket)
        return outcome
    position = matching[0]
    outcome.position_ticket = position.ticket

    close_request = _base_request(symbol, constants, position.volume)
    close_request.update(
        type=_ORDER_TYPE_SELL,
        position=position.ticket,
        price=_round_price(tick.bid, symbol.digits),
        sl=0.0,
        tp=0.0,
    )
    close_result = gateway.wait_for_result(
        gateway.order_send(close_request), "order_send", timeout_s
    )
    if not close_result.succeeded:
        log.error(
            "demo-trade-test: close failed retcode={} — position still open", close_result.retcode
        )
        raise TradeError(close_result.retcode, close_result.comment or "close failed")
    outcome.closed = True
    outcome.exit_price = close_result.price
    outcome.notes.append(f"closed position #{position.ticket} at {close_result.price}")

    # -- verify nothing is left ---------------------------------------------------------
    leftover = _find_position(gateway, result.order_ticket)
    if leftover:
        log.error("demo-trade-test: position {} still present after close", result.order_ticket)
        return outcome

    log.info("demo-trade-test: completed {}", outcome.summary())
    return outcome
