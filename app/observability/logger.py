"""Logging bootstrap (Phase 1 minimum).

Phase 2 (SPEC E3) replaces this with structured JSON logs per category,
trace-id context, rotation, and masking. The public ``init_logging`` entry
point is kept stable so callers do not change.
"""

from __future__ import annotations

import sys

from loguru import logger

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def init_logging(debug: bool = False) -> None:
    """Configure console logging. Idempotent; safe to call multiple times."""
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if debug else "INFO",
        format=_FORMAT,
        enqueue=False,
        backtrace=False,
        diagnose=False,
    )
