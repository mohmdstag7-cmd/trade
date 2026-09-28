"""MT5 error taxonomy with friendly bilingual messages (SPEC C7).

The MetaTrader5 package reports failures through two channels:

1. ``last_error()`` codes returned by terminal-level calls such as
   ``initialize`` / ``login`` (e.g. ``-6`` = authorization failed).
2. Trade retcodes inside ``order_send`` / ``order_check`` results
   (e.g. ``10019`` = not enough money).

This module maps both onto a small exception hierarchy. Every exception
carries the raw code, the raw description and a *friendly* English and
Persian sentence a non-developer can act on. Friendly messages are plain
data (no UI imports) so they are usable from CLI diagnostics as well.

Logging must never include the password; the gateway logs error *codes*
only. Account numbers are not secrets on their own but are masked at the
call site (see :mod:`app.mt5.diagnostics`).
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Terminal-level codes (returned via mt5.last_error())
# ---------------------------------------------------------------------------
TERMINAL_CODE_AUTH_FAILED = -6
TERMINAL_CODE_CONNECT_FAILED = -5
TERMINAL_CODE_NO_IPC = -4
TERMINAL_CODE_INIT_FAILED = -3
TERMINAL_CODE_INVALID_PARAMS = -2
TERMINAL_CODE_INTERNAL = -1

#: Friendly, actionable messages keyed by terminal-level error code.
TERMINAL_MESSAGES: dict[int, tuple[str, str]] = {
    TERMINAL_CODE_AUTH_FAILED: (
        "Login failed: check the account number, password and server name "
        "(e.g. 'MetaQuotes-Demo'). The password is NOT your investor password.",
        "ورود ناموفق بود: شماره حساب، رمز عبور و نام سرور را بررسی کنید "
        "(مثلاً «MetaQuotes-Demo»). این رمز نباید رمز Investor باشد.",
    ),
    TERMINAL_CODE_CONNECT_FAILED: (
        "Could not reach the MT5 terminal. Make sure MetaTrader 5 is installed, "
        "running and logged in to the correct broker, then try again.",
        "اتصال به ترمینال MT5 برقرار نشد. مطمئن شوید متاتریدر ۵ نصب و اجرا شده "
        "و به بروکر مورد نظر وارد شده است، سپس دوباره تلاش کنید.",
    ),
    TERMINAL_CODE_NO_IPC: (
        "Could not talk to the MT5 terminal (IPC). Close and start the terminal "
        "fresh, and make sure no other bot/script is attached to it.",
        "ارتباط با ترمینال MT5 برقرار نشد (IPC). ترمینال را ببندید و دوباره باز کنید؛ "
        "مطمئن شوید ربات یا اسکریپت دیگری به آن وصل نیست.",
    ),
    TERMINAL_CODE_INIT_FAILED: (
        "MT5 terminal failed to start. Reinstalling the terminal usually fixes this.",
        "اجرای ترمینال MT5 ناموفق بود. معمولاً نصب مجدد ترمینال این مشکل را حل می‌کند.",
    ),
    TERMINAL_CODE_INVALID_PARAMS: (
        "Invalid connection parameters (terminal path, login or server).",
        "پارامترهای اتصال نامعتبرند (مسیر ترمینال، شماره حساب یا سرور).",
    ),
    TERMINAL_CODE_INTERNAL: (
        "MT5 internal error. Restart the terminal; if it persists, restart Windows.",
        "خطای داخلی MT5. ترمینال را ری‌استارت کنید؛ اگر ادامه داشت ویندوز را ری‌استارت کنید.",
    ),
}

# ---------------------------------------------------------------------------
# Trade retcodes (100xx) — official MetaTrader 5 return codes
# ---------------------------------------------------------------------------
RETCODE_REQUOTE = 10004
RETCODE_REJECT = 10006
RETCODE_CANCEL = 10007
RETCODE_PLACED = 10008
RETCODE_DONE = 10009
RETCODE_DONE_PARTIAL = 10010
RETCODE_ERROR = 10011
RETCODE_TIMEOUT = 10012
RETCODE_INVALID = 10013
RETCODE_INVALID_VOLUME = 10014
RETCODE_INVALID_PRICE = 10015
RETCODE_INVALID_STOPS = 10016
RETCODE_TRADE_DISABLED = 10017
RETCODE_MARKET_CLOSED = 10018
RETCODE_NO_MONEY = 10019
RETCODE_PRICE_CHANGED = 10020
RETCODE_PRICE_OFF = 10021
RETCODE_INVALID_EXPIRATION = 10022
RETCODE_ORDER_CHANGED = 10023
RETCODE_TOO_MANY_REQUESTS = 10024
RETCODE_NO_CHANGES = 10025
RETCODE_SERVER_DISABLES_AT = 10026
RETCODE_CLIENT_DISABLES_AT = 10027
RETCODE_LOCKED = 10028
RETCODE_FROZEN = 10029
RETCODE_INVALID_FILL = 10030
RETCODE_CONNECTION = 10031
RETCODE_ONLY_REAL = 10032
RETCODE_LIMIT_ORDERS = 10033
RETCODE_LIMIT_VOLUME = 10034
RETCODE_INVALID_ORDER = 10035
RETCODE_POSITION_CLOSED = 10036
RETCODE_INVALID_CLOSE_VOLUME = 10037
RETCODE_CLOSE_ORDER_EXIST = 10038
RETCODE_LIMIT_POSITIONS = 10039
RETCODE_REJECT_CANCEL = 10040
RETCODE_LONG_ONLY = 10041
RETCODE_SHORT_ONLY = 10042
RETCODE_CLOSE_ONLY = 10043
RETCODE_FIFO_CLOSE = 10044

#: Friendly, actionable messages keyed by trade retcode.
RETCODE_MESSAGES: dict[int, tuple[str, str]] = {
    RETCODE_REQUOTE: (
        "Price moved before the order could be filled (requote). Retry is safe.",
        "قیمت پیش از اجرای سفارش تغییر کرد (Requote). تلاش مجدد بی‌خطر است.",
    ),
    RETCODE_REJECT: (
        "The broker rejected the order. Check the symbol's trading hours and rules.",
        "بروکر سفارش را رد کرد. ساعات معامله و قوانین نماد را بررسی کنید.",
    ),
    RETCODE_TIMEOUT: (
        "The broker did not answer in time. The order state is unknown — check Positions.",
        "بروکر به‌موقع پاسخ نداد. وضعیت سفارش نامشخص است — پوزیشن‌ها را بررسی کنید.",
    ),
    RETCODE_INVALID_VOLUME: (
        "Invalid volume. Use the broker's min/step volume shown on the symbol card.",
        "حجم نامعتبر است. از حداقل و گام حجم مجاز بروکر استفاده کنید.",
    ),
    RETCODE_INVALID_PRICE: (
        "Invalid price for this order type.",
        "قیمت برای این نوع سفارش نامعتبر است.",
    ),
    RETCODE_INVALID_STOPS: (
        "Stop loss / take profit is too close to the price. Widen the levels.",
        "حد ضرر / حد سود بیش از حد نزدیک به قیمت است. سطوح را بازتر کنید.",
    ),
    RETCODE_TRADE_DISABLED: (
        "Trading is disabled for this account or symbol by the broker.",
        "معامله برای این حساب یا نماد توسط بروکر غیرفعال است.",
    ),
    RETCODE_MARKET_CLOSED: (
        "The market is closed for this symbol right now.",
        "بازار این نماد در این لحظه بسته است.",
    ),
    RETCODE_NO_MONEY: (
        "Not enough free margin for this order. Reduce the volume or risk.",
        "حاشیه آزادی برای این سفارش کافی نیست. حجم یا ریسک را کاهش دهید.",
    ),
    RETCODE_PRICE_OFF: (
        "No quotes available for this symbol (price off).",
        "برای این نماد قیمت فعالی وجود ندارد.",
    ),
    RETCODE_INVALID_FILL: (
        "This broker does not accept the requested fill policy (FOK/IOC/Return).",
        "این بروکر سیاست پرشدهٔ درخواستی (FOK/IOC/Return) را نمی‌پذیرد.",
    ),
    RETCODE_CONNECTION: (
        "Lost connection to the broker trade server. The gateway will reconnect.",
        "ارتباط با سرور معاملاتی بروکر قطع شد. گیت‌وی دوباره وصل خواهد شد.",
    ),
    RETCODE_LIMIT_ORDERS: (
        "Order limit reached for this account.",
        "به سقف تعداد سفارش‌های مجاز این حساب رسیده‌اید.",
    ),
    RETCODE_LIMIT_VOLUME: (
        "Volume limit reached for this account.",
        "به سقف حجم مجاز این حساب رسیده‌اید.",
    ),
    RETCODE_POSITION_CLOSED: (
        "The position was already closed (possibly by SL/TP).",
        "پوزیشن قبلاً بسته شده است (احتمالاً با حد ضرر یا حد سود).",
    ),
    RETCODE_CLOSE_ONLY: (
        "The account or symbol is in close-only mode; new entries are blocked.",
        "حساب یا نماد در حالت «فقط بستن» است؛ ورود جدید مسدود است.",
    ),
}

#: Retcodes that mean the request *may* have succeeded or partially succeeded.
AMBIGUOUS_RETCODES: frozenset[int] = frozenset(
    {RETCODE_TIMEOUT, RETCODE_PRICE_CHANGED, RETCODE_REQUOTE}
)


def friendly_message(code: int, raw_description: str = "", *, lang: str = "en") -> str:
    """Return an actionable message for a terminal code or trade retcode.

    Falls back to a generic message that always includes the raw code and
    description, so nothing is lost for unknown errors.
    """
    table = TERMINAL_MESSAGES if code < 0 else RETCODE_MESSAGES
    entry = table.get(code)
    if entry is not None:
        return entry[0] if lang == "en" else entry[1]
    if raw_description:
        return f"MT5 error {code}: {raw_description}"
    return f"MT5 error code {code}. See the MetaTrader 5 documentation."


class MT5Error(Exception):
    """Base class for every MT5-related failure."""

    def __init__(self, code: int, description: str = "", *, detail: str = "") -> None:
        self.code = code
        self.description = description
        self.detail = detail
        super().__init__(friendly_message(code, description))


class TerminalError(MT5Error):
    """Terminal-level failure (initialize / login / unreachable terminal)."""


class AuthError(TerminalError):
    """Wrong login, password or server (terminal code -6)."""


class ConnectionLostError(MT5Error):
    """The terminal connection dropped mid-session (retcode 10031 / -5)."""


class TradeError(MT5Error):
    """Order was rejected or failed (trade retcode)."""

    def __init__(
        self, code: int, description: str = "", *, detail: str = "", ambiguous: bool = False
    ) -> None:
        self.ambiguous = ambiguous
        super().__init__(code, description, detail=detail)


class InvalidSymbolError(TerminalError):
    """The requested symbol does not exist on this broker.

    Terminal-classed so legacy ``except TerminalError`` handlers keep
    working; keeps ``symbol`` for log-friendly rendering.
    """

    def __init__(self, symbol: str, *, code: int = 0, description: str = "") -> None:
        self.symbol = symbol
        super().__init__(code, description or f"symbol not found on this broker: {symbol}")


class GatewayTimeoutError(MT5Error):
    """A gateway command did not finish within its timeout."""

    #: Shown when a *connect* outlives its timeout: a cold terminal start
    #  (terminal64.exe launching, broker login servers warming up) routinely
    #  takes a minute — the user should retry, not assume the app is broken.
    _CONNECT_HINT = " — the terminal may still be starting up; wait a moment and try again"

    def __init__(self, operation: str, timeout_s: float) -> None:
        self.operation = operation
        self.timeout_s = timeout_s
        description = f"{operation} timed out after {timeout_s:.1f}s"
        if operation == "connect":
            description += self._CONNECT_HINT
        super().__init__(code=0, description=description)


def _result_code(result: Any) -> int:
    """Extract a retcode from an order result object (real or fake)."""
    code = getattr(result, "retcode", None)
    return int(code) if isinstance(code, (int, float)) else 0


def _result_comment(result: Any) -> str:
    return str(getattr(result, "comment", "") or "")


def raise_for_order_result(result: Any, module: Any) -> None:
    """Raise :class:`TradeError` unless the order result is a full success.

    ``RETCODE_PLACED`` counts as success for pending orders. Partial fills
    (``RETCODE_DONE_PARTIAL``) are reported as success with ``detail`` —
    the execution engine (Phase 7+) tracks remaining volume itself.
    """
    code = _result_code(result)
    if code in (RETCODE_DONE, RETCODE_PLACED, RETCODE_DONE_PARTIAL):
        return
    raw = _result_comment(result) or _last_error_text(module)
    ambiguous = code in AMBIGUOUS_RETCODES
    raise TradeError(code, raw, ambiguous=ambiguous)


def raise_for_check_result(result: Any, module: Any) -> None:
    """Raise :class:`TradeError` unless an ``order_check`` result passes.

    ``order_check`` mirrors ``MqlTradeCheckResult``, whose success is
    commonly reported with retcode ``0`` ("check passed") rather than the
    ``TRADE_RETCODE_*`` values ``order_send`` returns. Accept both so a
    valid order is not rejected on real terminals (the exact retcode for
    success varies by build/broker — 0 and 10009 are both observed).
    """
    code = _result_code(result)
    if code in (0, RETCODE_DONE, RETCODE_PLACED, RETCODE_DONE_PARTIAL):
        return
    raw = _result_comment(result) or _last_error_text(module)
    ambiguous = code in AMBIGUOUS_RETCODES
    raise TradeError(code, raw, ambiguous=ambiguous)


def _last_error_text(module: Any) -> str:
    try:
        code, description = module.last_error()
        return str(description or f"code {code}")
    except Exception:  # pragma: no cover - defensive, never mask the real error
        return ""
