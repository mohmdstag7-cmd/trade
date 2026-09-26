"""Broker suffix symbol resolution tests (SPEC B4)."""

from __future__ import annotations

import pytest

from app.mt5.symbols import SymbolResolver

BROKER_SYMBOLS = ("EURUSD.m", "GBPUSD", "XAUUSD.pro", "usdjpy", "GER40.cash")


class TestResolve:
    def test_exact_match(self) -> None:
        resolver = SymbolResolver()
        resolved = resolver.resolve("GBPUSD", BROKER_SYMBOLS)
        assert resolved.broker_symbol == "GBPUSD"
        assert not resolved.has_suffix

    def test_suffix_match(self) -> None:
        resolver = SymbolResolver()
        assert resolver.resolve("EURUSD", BROKER_SYMBOLS).broker_symbol == "EURUSD.m"
        assert resolver.resolve("XAUUSD", BROKER_SYMBOLS).broker_symbol == "XAUUSD.pro"

    def test_case_insensitive(self) -> None:
        resolver = SymbolResolver()
        assert resolver.resolve("usdjpy", BROKER_SYMBOLS).broker_symbol == "usdjpy"
        assert resolver.resolve("EURusd", BROKER_SYMBOLS).broker_symbol == "EURUSD.m"

    def test_unique_prefix_match_warns_but_works(self) -> None:
        resolver = SymbolResolver()
        resolved = resolver.resolve("GBP", ("GBPUSD.custom", "EURUSD"))
        assert resolved.broker_symbol == "GBPUSD.custom"

    def test_ambiguous_prefix_raises(self) -> None:
        resolver = SymbolResolver()
        with pytest.raises(ValueError, match="ambiguous"):
            resolver.resolve("GBP", ("GBPUSD.a", "GBPUSD.b"))

    def test_missing_raises(self) -> None:
        resolver = SymbolResolver()
        with pytest.raises(ValueError, match="not found"):
            resolver.resolve("AAPL", BROKER_SYMBOLS)

    def test_results_cached(self) -> None:
        resolver = SymbolResolver()
        first = resolver.resolve("EURUSD", BROKER_SYMBOLS)
        # a different broker list must not change the cached mapping
        second = resolver.resolve("EURUSD", ("EURUSD.other",))
        assert first.broker_symbol == second.broker_symbol == "EURUSD.m"

    def test_missing_is_cached_too(self) -> None:
        resolver = SymbolResolver()
        with pytest.raises(ValueError):
            resolver.resolve("AAPL", BROKER_SYMBOLS)
        with pytest.raises(ValueError, match="cached"):
            resolver.resolve("AAPL", ("AAPL.NASDAQ",))

    def test_invalidate_clears_cache(self) -> None:
        resolver = SymbolResolver()
        with pytest.raises(ValueError):
            resolver.resolve("AAPL", BROKER_SYMBOLS)  # cached miss
        resolver.invalidate()
        resolved = resolver.resolve("AAPL", ("AAPL.NASDAQ",))
        assert resolved.broker_symbol == "AAPL.NASDAQ"


class TestResolveAll:
    def test_mixed_watchlist(self) -> None:
        resolver = SymbolResolver()
        resolved = resolver.resolve_all(("EURUSD", "GBPUSD", "AAPL"), BROKER_SYMBOLS)
        assert set(resolved) == {"EURUSD", "GBPUSD"}
        assert resolved["EURUSD"].broker_symbol == "EURUSD.m"

    def test_empty_available(self) -> None:
        resolver = SymbolResolver()
        assert resolver.resolve_all(("EURUSD",), ()) == {}
