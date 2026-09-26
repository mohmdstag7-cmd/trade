"""Custom candlestick chart on pyqtgraph (SPEC C1, C12).

pyqtgraph paints thousands of bars fast; the candle bodies/wicks are drawn
into a cached ``QPicture`` that is regenerated only when the bar set (or
theme) changes. The chart theme-switches with the app (dark/light tokens).
"""

from __future__ import annotations

from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPicture

from app.mt5.models import RateBar
from app.ui.theme.tokens import ThemeTokens


class CandlestickItem(pg.GraphicsObject):
    """One ``GraphicsObject`` holding the whole candle series."""

    def __init__(self) -> None:
        super().__init__()
        self._picture: QPicture | None = None
        self._bounds: QRectF | None = None
        self._up = QColor("#22C55E")
        self._down = QColor("#EF4444")
        self._flat = QColor("#8A91A5")
        self._bars: list[RateBar] = []

    # -- data -------------------------------------------------------------------
    def set_colors(self, tokens: ThemeTokens) -> None:
        self._up = QColor(tokens.profit)
        self._down = QColor(tokens.loss)
        self._flat = QColor(tokens.text_secondary)
        self._picture = None
        self.update()

    def set_bars(self, bars: list[RateBar]) -> None:
        self._bars = bars
        self._picture = None
        if bars:
            times = [b.time for b in bars]
            prices = [v for b in bars for v in (b.high, b.low)]
            self._bounds = QRectF(
                float(min(times)),
                float(min(prices)),
                float(max(times) - min(times)),
                float(max(prices) - min(prices)),
            )
        else:
            self._bounds = None
        self.update()

    @property
    def up_color(self) -> QColor:
        return self._up

    @property
    def down_color(self) -> QColor:
        return self._down

    def spacing(self) -> float:
        """Typical bar spacing in x-units (epoch seconds)."""
        if len(self._bars) < 2:
            return 60.0
        diffs = [b.time - a.time for a, b in zip(self._bars, self._bars[1:], strict=False)]
        return max(float(min(diffs)), 1.0)

    # -- painting -----------------------------------------------------------------
    def generatePicture(self) -> QPicture:
        picture = QPicture()
        painter = QPainter(picture)
        width = max(1.0, self.spacing() * 0.7)
        for bar in self._bars:
            color = (
                self._up
                if bar.close > bar.open
                else self._down
                if bar.close < bar.open
                else self._flat
            )
            pen = QPen(color, 1)
            painter.setPen(pen)
            painter.setBrush(QBrush(color))
            painter.drawLine(QPointF(bar.time, bar.low), QPointF(bar.time, bar.high))
            top = max(bar.open, bar.close)
            bottom = min(bar.open, bar.close)
            body_height = max(top - bottom, self.spacing() * 0.02)
            painter.drawRect(QRectF(bar.time - width / 2, bottom, width, body_height))
        painter.end()
        return picture

    # -- GraphicsObject API ---------------------------------------------------------
    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        if self._picture is None:
            self._picture = self.generatePicture()
        self._picture.play(painter)

    def boundingRect(self) -> QRectF:
        return self._bounds if self._bounds is not None else QRectF()


class PriceChart(pg.GraphicsLayoutWidget):
    """Candles + volume + last-price line, theme aware."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._candles = CandlestickItem()
        self._price_plot: pg.PlotItem = self.addPlot(row=0, col=0)
        self._price_plot.showGrid(x=False, y=False)
        self._price_plot.setMouseEnabled(x=True, y=False)
        self._price_plot.hideButtons()
        self._price_plot.setAxisItems({"bottom": pg.DateAxisItem(orientation="bottom")})

        self._volume_plot: pg.PlotItem = self.addPlot(row=1, col=0)
        self._volume_plot.setXLink(self._price_plot)
        self._volume_plot.setMaximumHeight(80)
        self._volume_plot.hideAxis("left")
        self._volume_plot.setMouseEnabled(x=True, y=False)
        self._volume_plot.hideButtons()
        self._volume_plot.setAxisItems({"bottom": pg.DateAxisItem(orientation="bottom")})

        self._price_plot.addItem(self._candles)
        self._last_price_line = pg.InfiniteLine(angle=0, movable=False)
        self._last_price_line.setZValue(10)
        self._price_plot.addItem(self._last_price_line)
        self._volume_bars: pg.BarGraphItem | None = None

    # -- data -------------------------------------------------------------------
    def set_data(self, bars: list[RateBar]) -> None:
        self._candles.set_bars(bars)
        if self._volume_bars is not None:
            self._volume_plot.removeItem(self._volume_bars)
            self._volume_bars = None
        if bars:
            self._last_price_line.setPos(bars[-1].close)
            self._last_price_line.show()
            x = [float(b.time) for b in bars]
            heights = [float(b.tick_volume) for b in bars]
            brushes = [
                QBrush(self._candles.up_color if b.close >= b.open else self._candles.down_color)
                for b in bars
            ]
            self._volume_bars = pg.BarGraphItem(
                x=x,
                height=heights,
                width=max(1.0, self._candles.spacing() * 0.7),
                brushes=brushes,
            )
            self._volume_plot.addItem(self._volume_bars)
            self._price_plot.autoRange()
            self._volume_plot.autoRange()
        else:
            self._last_price_line.hide()

    def apply_theme(self, tokens: ThemeTokens) -> None:
        text = QColor(tokens.text_secondary)
        for plot in (self._price_plot, self._volume_plot):
            for axis in ("left", "bottom"):
                ax = plot.getAxis(axis)
                ax.setPen(pg.mkPen(text))
                ax.setTextPen(pg.mkPen(text))
        for item in (self._price_plot, self._volume_plot):
            item.getViewBox().setBackgroundColor(tokens.card)
        self.setBackground(tokens.card)
        self._last_price_line.setPen(pg.mkPen(QColor(tokens.accent), width=1))
        self._candles.set_colors(tokens)
