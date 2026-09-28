"""Self-built indicators over numpy arrays (SPEC C1: no external TA lib).

Conventions:

- Every function takes plain numpy arrays (already float64) ordered oldest
  → newest and returns an array of the SAME length; positions where the
  indicator is undefined are ``NaN``.
- Inputs must contain CLOSED bars only. The caller (data manager) is
  responsible for dropping the still-forming bar, so no function here can
  look ahead (SPEC C7, I-4).
- Smoothing uses Wilder's method where the indicator traditionally does
  (RSI, ATR, ADX) and the standard EMA recursion with an SMA seed
  elsewhere — both causal recursions.
"""

from __future__ import annotations

import numpy as np

__all__ = ["adx", "atr", "ema", "rsi", "slope_pct", "sma"]


def _as_f64(values: np.ndarray | list[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError("indicator input must be a 1-D array")
    return arr


def sma(values: np.ndarray | list[float], period: int) -> np.ndarray:
    """Simple moving average; ``NaN`` before ``period - 1``."""
    if period < 1:
        raise ValueError("period must be >= 1")
    arr = _as_f64(values)
    out = np.full(arr.shape[0], np.nan)
    if arr.shape[0] < period:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(arr, period)
    out[period - 1 :] = windows.mean(axis=1)
    return out


def ema(values: np.ndarray | list[float], period: int) -> np.ndarray:
    """Exponential moving average seeded with the SMA of the first period."""
    if period < 1:
        raise ValueError("period must be >= 1")
    arr = _as_f64(values)
    n = arr.shape[0]
    out = np.full(n, np.nan)
    if n < period:
        return out
    alpha = 2.0 / (period + 1.0)
    out[period - 1] = arr[:period].mean()
    for i in range(period, n):
        out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
    return out


def _wilder(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing (RMA): first value = SMA, then recursive mean."""
    n = values.shape[0]
    out = np.full(n, np.nan)
    if n < period:
        return out
    out[period - 1] = values[:period].mean()
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def rsi(values: np.ndarray | list[float], period: int = 14) -> np.ndarray:
    """Relative Strength Index with Wilder smoothing (0..100)."""
    if period < 1:
        raise ValueError("period must be >= 1")
    arr = _as_f64(values)
    n = arr.shape[0]
    out = np.full(n, np.nan)
    if n < period + 1:
        return out
    delta = np.diff(arr)
    gain = np.where(delta > 0.0, delta, 0.0)
    loss = np.where(delta < 0.0, -delta, 0.0)
    avg_gain = _wilder(gain, period)
    avg_loss = _wilder(loss, period)
    rs = np.divide(
        avg_gain,
        avg_loss,
        out=np.full(n - 1, np.inf),
        where=avg_loss != 0.0,
    )
    rsi_body = 100.0 - 100.0 / (1.0 + rs)
    rsi_body = np.where((avg_loss == 0.0) & (avg_gain == 0.0), 50.0, rsi_body)
    out[1:] = rsi_body
    return out


def true_range(
    highs: np.ndarray | list[float],
    lows: np.ndarray | list[float],
    closes: np.ndarray | list[float],
) -> np.ndarray:
    """True Range series; the first bar has no previous close (plain range)."""
    h = _as_f64(highs)
    lows_a = _as_f64(lows)
    c = _as_f64(closes)
    if not (h.shape[0] == lows_a.shape[0] == c.shape[0]):
        raise ValueError("highs/lows/closes must have the same length")
    if h.shape[0] == 0:
        # Empty input returns an empty series instead of raising IndexError
        # (same-length contract with the OHLC arrays).
        return h
    prev_close = np.concatenate(([c[0]], c[:-1]))
    tr: np.ndarray = np.maximum(
        h - lows_a, np.maximum(np.abs(h - prev_close), np.abs(lows_a - prev_close))
    )
    return tr


def atr(
    highs: np.ndarray | list[float],
    lows: np.ndarray | list[float],
    closes: np.ndarray | list[float],
    period: int = 14,
) -> np.ndarray:
    """Average True Range with Wilder smoothing."""
    if period < 1:
        raise ValueError("period must be >= 1")
    tr = true_range(highs, lows, closes)
    if tr.shape[0] == 0:
        return tr
    with np.errstate(divide="ignore", invalid="ignore"):
        return _wilder(tr, period)


def adx(
    highs: np.ndarray | list[float],
    lows: np.ndarray | list[float],
    closes: np.ndarray | list[float],
    period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Average Directional Index.

    Returns ``(adx, plus_di, minus_di)`` — all Wilder-smoothed, ``NaN``
    until enough bars exist (about ``2 * period``).
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    h = _as_f64(highs)
    lows_a = _as_f64(lows)
    c = _as_f64(closes)
    n = h.shape[0]
    nan_all = np.full(n, np.nan)
    if n < 2 * period + 1:
        return nan_all, nan_all.copy(), nan_all.copy()

    up_move = h[1:] - h[:-1]
    down_move = lows_a[:-1] - lows_a[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        tr = true_range(h, lows_a, c)[1:]
        atr_smooth = _wilder(tr, period)
        # Guard perfectly flat / illiquid series (atr_smooth == 0).
        safe_atr = np.where(atr_smooth == 0.0, np.nan, atr_smooth)
        plus_di = 100.0 * _wilder(plus_dm, period) / safe_atr
        minus_di = 100.0 * _wilder(minus_dm, period) / safe_atr

        denom = plus_di + minus_di
        dx = 100.0 * np.abs(plus_di - minus_di) / np.where(denom == 0.0, np.nan, denom)

    # Seed the ADX with VALID DX values only: the naive nan_to_num(dx, 0)
    # let the leading NaN region (DI undefined) enter as zeros and biased
    # the SMA seed badly (verified: 44.7 vs 100 on a trending series).
    valid_dx = ~np.isnan(dx)
    m = dx.shape[0]
    adx_arr = np.full(m, np.nan)
    if valid_dx.any():
        first_valid = int(np.argmax(valid_dx))
        adx_valid = _wilder(dx[first_valid:], period)
        writable = adx_valid[period - 1 :]
        end = min(m, first_valid + period - 1 + writable.shape[0])
        adx_arr[first_valid + period - 1 : end] = writable[: end - (first_valid + period - 1)]
    # _wilder leaves NaN before the seed position — ADX now stays NaN until
    # roughly 2 * period bars, as documented.
    # Pad to length n to preserve same-length contract (R2-003).
    pad = np.array([np.nan])
    adx_padded = np.concatenate([pad, adx_arr])
    plus_di_padded = np.concatenate([pad, plus_di])
    minus_di_padded = np.concatenate([pad, minus_di])
    return adx_padded, plus_di_padded, minus_di_padded


def slope_pct(values: np.ndarray | list[float], lookback: int = 5) -> np.ndarray:
    """Per-bar percentage slope over ``lookback`` bars (0 at the start).

    ``slope_pct[i] = (v[i] - v[i - lookback]) / (v[i - lookback] * lookback)``
    — a rough "percent per bar" measure used for trend strength.
    """
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    arr = _as_f64(values)
    n = arr.shape[0]
    out = np.zeros(n)
    if n <= lookback:
        return out
    base = arr[:-lookback]
    diff = arr[lookback:] - base
    out[lookback:] = np.divide(
        diff,
        np.abs(base) * lookback,
        out=np.zeros(n - lookback),
        where=np.abs(base) > 0.0,
    )
    return out
