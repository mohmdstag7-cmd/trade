"""Analysis card: plain-language market summary per symbol (SPEC C3.10).

The card is rule-generated from the analysis modules and carries i18n keys
with typed params, so the UI renders it in English AND Persian (RTL)
without duplicating logic. Verdicts are informational — never trade advice
and never a profit promise (SPEC I-1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analysis import patterns as patterns_mod
from app.analysis import structure as structure_mod
from app.analysis import volatility as volatility_mod
from app.analysis.correlation import CorrelationMatrix
from app.analysis.levels import Level, nearest_levels
from app.analysis.market_data import SanityIssue
from app.analysis.spread import SpreadMonitor
from app.analysis.trend import TrendMatrix
from app.core.timeframes import Timeframe

PREFIX = "analysis.card"


@dataclass(frozen=True, slots=True)
class CardLine:
    """One renderable line: an i18n key plus its parameters."""

    key: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NextEvent:
    """Minimal calendar info embedded in the card (countdown rendering)."""

    currency: str
    title: str
    minutes_until: int
    impact: str


@dataclass(frozen=True, slots=True)
class AnalysisCard:
    symbol: str
    bias_score: int
    verdict: str  # verdict key suffix: wait / watch / bias_up / bias_down
    lines: tuple[CardLine, ...]
    trend: TrendMatrix
    regime: str  # low / normal / high
    session: str
    spread_abnormal: bool
    patterns: tuple[str, ...]  # pattern kind names on the last bars
    next_event: NextEvent | None
    data_issues: tuple[str, ...]  # sanity issue kinds on the newest bar

    def verdict_key(self) -> str:
        return f"{PREFIX}.verdict.{self.verdict}"


class CardBuilder:
    """Assembles :class:`AnalysisCard` objects from module outputs."""

    def __init__(self, spread_monitor: SpreadMonitor | None = None) -> None:
        self.spread_monitor = spread_monitor or SpreadMonitor()

    def build(
        self,
        symbol: str,
        trend: TrendMatrix,
        h1_bars: list,
        d1_bars: list,
        levels: list[Level],
        volatility: volatility_mod.VolatilitySnapshot,
        session_name: str,
        atr_value: float,
        last_close: float,
        patterns: list[patterns_mod.Pattern] | None = None,
        next_event: NextEvent | None = None,
        issues: list[SanityIssue] | None = None,
        structure_events: list[structure_mod.StructureEvent] | None = None,
    ) -> AnalysisCard:
        lines: list[CardLine] = []
        verdict = self._verdict(symbol, trend, volatility, session_name, next_event)

        # -- trend summary line -------------------------------------------
        h4 = trend.vector(Timeframe.H4)
        h4_dir = h4.label if h4 else "flat"
        lines.append(
            CardLine(
                f"{PREFIX}.trend",
                {"symbol": symbol, "tf": "H4", "direction": h4_dir, "bias": trend.bias_score},
            )
        )

        # -- structure / nearest key level --------------------------------
        if levels and atr_value > 0:
            nearest = nearest_levels(levels, last_close, atr_value, max_count=1)
            if nearest:
                level, dist = nearest[0]
                lines.append(
                    CardLine(
                        f"{PREFIX}.level",
                        {"name": level.name, "price": level.price, "distance_atr": round(dist, 1)},
                    )
                )
        if structure_events:
            last_ev = structure_events[-1]
            lines.append(
                CardLine(
                    f"{PREFIX}.structure_event",
                    {
                        "type": last_ev.type.value,
                        "direction": "up" if last_ev.direction > 0 else "down",
                    },
                )
            )

        # -- volatility / session / spread ---------------------------------
        lines.append(
            CardLine(
                f"{PREFIX}.volatility",
                {"regime": volatility.regime.value, "adr_used": round(volatility.adr_used_pct)},
            )
        )
        lines.append(CardLine(f"{PREFIX}.session", {"session": session_name}))
        if next_event is not None:
            lines.append(
                CardLine(
                    f"{PREFIX}.event",
                    {
                        "currency": next_event.currency,
                        "title": next_event.title,
                        "minutes": next_event.minutes_until,
                        "impact": next_event.impact,
                    },
                )
            )
        if patterns:
            names = [p.kind.value for p in patterns[:2]]
            lines.append(CardLine(f"{PREFIX}.patterns", {"patterns": ", ".join(names)}))
        for issue_kind in sorted({i.kind for i in (issues or [])}):
            lines.append(CardLine(f"{PREFIX}.issue", {"issue": issue_kind}))

        return AnalysisCard(
            symbol=symbol,
            bias_score=trend.bias_score,
            verdict=verdict,
            lines=tuple(lines),
            trend=trend,
            regime=volatility.regime.value,
            session=session_name,
            spread_abnormal=self._spread_flag(symbol),
            patterns=tuple(p.kind.value for p in (patterns or [])),
            next_event=next_event,
            data_issues=tuple(sorted({i.kind for i in (issues or [])})),
        )

    # -- internals ------------------------------------------------------------
    def _spread_flag(self, symbol: str) -> bool:
        latest = getattr(self.spread_monitor, "latest_abnormal", None)
        if callable(latest):
            return bool(latest(symbol))
        return False

    def _verdict(
        self,
        symbol: str,
        trend: TrendMatrix,
        volatility: volatility_mod.VolatilitySnapshot,
        session_name: str,
        next_event: NextEvent | None,
    ) -> str:
        """Informational verdict: what the market state suggests (C3.10)."""
        if next_event is not None and next_event.impact == "high":
            return "wait"  # event risk ahead
        if self._spread_flag(symbol):
            return "wait"  # poor execution conditions
        bias = trend.bias_score
        if abs(bias) >= 50 and volatility.regime is not volatility_mod.Regime.LOW:
            return "bias_up" if bias > 0 else "bias_down"
        if abs(bias) >= 25:
            return "watch"
        return "wait"

    def spread_abnormal_flag(self) -> bool:  # pragma: no cover - hook point
        """Overridden by the service layer with live monitor state."""
        return False


def build_currency_notes(matrix: CorrelationMatrix, symbol: str) -> list[CardLine]:
    """Optional correlation footnote lines for a symbol's card."""
    pairs = matrix.with_symbol(symbol)
    strong = [(s, c) for s, c in pairs.items() if abs(c) >= 0.7]
    return [
        CardLine(f"{PREFIX}.correlation", {"other": s, "corr": round(c, 2)})
        for s, c in sorted(strong, key=lambda t: -abs(t[1]))
    ]
