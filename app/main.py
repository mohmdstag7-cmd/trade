"""Composition root and command-line entry point.

Phase 1 scope:
- ``--version`` prints the application version.
- ``--self-check`` verifies that the ``MetaTrader5`` package can be imported
  (used by the CI build job to prove the packaged exe is real-MT5 capable).
- Default: launches the GUI.

Later phases extend the CLI (``--mt5-smoke-test``, ``--mt5-trade-test``,
``--profile NAME``) without changing the structure of this module.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable
from types import ModuleType

from app.__version__ import __version__
from app.observability.logger import init_logging

SELF_CHECK_OK = "SELF-CHECK OK"
SELF_CHECK_FAIL = "SELF-CHECK FAIL"


def _import_mt5() -> ModuleType:
    """Import the real MetaTrader5 package.

    Kept as an importable function so tests can inject fakes. PyInstaller
    bundles the package via ``hiddenimports`` in ``installer/app.spec``.
    """
    return importlib.import_module("MetaTrader5")


def run_self_check(import_mt5: Callable[[], ModuleType] = _import_mt5) -> int:
    """Verify the MetaTrader5 package imports and report its version.

    Exit code semantics (SPEC I2): 0 = package usable, 1 = package missing or
    broken. A production build must never fall back to fake data.
    """
    try:
        module = import_mt5()
    except Exception as exc:
        print(f"{SELF_CHECK_FAIL}: MetaTrader5 package could not be loaded: {exc!r}")
        return 1
    version = getattr(module, "__version__", "unknown")
    print(f"{SELF_CHECK_OK}: MetaTrader5 {version}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="MT5TradingWorkstation",
        description="MT5 Trading Workstation desktop application.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the application version and exit",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="verify the MetaTrader5 package can be imported and exit (0/1)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="enable verbose logging",
    )
    return parser


def run_gui(debug: bool = False) -> int:
    """Compose the application and start the Qt event loop."""
    init_logging(debug=debug)

    from PySide6.QtWidgets import QApplication

    from app.core.event_bus import EventBus
    from app.core.settings import UiSettings
    from app.ui.i18n.translator import Translator
    from app.ui.main_window import MainWindow
    from app.ui.theme.manager import ThemeManager

    existing = QApplication.instance()
    qt_app: QApplication = (
        existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    )
    qt_app.setApplicationName("MT5 Trading Workstation")
    qt_app.setOrganizationName("MT5TradingWorkstation")
    qt_app.setStyle("Fusion")

    settings = UiSettings.load()
    translator = Translator(settings)
    theme_manager = ThemeManager(settings)
    bus = EventBus()

    theme_manager.apply()
    qt_app.setApplicationDisplayName(translator.translate("app.title"))
    qt_app.setLayoutDirection(translator.layout_direction())

    window = MainWindow(
        bus=bus,
        settings=settings,
        translator=translator,
        theme_manager=theme_manager,
    )
    window.show()
    return qt_app.exec()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"MT5 Trading Workstation {__version__}")
        return 0
    if args.self_check:
        return run_self_check()
    return run_gui(debug=args.debug)


if __name__ == "__main__":
    raise SystemExit(main())
