"""Market analysis domain layer (SPEC C3).

Pure-Python/numpy modules that turn OHLCV bars into market understanding:
trend, structure, levels, volatility, sessions, correlation, spread,
patterns, analysis cards and the opportunity scanner.

Layering rules (SPEC D2): this package never imports MetaTrader5 or
PySide6. Bars enter as :class:`app.mt5.models.RateBar` dataclasses (a plain
dataclass, safe to depend on) through the data manager; the UI renders the
results. Evaluation happens on CLOSED bars only (SPEC C7/I-4): the data
manager drops the still-forming bar before anything downstream sees it.
"""
