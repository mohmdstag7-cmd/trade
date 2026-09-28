"""History import — turn closed MT5 deals into trade rows (SPEC G3-4).

Each closing deal (OUT / INOUT / OUT_BY) becomes one ``trades`` row with a
DETERMINISTIC id derived from (ticket, position_id), so re-running the
import — or importing overlapping date ranges after an offline period —
can never duplicate a trade (SPEC G3-4 acceptance). Rows land in SQLite
and the outbox in one transaction, mirroring to Supabase automatically.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.mt5.gateway import MT5Gateway
from app.observability.logger import get_logger
from app.storage.repositories import TradeRepository

log = get_logger("sync")


def epoch_to_iso(epoch_seconds: int) -> str:
    """UTC epoch seconds → ISO-8601 with milliseconds and Z suffix."""
    stamp = dt.datetime.fromtimestamp(int(epoch_seconds), tz=dt.UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass(slots=True)
class ImportStats:
    """Result summary for the UI / CLI."""

    fetched: int = 0
    imported: int = 0
    skipped: int = 0

    def summary(self) -> str:
        return f"fetched={self.fetched} imported={self.imported} skipped={self.skipped}"


def build_trade_row(deal: object) -> dict[str, object]:
    """Convert a closing deal into a trade row (pure, testable)."""
    # The closing deal's side is opposite to the position's direction.
    deal_type = int(getattr(deal, "type", -1))
    if deal_type == 0:
        direction = "sell"
    elif deal_type == 1:
        direction = "buy"
    else:
        msg = f"unexpected deal type {deal_type!r} (expected 0=BUY or 1=SELL)"
        raise ValueError(msg)
    net = float(deal.profit) + float(deal.commission) + float(deal.swap)  # type: ignore[attr-defined]
    outcome = "win" if net > 0 else "loss" if net < 0 else "breakeven"
    return {
        "source": "import",
        "mode": "live",
        "ticket": int(deal.ticket),  # type: ignore[attr-defined]
        "position_id": int(deal.position_id),  # type: ignore[attr-defined]
        "magic": int(deal.magic),  # type: ignore[attr-defined]
        "symbol": str(deal.symbol),  # type: ignore[attr-defined]
        "direction": direction,
        "volume": float(deal.volume),  # type: ignore[attr-defined]
        "close_time": epoch_to_iso(int(deal.time)),  # type: ignore[attr-defined]
        "close_price": float(deal.price),  # type: ignore[attr-defined]
        "profit": float(deal.profit),  # type: ignore[attr-defined]
        "commission": float(deal.commission),  # type: ignore[attr-defined]
        "swap": float(deal.swap),  # type: ignore[attr-defined]
        "net_profit": net,
        "outcome": outcome,
        "exit_reason": "history_import",
        "comment": str(deal.comment),  # type: ignore[attr-defined]
    }


class HistoryImporter:
    """Fetches deals through the gateway and imports them idempotently."""

    def __init__(
        self, gateway: MT5Gateway, repo: TradeRepository, *, timeout_s: float = 30.0
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._timeout_s = timeout_s

    def import_closed_deals(
        self, *, since_epoch: int, until_epoch: int | None = None
    ) -> ImportStats:
        """Import closing deals in ``[since, until)``; returns counters."""
        import time

        until = int(until_epoch) if until_epoch is not None else int(time.time())
        deals = self._gateway.wait_for_result(
            self._gateway.history_deals(int(since_epoch), until),
            "history_deals",
            self._timeout_s,
        )
        stats = ImportStats(fetched=len(deals))
        for deal in deals:
            if not deal.closes_position or not deal.symbol or deal.position_id == 0:
                stats.skipped += 1
                continue
            try:
                row = build_trade_row(deal)
            except ValueError as exc:
                log.warning("storage: skipping deal with unexpected type: {}", exc)
                stats.skipped += 1
                continue
            if self._repo.import_row(row):
                stats.imported += 1
            else:
                stats.skipped += 1
        log.info(
            "storage: history import {} (since={} until={})",
            stats.summary(),
            since_epoch,
            until,
        )
        return stats
