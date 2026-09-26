"""Filesystem locations for logs and crash reports.

Windows-first (SPEC B: Windows 10/11 target) with sane fallbacks for
non-Windows development machines, so the same code runs in CI. The
observability layer never depends on Qt, therefore paths are resolved with
environment variables rather than QStandardPaths.
"""

from __future__ import annotations

import os
import pathlib

APP_DATA_FOLDER = "MT5TradingWorkstation"


def default_data_dir() -> pathlib.Path:
    """Return the per-user application data directory."""
    if os.name == "nt":
        base = pathlib.Path(
            os.environ.get("LOCALAPPDATA", pathlib.Path.home() / "AppData" / "Local")
        )
    else:
        base = pathlib.Path(
            os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local" / "share")
        )
    return base / APP_DATA_FOLDER


def default_logs_dir() -> pathlib.Path:
    """Directory holding ``<category>/<date>.jsonl`` and ``all.log``."""
    return default_data_dir() / "logs"


def default_crash_reports_dir() -> pathlib.Path:
    """Directory holding ``crash_<timestamp>.json`` reports."""
    return default_data_dir() / "crash_reports"
