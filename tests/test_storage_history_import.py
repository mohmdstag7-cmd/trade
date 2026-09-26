"""History import tests: gateway deal fetch + idempotent trade import."""

from __future__ import annotations

import pathlib

import pytest

from app.mt5.gateway import MT5Gateway
from app.mt5.models import (
    DEAL_ENTRY_IN,
    DEAL_ENTRY_OUT,
    ConnectionState,
    ConnectRequest,
)
from app.storage.db import Database
from app.storage.history_import import (
    HistoryImporter,
    ImportStats,
    build_trade_row,
    epoch_to_iso,
)
from app.storage.migrations import MigrationRunner
from app.storage.repositories import OutboxRepository, TradeRepository
from tests.fakes.fake_mt5 import FakeMetaTrader5, FakeSymbolConfig, _Deal


def make_request() -> ConnectRequest:
    return ConnectRequest(
        login=12345678,
        password="secret-password",
        server="FakeServer-Demo",
        terminal_path="",
    )  # type: ignore[arg-type]


def make_deal(
    ticket: int,
    *,
    entry: int = DEAL_ENTRY_OUT,
    type_: int = 0,
    position_id: int | None = None,
    time: int = 1_760_000_000,
    symbol: str = "EURUSD",
    profit: float = 25.0,
    commission: float = -1.5,
    swap: float = 0.0,
    volume: float = 0.10,
    price: float = 1.0850,
    magic: int = 700100,
) -> _Deal:
    return _Deal(
        ticket=ticket,
        order=ticket + 1,
        time=time,
        time_msc=time * 1000,
        type=type_,
        entry=entry,
        magic=magic,
        position_id=position_id if position_id is not None else ticket // 2,
        symbol=symbol,
        volume=volume,
        price=price,
        commission=commission,
        swap=swap,
        profit=profit,
        comment="history",
    )


@pytest.fixture
def db(tmp_path: pathlib.Path) -> Database:
    database = Database(tmp_path / "w.db")
    MigrationRunner(database).run_all()
    yield database
    database.close_all()


@pytest.fixture
def trades(db: Database) -> TradeRepository:
    return TradeRepository(db)


class TestEpochToIso:
    def test_format(self) -> None:
        assert epoch_to_iso(0) == "1970-01-01T00:00:00.000Z"

    def test_millisecond_precision(self) -> None:
        stamp = epoch_to_iso(1_760_000_123)
        assert stamp.endswith("Z") and "." in stamp


class TestBuildTradeRow:
    def test_closing_buy_deal_means_sell_position(self) -> None:
        # DEAL_TYPE_BUY (0) closing deal → the position was a SELL.
        row = build_trade_row(make_deal(1, type_=0, profit=10.0, commission=-1.0))
        assert row["direction"] == "sell"
        assert row["net_profit"] == 9.0
        assert row["outcome"] == "win"
        assert row["source"] == "import"

    def test_loss_row(self) -> None:
        row = build_trade_row(make_deal(2, type_=1, profit=-30.0))
        assert row["direction"] == "buy"
        assert row["outcome"] == "loss"

    def test_breakeven_row(self) -> None:
        row = build_trade_row(make_deal(3, profit=0.0, commission=0.0, swap=0.0))
        assert row["outcome"] == "breakeven"


