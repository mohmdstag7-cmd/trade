"""Rolling correlation matrix + currency strength meter (SPEC C3.6)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class CorrelationMatrix:
    symbols: tuple[str, ...]
    matrix: np.ndarray  # (n, n) Pearson correlations, diagonal = 1

    def with_symbol(self, symbol: str) -> dict[str, float]:
        """Correlations of one symbol against all others (excluding self)."""
        if symbol not in self.symbols:
            return {}
        i = self.symbols.index(symbol)
        return {s: float(self.matrix[i, j]) for j, s in enumerate(self.symbols) if j != i}


def daily_returns(closes: np.ndarray | list[float]) -> np.ndarray:
    """Simple per-bar returns of a close series."""
    arr = np.asarray(closes, dtype=np.float64)
    if arr.shape[0] < 2:
        return np.empty(0)
    return np.diff(arr) / arr[:-1]


def rolling_correlation(
    closes_by_symbol: dict[str, list[tuple[int, float]]],
    window: int = 50,
) -> CorrelationMatrix:
    """Pearson correlation of timestamp-aligned return tails across symbols.

    ``closes_by_symbol`` maps symbol -> (bar_time, close) pairs oldest to
    newest. For each pair, returns are computed over the timestamps BOTH
    symbols share, then the last ``window`` aligned returns are correlated
    (never position-aligned tails — a missing day on one symbol would
    silently shift every later bar). Pairs with fewer than ``window``
    common points get 0.0.
    """
    symbols = sorted(closes_by_symbol)
    n = len(symbols)
    matrix = np.eye(n)
    aligned_returns: dict[str, np.ndarray] = {}
    common: set[int] | None = None
    for s in symbols:
        times = [t for t, _ in closes_by_symbol[s]]
        common = set(times) if common is None else (common & set(times))
    if common is None:
        common = set()
    for s in symbols:
        price_by_time = dict(closes_by_symbol[s])
        shared = sorted(common)
        closes = [price_by_time[t] for t in shared if t in price_by_time]
        aligned_returns[s] = daily_returns(closes)[-window:]
    for i, a in enumerate(symbols):
        for j in range(i + 1, n):
            b = symbols[j]
            ra, rb = aligned_returns[a], aligned_returns[b]
            m = min(ra.shape[0], rb.shape[0])
            if m < window:
                matrix[i, j] = matrix[j, i] = 0.0
                continue
            va, vb = ra[-m:], rb[-m:]
            std_a = va.std()
            std_b = vb.std()
            if std_a == 0.0 or std_b == 0.0:
                matrix[i, j] = matrix[j, i] = 0.0
            else:
                corr = float(np.corrcoef(va, vb)[0, 1])
                matrix[i, j] = matrix[j, i] = corr
    return CorrelationMatrix(tuple(symbols), matrix)


# -- currency strength ---------------------------------------------------------


#: Common ISO currency codes treated as FX legs for the strength meter.
ISO_CURRENCIES = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CHF",
        "AUD",
        "NZD",
        "CAD",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
        "HUF",
        "CZK",
        "TRY",
        "MXN",
        "ZAR",
        "SGD",
        "HKD",
        "CNH",
    }
)


def _currencies_of(symbol: str) -> tuple[str, str]:
    """Split an FX pair into base/quote (3-letter codes), best effort."""
    s = symbol.upper()
    for sep in ("_", "-", ".", "/"):
        if sep in s and len(s.split(sep)[0]) == 3:
            base, quote = s.split(sep, 1)
            return base[:3], quote[:3]
    if len(s) == 6 and s.isalpha():
        return s[:3], s[3:]
    return "", ""


def currency_strength(
    daily_returns_by_symbol: dict[str, list[float]],
    lookback: int = 20,
) -> dict[str, float]:
    """Sum of signed daily returns: base adds, quote subtracts.

    A currency's score over the lookback window is the sum of the returns
    of pairs where it is the base minus the sum where it is the quote —
    a simple, explainable proxy (SPEC C3.6). Non-FX symbols (e.g. metals
    quoted in USD) contribute USD legs via their quote currency.
    """
    scores: dict[str, float] = {}
    for symbol, closes in daily_returns_by_symbol.items():
        base, quote = _currencies_of(symbol)
        if not quote:
            continue
        recent = daily_returns(closes)[-lookback:]
        total = float(np.sum(recent)) if recent.shape[0] else 0.0
        if base in ISO_CURRENCIES:
            scores[base] = scores.get(base, 0.0) + total
        # metals/indices quoted in USD move the USD (quote) leg only
        scores[quote] = scores.get(quote, 0.0) - total
    return scores


def strength_ranking(scores: dict[str, float]) -> list[tuple[str, float]]:
    """Currencies sorted strongest → weakest."""
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
