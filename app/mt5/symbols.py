"""Broker symbol resolution (SPEC B4, C7).

Brokers decorate symbol names with suffixes: canonical ``EURUSD`` may be
listed as ``EURUSD.m``, ``EURUSD.a``, ``EURUSD.pro``, ``EURUSD#`` … The
application therefore stores canonical names and resolves them once against
the terminal's symbol list, caching the mapping for the session.

Resolution order:
1. exact (case-insensitive) match,
2. canonical + suffix for every candidate suffix,
3. prefix match as a last resort (warns — brokers sometimes use odd names).

The resolver only needs the list of symbol names from the gateway; it never
queries per-symbol info, so resolution costs exactly one command.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.observability.logger import get_logger

log = get_logger("mt5")

#: Decorations seen across retail brokers, ordered by prevalence.
SUFFIX_CANDIDATES: tuple[str, ...] = (
    "",
    ".m",
    ".a",
    ".b",
    ".c",
    ".d",
    ".e",
    ".pro",
    ".raw",
    ".r",
    ".std",
    ".standard",
    ".ecn",
    ".micro",
    ".mini",
    ".z",
    ".i",
    ".s",
    ".x",
    ".",
    "#",
    "+",
    "-ecn",
    ".spot",
    ".cash",
)

DEFAULT_WATCHLIST: tuple[str, ...] = ("EURUSD", "GBPUSD", "XAUUSD")


@dataclass(frozen=True, slots=True)
class ResolvedSymbol:
    """Canonical name mapped to the broker-side symbol."""

    canonical: str
    broker_symbol: str

    @property
    def has_suffix(self) -> bool:
        return self.broker_symbol != self.canonical


class SymbolResolver:
    """Resolve canonical names against the terminal's symbol list."""

    def __init__(self, suffixes: tuple[str, ...] = SUFFIX_CANDIDATES) -> None:
        self._suffixes = suffixes
        self._cache: dict[str, str | None] = {}

    def resolve(self, canonical: str, available: tuple[str, ...] | list[str]) -> ResolvedSymbol:
        """Map ``canonical`` onto a listed symbol, or raise ``ValueError``.

        Results are cached per canonical name for the lifetime of the
        mapping passed in; call :meth:`invalidate` after reconnecting to a
        different broker account.
        """
        key = canonical.strip().upper()
        if key in self._cache:
            cached = self._cache[key]
            if cached is None:
                msg = f"symbol not found on this broker: {canonical} (cached)"
                raise ValueError(msg)
            return ResolvedSymbol(canonical=canonical, broker_symbol=cached)

        lookup = {name.upper(): name for name in available}
        exact = lookup.get(key)
        if exact is not None:
            self._cache[key] = exact
            if exact != canonical:
                log.info("symbols: {} -> {} (case)", canonical, exact)
            return ResolvedSymbol(canonical=canonical, broker_symbol=exact)

        for suffix in self._suffixes:
            candidate = key + suffix.upper()
            found = lookup.get(candidate)
            if found is not None:
                self._cache[key] = found
                log.info("symbols: resolved {} -> {}", canonical, found)
                return ResolvedSymbol(canonical=canonical, broker_symbol=found)

        # last resort: unique prefix match
        prefix_matches = [original for upper, original in lookup.items() if upper.startswith(key)]
        if len(prefix_matches) == 1:
            found = prefix_matches[0]
            self._cache[key] = found
            log.warning("symbols: {} -> {} (prefix match)", canonical, found)
            return ResolvedSymbol(canonical=canonical, broker_symbol=found)
        if len(prefix_matches) > 1:
            self._cache[key] = None
            msg = f"ambiguous symbol match for {canonical}: {', '.join(sorted(prefix_matches)[:5])}"
            raise ValueError(msg)

        self._cache[key] = None
        msg = f"symbol not found on this broker: {canonical}"
        raise ValueError(msg)

    def invalidate(self) -> None:
        """Drop the cache (broker account changed / reconnect)."""
        self._cache.clear()

    def resolve_all(
        self, canonicals: tuple[str, ...] | list[str], available: tuple[str, ...] | list[str]
    ) -> dict[str, ResolvedSymbol]:
        """Resolve a watchlist; missing symbols are reported, not fatal."""
        resolved: dict[str, ResolvedSymbol] = {}
        for canonical in canonicals:
            try:
                resolved[canonical] = self.resolve(canonical, available)
            except ValueError as exc:
                log.warning("symbols: {}", exc)
        return resolved