class TestGatewayHistoryDeals:
    def test_fetch_deals_through_gateway(self) -> None:
        deal = make_deal(42, time=1_760_000_000)
        fake = FakeMetaTrader5(symbols=[FakeSymbolConfig("EURUSD")], deals=[deal])
        gateway = MT5Gateway(mt5_factory=lambda: fake, request_timeout_s=5.0)
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            assert gateway.state == ConnectionState.CONNECTED
            deals = gateway.wait_for_result(
                gateway.history_deals(1_760_000_000 - 10, 1_760_000_100),
                "history_deals",
            )
            assert len(deals) == 1
            assert deals[0].ticket == 42
            assert deals[0].closes_position is True
        finally:
            gateway.stop()

    def test_date_range_filtering(self) -> None:
        early = make_deal(1, time=1_760_000_000)
        late = make_deal(2, time=1_760_100_000)
        fake = FakeMetaTrader5(symbols=[FakeSymbolConfig("EURUSD")], deals=[early, late])
        gateway = MT5Gateway(mt5_factory=lambda: fake, request_timeout_s=5.0)
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            deals = gateway.wait_for_result(
                gateway.history_deals(1_760_050_000, 1_760_200_000), "history_deals"
            )
            assert [d.ticket for d in deals] == [2]
        finally:
            gateway.stop()

    def test_deal_conversion_fields(self) -> None:
        deal = make_deal(7, profit=12.5, commission=-0.7, swap=0.3, volume=0.05, price=2401.5)
        fake = FakeMetaTrader5(symbols=[FakeSymbolConfig("EURUSD")], deals=[deal])
        gateway = MT5Gateway(mt5_factory=lambda: fake, request_timeout_s=5.0)
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            deals = gateway.wait_for_result(
                gateway.history_deals(0, 2_000_000_000), "history_deals"
            )
            fetched = deals[0]
            assert fetched.volume == 0.05
            assert fetched.price == 2401.5
            assert fetched.profit == 12.5
            assert fetched.commission == -0.7
            assert fetched.swap == 0.3
            assert fetched.symbol == "EURUSD"
        finally:
            gateway.stop()


class TestHistoryImporter:
    def test_import_closes_only(self, db: Database, trades: TradeRepository) -> None:
        opening = make_deal(10, entry=DEAL_ENTRY_IN, position_id=5)
        closing = make_deal(11, entry=DEAL_ENTRY_OUT, position_id=5)
        orphan = make_deal(12, position_id=0)  # no position → skipped
        fake = FakeMetaTrader5(
            symbols=[FakeSymbolConfig("EURUSD")],
            deals=[opening, closing, orphan],
        )
        gateway = MT5Gateway(mt5_factory=lambda: fake, request_timeout_s=5.0)
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            importer = HistoryImporter(gateway, trades)
            stats = importer.import_closed_deals(since_epoch=0, until_epoch=2_000_000_000)
        finally:
            gateway.stop()

        assert stats == ImportStats(fetched=3, imported=1, skipped=2)
        assert trades.count() == 1
        row = trades.recent(1)[0]
        assert row["ticket"] == 11
        assert row["position_id"] == 5

    def test_reimport_is_duplicate_free(self, db: Database, trades: TradeRepository) -> None:
        """Overlapping ranges re-import the same deals exactly once (G3-4 ✓)."""
        deal = make_deal(20, position_id=9)
        fake = FakeMetaTrader5(symbols=[FakeSymbolConfig("EURUSD")], deals=[deal])
        gateway = MT5Gateway(mt5_factory=lambda: fake, request_timeout_s=5.0)
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            importer = HistoryImporter(gateway, trades)
            first = importer.import_closed_deals(since_epoch=0, until_epoch=2_000_000_000)
            second = importer.import_closed_deals(since_epoch=0, until_epoch=2_000_000_000)
        finally:
            gateway.stop()
        assert first.imported == 1
        assert second.imported == 0
        assert second.skipped == 1
        assert trades.count() == 1
        # mirrored exactly once
        outbox_rows = db.query("SELECT row_id FROM outbox WHERE table_name = 'trades'")
        assert len(outbox_rows) == 1

    def test_imported_row_enqueued_for_mirror(self, db: Database, trades: TradeRepository) -> None:
        fake = FakeMetaTrader5(
            symbols=[FakeSymbolConfig("EURUSD")], deals=[make_deal(30, position_id=3)]
        )
        gateway = MT5Gateway(mt5_factory=lambda: fake, request_timeout_s=5.0)
        gateway.start()
        try:
            gateway.wait_for_result(gateway.connect(make_request()), "connect")
            HistoryImporter(gateway, trades).import_closed_deals(
                since_epoch=0, until_epoch=2_000_000_000
            )
        finally:
            gateway.stop()
        counts = OutboxRepository(db).counts()
        assert counts.get("pending") == 1

    def test_stats_summary_format(self) -> None:
        stats = ImportStats(fetched=5, imported=3, skipped=2)
        assert stats.summary() == "fetched=5 imported=3 skipped=2"
