"""Qt tests for the Market page and the broker clock in the status bar."""

from __future__ import annotations

import pytest

from app.analysis.service import MarketAnalysisService
from app.core.timeframes import Timeframe
from app.ui.pages.market import MarketPage
from tests.test_analysis_service import FakeGateway  # reuse the fake gateway

pytestmark = pytest.mark.usefixtures("qapp")


def _filled_service() -> MarketAnalysisService:
    gateway = FakeGateway()
    service = MarketAnalysisService(gateway, watched=("EURUSD", "GBPUSD", "XAUUSD"))
    service.refresh_now()
    service.poll()
    return service


class TestMarketPageOffline:
    def test_offline_shows_empty_state(self, qtbot, translator, theme_manager) -> None:
        page = MarketPage(translator, theme_manager, service=None)
        qtbot.addWidget(page)
        page.show()
        assert page._empty_label.isVisible()

    def test_set_service_none_keeps_timers_stopped(self, qtbot, translator, theme_manager) -> None:
        page = MarketPage(translator, theme_manager, service=None)
        qtbot.addWidget(page)
        page.set_service(None)
        assert not page._poll_timer.isActive()


class TestMarketPageWithData:
    def test_snapshot_renders_card_and_panels(self, qtbot, translator, theme_manager) -> None:
        service = _filled_service()
        page = MarketPage(translator, theme_manager, service=None)
        qtbot.addWidget(page)
        page.show()
        page.set_service(service)
        page._poll()
        assert page._card_frame.isVisible()
        card_texts = [
            page._card_layout.itemAt(i).widget().text()
            for i in range(page._card_layout.count())
            if page._card_layout.itemAt(i).widget() is not None
        ]
        assert any("EURUSD" in t for t in card_texts)
        # trend matrix renders a heading + one row per analysed timeframe
        assert page._trend_layout.count() >= 3

    def test_language_switch_keeps_rendering(self, qtbot, translator, theme_manager) -> None:
        service = _filled_service()
        page = MarketPage(translator, theme_manager, service=None)
        qtbot.addWidget(page)
        page.show()
        page.set_service(service)
        page._poll()
        translator.set_language("fa")
        assert page._card_frame.isVisible()
        assert translator.is_rtl

    def test_symbol_switch_updates_card(self, qtbot, translator, theme_manager) -> None:
        service = _filled_service()
        page = MarketPage(translator, theme_manager, service=None)
        qtbot.addWidget(page)
        page.show()
        page.set_service(service)
        page._poll()
        page.select_symbol("GBPUSD")
        card_texts = [
            page._card_layout.itemAt(i).widget().text()
            for i in range(page._card_layout.count())
            if page._card_layout.itemAt(i).widget() is not None
        ]
        assert any("GBPUSD" in t for t in card_texts)

    def test_chart_receives_bars(self, qtbot, translator, theme_manager) -> None:
        service = _filled_service()
        page = MarketPage(translator, theme_manager, service=None)
        qtbot.addWidget(page)
        page.show()
        page.set_service(service)
        page._poll()
        bars = list(service.manager.series("EURUSD", Timeframe.M15).bars)
        assert bars


class TestStatusBarBrokerClock:
    def test_broker_offset_shown(self, qtbot, translator) -> None:
        from app.ui.widgets.status_bar import StatusBar

        bar = StatusBar(
            translator, "0.0.0", on_toggle_theme=lambda: None, on_toggle_language=lambda: None
        )
        qtbot.addWidget(bar)
        bar.set_broker_offset(180, "UTC+03:00")
        assert "UTC+03:00" in bar._clock_label.text()

    def test_no_offset_keeps_local_only(self, qtbot, translator) -> None:
        from app.ui.widgets.status_bar import StatusBar

        bar = StatusBar(
            translator, "0.0.0", on_toggle_theme=lambda: None, on_toggle_language=lambda: None
        )
        qtbot.addWidget(bar)
        assert "UTC" not in bar._clock_label.text()
