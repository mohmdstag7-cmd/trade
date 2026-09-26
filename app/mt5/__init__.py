"""MT5 integration layer (SPEC C3, C7, C13).

Everything that touches the MetaTrader5 package lives in this package:

- :class:`app.mt5.gateway.MT5Gateway` — the single-threaded command queue.
- :mod:`app.mt5.errors` — error taxonomy with friendly bilingual messages.
- :mod:`app.mt5.credentials` — Windows Credential Manager storage.
- :mod:`app.mt5.symbols` — broker suffix resolution.
- :mod:`app.mt5.diagnostics` — smoke test + UI connection probe.
- :mod:`app.mt5.demo_trade_test` — demo-only order path test.

Nothing outside this package imports ``MetaTrader5`` directly; the domain
stays broker-independent (SPEC C2).
"""

from app.mt5.credentials import CredentialStore, CredentialStoreError
from app.mt5.errors import (
    AuthError,
    ConnectionLostError,
    MT5Error,
    TerminalError,
    TradeError,
    friendly_message,
)
from app.mt5.gateway import MT5Gateway
from app.mt5.models import (
    AccountSnapshot,
    ConnectionState,
    ConnectRequest,
    OrderResultSnapshot,
    PositionSnapshot,
    RateBar,
    SymbolSnapshot,
    TerminalSnapshot,
    TickSnapshot,
)

__all__ = [
    "AccountSnapshot",
    "AuthError",
    "ConnectRequest",
    "ConnectionLostError",
    "ConnectionState",
    "CredentialStore",
    "CredentialStoreError",
    "MT5Error",
    "MT5Gateway",
    "OrderResultSnapshot",
    "PositionSnapshot",
    "RateBar",
    "SymbolSnapshot",
    "TerminalError",
    "TerminalSnapshot",
    "TickSnapshot",
    "TradeError",
    "friendly_message",
]
