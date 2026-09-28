"""Market page (SPEC G3-5, F3): chart + analysis cards + scanner + calendar.

v2 layout: a horizontal ``QSplitter`` — left = candlestick chart with the
chosen symbol/timeframe, right = a scrollable analysis column (plain-
language card, trend matrix, key levels, scanner, calendar). The splitter
keeps a responsive balance on any window size and the user can drag it.
Symbol and timeframe selectors sit in a toolbar row with spread/broker
badges on the leading edge.

Data flow: the page owns NO analysis logic. A QTimer drives
``MarketAnalysisService.refresh_now()``/``poll()``; the service never
blocks the UI thread (all MT5 touches are gateway futures). Every new
snapshot re-renders the visible widgets. Without a gateway the page shows
its offline empty state.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.analysis.service import MarketAnalysisService, MarketSnapshot, SymbolSnapshot
from app.core.timeframes import Timeframe
from app.ui.i18n.translator import Translator
from app.ui.theme.manager import ThemeManager
from app.ui.widgets.chart import PriceChart

#: Poll cadence (light): ticks/spread/calendar countdowns.
POLL_MS = 5000
#: Slow cadence for re-submitting bar fetches (closed bars arrive per TF).
REFRESH_MS = 60_000

VISIBLE_LEVELS = 8
CHART_BARS = 120
#: Minimum widths for the splitter children (keeps both sides usable).
CHART_MIN_WIDTH = 420
SIDE_MIN_WIDTH = 320


def _card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("Card")
    return frame


def _clear_layout(layout: QLayout) -> None:
    """Remove (and delete) all widgets AND sub-layouts in a layout.

    Sub-layouts previously survived the sweep: their child widgets stayed
    alive (visible, overlapping duplicates) and leaked on every re-render
    of the trend matrix (runtime-verified).
    """
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        sub = item.layout()
        if sub is not None:
            _clear_layout(sub)
            sub.deleteLater()
        spacer = item.spacerItem()
        if spacer is not None:
            layout.removeItem(spacer)


def _card_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("CardTitle")
    return label


class MarketPage(QWidget):
    """The Market & analysis page."""

    def __init__(
        self,
        translator: Translator,
        theme_manager: ThemeManager,
        parent: QWidget | None = None,
        *,
        service: MarketAnalysisService | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._theme = theme_manager
        self._service = service
        self._symbol = "EURUSD"
        self._timeframe = Timeframe.M15
        self._last_snapshot: MarketSnapshot | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # -- symbol + timeframe selector row --------------------------------
        selector_row = QHBoxLayout()
        selector_row.setSpacing(0)
        self._symbol_buttons: dict[str, QPushButton] = {}
        for symbol in ("EURUSD", "GBPUSD", "XAUUSD"):
            btn = QPushButton(symbol)
            btn.setObjectName("SegmentButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, s=symbol: self.select_symbol(s))
            self._symbol_buttons[symbol] = btn
            selector_row.addWidget(btn)
        selector_row.addSpacing(16)
        self._tf_combo = QComboBox()
        for tf in (Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1):
            self._tf_combo.addItem(tf.value, tf)
        self._tf_combo.setCurrentIndex(0)
        self._tf_combo.currentIndexChanged.connect(self._on_tf_changed)
        selector_row.addWidget(self._tf_combo)
        selector_row.addStretch(1)

        self._spread_label = QLabel()
        self._spread_label.setObjectName("Badge")
        selector_row.addWidget(self._spread_label)
        self._broker_label = QLabel()
        self._broker_label.setObjectName("Badge")
        selector_row.addWidget(self._broker_label)
        root.addLayout(selector_row)

        self._empty_label = QLabel()
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        root.addWidget(self._empty_label)

        # -- main split: chart | side panels ---------------------------------
        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(6)

        chart_card = _card()
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(12, 12, 12, 12)
        self._chart = PriceChart(chart_card)
        self._chart.setMinimumHeight(360)
        chart_layout.addWidget(self._chart, 1)
        chart_card.setMinimumWidth(CHART_MIN_WIDTH)
        self._splitter.addWidget(chart_card)

        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        side_widget = QWidget()
        side_widget.setStyleSheet("background: transparent;")
        side_layout = QVBoxLayout(side_widget)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(12)

        self._card_frame = _card()
        self._card_layout = QVBoxLayout(self._card_frame)
        self._card_layout.setContentsMargins(16, 14, 16, 14)
        self._card_layout.setSpacing(6)
        side_layout.addWidget(self._card_frame)

        self._trend_frame = _card()
        self._trend_layout = QVBoxLayout(self._trend_frame)
        self._trend_layout.setContentsMargins(16, 14, 16, 14)
        self._trend_layout.setSpacing(6)
        side_layout.addWidget(self._trend_frame)

        self._levels_frame = _card()
        self._levels_layout = QGridLayout(self._levels_frame)
        self._levels_layout.setContentsMargins(16, 14, 16, 14)
        self._levels_layout.setHorizontalSpacing(14)
        self._levels_layout.setVerticalSpacing(5)
        side_layout.addWidget(self._levels_frame)

        self._scan_frame = _card()
        self._scan_layout = QVBoxLayout(self._scan_frame)
        self._scan_layout.setContentsMargins(16, 14, 16, 14)
        self._scan_layout.setSpacing(6)
        side_layout.addWidget(self._scan_frame)

        self._calendar_frame = _card()
        self._calendar_layout = QVBoxLayout(self._calendar_frame)
        self._calendar_layout.setContentsMargins(16, 14, 16, 14)
        self._calendar_layout.setSpacing(6)
        side_layout.addWidget(self._calendar_frame)
        side_layout.addStretch(1)

        side_scroll.setWidget(side_widget)
        side_scroll.setMinimumWidth(SIDE_MIN_WIDTH)
        self._splitter.addWidget(side_scroll)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([760, 460])
        root.addWidget(self._splitter, 1)

        self._side_panels = [
            self._card_frame,
            self._trend_frame,
            self._levels_frame,
            self._scan_frame,
            self._calendar_frame,
        ]
        self._set_analysis_visible(False)

        # -- timers ------------------------------------------------------------
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(REFRESH_MS)
        self._refresh_timer.timeout.connect(self._refresh)
        # Start immediately when a service was injected via the constructor
        # (the app shell path). Previously the timers ONLY started in
        # set_service(), which production never called — the page rendered
        # once at construction and stayed frozen forever.
        if service is not None:
            self._poll_timer.start()
            self._refresh_timer.start()

        translator.language_changed.connect(lambda _lang: self.retranslate())
        self._theme.theme_changed.connect(lambda _name: self._apply_theme())
        self.retranslate()
        self._apply_theme()

    # -- public ------------------------------------------------------------------
    def set_service(self, service: MarketAnalysisService | None) -> None:
        """Attach (or replace) the analysis service and sync the timers."""
        self._service = service
        self._refresh()
        if service is not None:
            if not self._poll_timer.isActive():
                self._poll_timer.start()
            if not self._refresh_timer.isActive():
                self._refresh_timer.start()
        else:
            self._poll_timer.stop()
            self._refresh_timer.stop()
        self.retranslate()

    def select_symbol(self, symbol: str) -> None:
        self._symbol = symbol
        self._sync_symbol_buttons()
        self._render()

    # -- slots -----------------------------------------------------------------------
    def _on_tf_changed(self, index: int) -> None:
        tf = self._tf_combo.itemData(index)
        if tf is not None:
            self._timeframe = tf
            self._render()

    def _poll(self) -> None:
        if self._service is None:
            return
        snapshot = self._service.poll()
        if snapshot is not None and snapshot is not self._last_snapshot:
            self._last_snapshot = snapshot
            self._render()

    def _refresh(self) -> None:
        if self._service is not None:
            self._service.refresh_now()

    # -- rendering --------------------------------------------------------------------
    def _set_analysis_visible(self, visible: bool) -> None:
        for panel in self._side_panels:
            panel.setVisible(visible)
        self._empty_label.setVisible(not visible)

    def _sync_symbol_buttons(self) -> None:
        for symbol, btn in self._symbol_buttons.items():
            btn.setChecked(symbol == self._symbol)

    def _apply_theme(self) -> None:
        # the chart colors itself via its own token application
        self._chart.apply_theme(self._theme.tokens)

    def _render_empty_hint(self, snapshot: MarketSnapshot | None) -> None:
        """Explain WHY the analysis area is empty (offline vs broker symbols)."""
        tr = self._translator.translate
        base = f"{tr('market.no_data')}\n\n{tr('market.empty.desc')}"
        if snapshot is not None and snapshot.unresolved:
            names = ", ".join(snapshot.unresolved)
            base += f"\n\n{tr('market.empty.unresolved', symbols=names)}"
        self._empty_label.setText(base)

    def _render(self) -> None:
        """Re-render everything from the latest service snapshot."""
        service = self._service
        snapshot = self._last_snapshot or (service.snapshot if service is not None else None)
        snap = snapshot.by_symbol(self._symbol) if snapshot is not None else None
        if snap is None:
            self._set_analysis_visible(False)
            self._chart.set_data([])
            self._render_empty_hint(snapshot)
            return
        self._set_analysis_visible(True)
        self._render_card(snap)
        self._render_trend(snap)
        self._render_levels(snap)
        self._render_scan(snapshot)
        self._render_calendar(snap)
        self._render_badges(snap)
        self._render_chart(snapshot, snap)

    def _render_card(self, snap: SymbolSnapshot) -> None:
        tr = self._translator.translate
        _clear_layout(self._card_layout)
        card = snap.card
        title = QLabel(f"{snap.symbol}  ·  {tr(card.verdict_key())}")
        title.setWordWrap(True)
        title.setObjectName("CardTitle")
        self._card_layout.addWidget(title)
        for line in getattr(card, "lines", ()):
            label = QLabel(tr(line.key, **line.params))
            label.setWordWrap(True)
            label.setObjectName("MutedLabel")
            self._card_layout.addWidget(label)

    def _render_trend(self, snap: SymbolSnapshot) -> None:
        tr = self._translator.translate
        _clear_layout(self._trend_layout)
        self._trend_layout.addWidget(_card_title(tr("market.trend")))
        for vector in snap.trend.vectors:
            row = QHBoxLayout()
            row.setSpacing(8)
            tf_label = QLabel(vector.timeframe.value.upper())
            tf_label.setObjectName("MonoLabel")
            tf_label.setFixedWidth(38)
            value_label = QLabel(f"{vector.label}  ({vector.strength}/100)")
            value_label.setObjectName("MutedLabel")
            row.addWidget(tf_label)
            row.addWidget(value_label, 1)
            self._trend_layout.addLayout(row)

    def _render_levels(self, snap: SymbolSnapshot) -> None:
        tr = self._translator.translate
        layout = self._levels_layout
        _clear_layout(layout)
        layout.addWidget(_card_title(tr("market.levels")), 0, 0, 1, 2)
        atr_value = snap.volatility.atr or 0.0
        price = snap.last_close
        rows = 1
        for level in snap.levels[:VISIBLE_LEVELS]:
            distance = level.distance_atr(price, atr_value) if price and atr_value else 0.0
            name = QLabel(level.name)
            value = QLabel(
                f"{level.price:.{snap.digits}f}  ·  "
                + tr("market.level.distance", atr=f"{distance:+.1f}")
            )
            value.setObjectName("MonoLabel")
            layout.addWidget(name, rows, 0)
            layout.addWidget(value, rows, 1, Qt.AlignmentFlag.AlignRight)
            rows += 1

    def _render_scan(self, snapshot: MarketSnapshot | None) -> None:
        tr = self._translator.translate
        _clear_layout(self._scan_layout)
        self._scan_layout.addWidget(_card_title(tr("market.scanner")))
        if snapshot is None:
            return
        for entry in snapshot.scan:
            state_key = f"market.scan.state.{entry.state}"
            label = QLabel(
                f"{entry.symbol} — {tr(state_key)}  ·  {tr('market.bias')} {entry.score}"
            )
            label.setObjectName("MutedLabel")
            self._scan_layout.addWidget(label)

    def _render_calendar(self, snap: SymbolSnapshot) -> None:
        tr = self._translator.translate
        _clear_layout(self._calendar_layout)
        self._calendar_layout.addWidget(_card_title(tr("market.calendar")))
        event = snap.card.next_event
        if event is None:
            note = QLabel(tr("market.calendar.empty"))
            note.setWordWrap(True)
            note.setObjectName("MutedLabel")
            self._calendar_layout.addWidget(note)
            return
        minutes = event.minutes_until
        if minutes < 0:
            # Event already started (stale snapshot) — don't render "in -5 min".
            when = tr("market.calendar.now")
        elif minutes >= 60:
            when = tr("market.calendar.hours", hours=minutes // 60, minutes=minutes % 60)
        else:
            when = tr("market.calendar.minutes", minutes=minutes)
        label = QLabel(f"{event.currency} · {event.title} — {when}")
        label.setWordWrap(True)
        self._calendar_layout.addWidget(label)

    def _render_badges(self, snap: SymbolSnapshot) -> None:
        tr = self._translator.translate
        spread_points = snap.spread_points
        if spread_points is None:
            self._spread_label.setText(tr("market.spread"))
        else:
            text = tr("market.spread.value", points=f"{spread_points:.0f}")
            typical = snap.spread_typical
            if typical is not None:
                text += f" ({tr('market.spread.typical', points=f'{typical:.0f}')})"
            if snap.spread_abnormal:
                text += f" · {tr('market.spread.abnormal')}"
            self._spread_label.setText(text)
        offset = snap.broker_offset
        if offset:
            self._broker_label.setText(tr("market.broker_time", offset=offset))
        else:
            self._broker_label.setText("")

    def _render_chart(self, snapshot: MarketSnapshot | None, snap: SymbolSnapshot) -> None:
        service = self._service
        bars: list = []
        if service is not None:
            series = service.manager.series(self._symbol, self._timeframe)
            bars = list(series.bars)[-CHART_BARS:]
        _ = snapshot, snap
        self._chart.set_data(bars)

    # -- retranslate -------------------------------------------------------------------
    def retranslate(self) -> None:
        tr = self._translator.translate
        self._empty_label.setText(f"{tr('market.no_data')}\n\n{tr('market.empty.desc')}")
        self._render()
