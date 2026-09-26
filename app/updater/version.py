"""Version parsing and comparison for the updater.

Deliberately dependency-free: the app versions are simple
``MAJOR.MINOR.PATCH`` strings (optionally prefixed with ``v`` and with an
optional non-numeric suffix such as ``rc1`` which is ignored for
ordering). A tiny dedicated parser beats pulling ``packaging`` into a
150 MB bundle.
"""

from __future__ import annotations

import re

_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*")


def parse_version(text: str) -> tuple[int, ...]:
    """Parse ``v0.6.0`` → ``(0, 6, 0)``; ignore any non-numeric suffix.

    Raises :class:`ValueError` when no numeric component exists at all.
    """
    cleaned = text.strip().lstrip("vV")
    match = _VERSION_RE.match(cleaned)
    if match is None:
        msg = f"unparsable version: {text!r}"
        raise ValueError(msg)
    return tuple(int(part) for part in match.group(0).split("."))


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is strictly newer than ``current``."""
    return parse_version(candidate) > parse_version(current)
