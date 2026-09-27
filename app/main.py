"""Composition root and command-line entry point.

Phase 1 scope:
- ``--version`` prints the application version.
- ``--self-check`` verifies that the ``MetaTrader5`` package can be imported
  (used by the CI build job to prove the packaged exe is real-MT5 capable).
- Default: launches the GUI.

Phase 3 adds real-terminal diagnostics (SPEC C13, G3-3):
- ``--mt5-smoke-test``: read-only connection health report.
- ``--mt5-trade-test``: one minimal order on a DEMO account only.

Phase 4 adds storage diagnostics (SPEC E1, G3-4):
- ``--db-check``: migrate the local database, print stats, verify integrity.
- ``--self-check`` now also proves the storage layer works in the package.

Later phases extend the CLI (``--profile NAME``) without changing the
structure of this module.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import json
import sys
from collections.abc import Callable
from types import ModuleType
from typing import Any

from app.__version__ import __version__
from app.observability.logger import init_logging
from app.observability.paths import default_crash_reports_dir, default_data_dir, default_logs_dir

SELF_CHECK_OK = "SELF-CHECK OK"
SELF_CHECK_FAIL = "SELF-CHECK FAIL"

SMOKE_OK = "MT5 SMOKE TEST PASSED"
SMOKE_FAIL = "MT5 SMOKE TEST FAILED"


def _import_mt5() -> ModuleType:
    """Import the real MetaTrader5 package.

    Kept as an importable function so tests can inject fakes. PyInstaller
    bundles the package via ``hiddenimports`` in ``installer/app.spec``.
    """
    return importlib.import_module("MetaTrader5")


def run_self_check(import_mt5: Callable[[], ModuleType] = _import_mt5) -> int:
    """Verify the MetaTrader5 package and the storage layer.

    Exit code semantics (SPEC I2): 0 = everything usable, 1 = something is
    missing or broken. A production build must never fall back to fake data.
    """
    try:
        module = import_mt5()
    except Exception as exc:
        print(f"{SELF_CHECK_FAIL}: MetaTrader5 package could not be loaded: {exc!r}")
        return 1
    version = getattr(module, "__version__", "unknown")

    storage_error = _probe_storage()
    if storage_error is not None:
        print(f"{SELF_CHECK_FAIL}: storage layer broken: {storage_error}")
        return 1
    print(f"{SELF_CHECK_OK}: MetaTrader5 {version}; storage OK")
    return 0


def _probe_storage() -> str | None:
    """Migrate a throwaway database; return an error message or None."""
    import pathlib
    import tempfile

    from app.storage.db import Database
    from app.storage.migrations import MigrationRunner

    try:
        with tempfile.TemporaryDirectory(prefix="mt5ws-check-") as tmp:
            db = Database(pathlib.Path(tmp) / "probe.db")
            try:
                runner = MigrationRunner(db)
                runner.run_all()
                if not runner.verify() or db.row_count("outbox") != 0:
                    return "migration or integrity check failed"
            finally:
                db.close_all()
    except Exception as exc:
        return repr(exc)
    return None


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
        "--mt5-smoke-test",
        action="store_true",
        help="read-only connection diagnostics against a real MT5 terminal (0/1)",
    )
    parser.add_argument(
        "--mt5-trade-test",
        action="store_true",
        help="open+close one minimal order — DEMO accounts only (0/1)",
    )
    parser.add_argument(
        "--db-check",
        action="store_true",
        help="migrate the local database, print stats and verify integrity (0/1)",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="override the application data directory (used with --db-check)",
    )
    update = parser.add_argument_group("update installer (internal)")
    update.add_argument(
        "--apply-update",
        action="store_true",
        help="install a staged update: swap the staging tree over this install, "
        "then relaunch the app (used by the in-app updater, not meant for humans)",
    )
    update.add_argument(
        "--update-staging",
        default="",
        help="staging directory holding the downloaded update (with --apply-update)",
    )
    update.add_argument(
        "--update-pid",
        type=int,
        default=0,
        help="process id of the running app to wait for (with --apply-update)",
    )
    mt5 = parser.add_argument_group("MT5 connection")
    mt5.add_argument("--login", type=int, default=0, help="MT5 account number")
    mt5.add_argument("--server", default="", help="broker server name, e.g. MetaQuotes-Demo")
    mt5.add_argument("--terminal-path", default="", help="optional path to terminal64.exe")
    mt5.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the account password from stdin instead of the OS vault",
    )
    mt5.add_argument(
        "--symbols",
        default="EURUSD,GBPUSD,XAUUSD",
        help="comma-separated canonical symbols for the smoke test",
    )
    mt5.add_argument("--symbol", default="EURUSD", help="symbol for the demo trade test")
    mt5.add_argument("--volume", type=float, default=None, help="volume for the demo trade test")
    mt5.add_argument(
        "--yes",
        action="store_true",
        help="explicit confirmation required by --mt5-trade-test",
    )
    mt5.add_argument("--json", action="store_true", help="print machine-readable JSON output")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="enable verbose logging",
    )
    return parser


def _resolve_password(args: argparse.Namespace, login: int, *, quiet: bool = False) -> str:
    """Password from the OS vault, else stdin. Never from argv/env."""
    from app.mt5.credentials import CredentialStore, CredentialStoreError

    if login and not args.password_stdin:
        try:
            stored = CredentialStore().get_password(login)
        except CredentialStoreError as exc:
            if not quiet:
                print(f"Could not read the password vault: {exc}")
            stored = None
        if stored:
            if not quiet:
                print("Using the password stored in Windows Credential Manager.")
            return stored
    if args.password_stdin:
        if not quiet:
            print("Paste the account password and press Enter:")
        return sys.stdin.readline().rstrip("\r\n")
    if not quiet:
        print(
            "No password available. Either save it once via the app Settings page "
            "(Windows Credential Manager) or re-run with --password-stdin."
        )
    return ""


def _require_credentials(
    args: argparse.Namespace, *, quiet: bool = False
) -> tuple[int, str, str] | None:
    login, server = args.login, args.server.strip()
    if not login or not server:
        if not quiet:
            print("--login and --server are required for MT5 diagnostics.")
        return None
    password = _resolve_password(args, login, quiet=quiet)
    if not password:
        return None
    return login, server, password


def _write_json_report(payload: dict[str, Any], logs_dir: Any) -> str:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%d_%H%M%S")
    path = logs_dir / f"mt5_smoke_test_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def run_mt5_smoke_test(args: argparse.Namespace) -> int:
    """Read-only diagnostics: import → connect → account → symbols → bars → latency."""
    from app.mt5.diagnostics import run_smoke_test
    from app.mt5.models import ConnectRequest

    creds = _require_credentials(args, quiet=args.json)
    if creds is None:
        return 2
    login, server, password = creds
    request = ConnectRequest(
        login=login,
        password=password,
        server=server,
        terminal_path=args.terminal_path.strip(),
    )
    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())

    report = run_smoke_test(_import_mt5, request, symbols=symbols)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        for line in report.summary_lines():
            print(line)
    try:
        path = _write_json_report(report.to_dict(), default_logs_dir())
        if not args.json:
            print(f"Report saved: {path}")
    except OSError:  # pragma: no cover - report writing is best-effort
        pass
    return 0 if report.ok else 1


def run_mt5_trade_test(args: argparse.Namespace) -> int:
    """Open+close one minimal order — demo accounts only, `--yes` required."""
    from app.mt5.demo_trade_test import run_demo_trade_test
    from app.mt5.gateway import MT5Gateway
    from app.mt5.models import ConnectRequest, is_demo_account
    from app.mt5.symbols import SymbolResolver

    if not args.yes:
        print(
            "--mt5-trade-test sends a real order to your terminal. It only runs on "
            "DEMO accounts. Confirm by adding --yes."
        )
        return 2
    creds = _require_credentials(args)
    if creds is None:
        return 2
    login, server, password = creds

    gateway = MT5Gateway(mt5_factory=_import_mt5, request_timeout_s=30.0, name="cli")
    gateway.start()
    try:
        account = gateway.wait_for_result(
            gateway.connect(
                ConnectRequest(
                    login=login,
                    password=password,
                    server=server,
                    terminal_path=args.terminal_path.strip(),
                )
            ),
            "connect",
            30.0,
        )
        if not is_demo_account(account.trade_mode):
            print(
                f"REFUSED: account ***{str(account.login)[-3:]} is a "
                f"{account.mode_name.upper()} account. The trade test runs on demo only."
            )
            return 2
        # map the canonical symbol onto the broker's decorated name
        canonical = args.symbol.strip().upper()
        names = gateway.wait_for_result(gateway.symbol_names(), "symbol_names", 30.0)
        broker_symbol = SymbolResolver().resolve(canonical, names).broker_symbol
        outcome = run_demo_trade_test(gateway, broker_symbol, volume=args.volume)
        print(outcome.summary())
        for note in outcome.notes:
            print(f"  - {note}")
        return 0 if outcome.ok else 1
    except Exception as exc:
        print(f"TRADE TEST FAILED: {exc}")
        return 1
    finally:
        gateway.stop()


def run_db_check(args: argparse.Namespace) -> int:
    """Migrate the local database, print stats. Exit 0 = integrity OK."""
    import contextlib
    import pathlib

    from app.observability.paths import default_data_dir
    from app.storage.migrations import MigrationRunner
    from app.storage.service import StorageService

    data_dir = pathlib.Path(args.data_dir) if args.data_dir else default_data_dir()
    service = StorageService(data_dir)
    try:
        applied = MigrationRunner(service.db).run_all()
        stats = service.stats()
        stats["applied_migrations"] = [m.version for m in applied]
    except Exception as exc:
        print(f"DB CHECK FAILED: {exc}")
        return 1
    finally:
        with contextlib.suppress(Exception):
            service.db.close_all()

    if args.json:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    else:
        print(f"Database: {stats['path']}")
        print(
            f"Size: {stats['size_bytes'] / 1024:.1f} KB (WAL {stats['wal_size_bytes'] / 1024:.1f} KB)"
        )
        print(f"Schema version: {stats['version']}")
        print(f"Integrity: {'OK' if stats['integrity_ok'] else 'BROKEN'}")
        print(f"Backups kept: {stats['backups']}")
        print(f"Cloud sync: {'ON' if stats['cloud_enabled'] else 'OFF (local-only mode)'}")
        outbox = stats["outbox"]
        if outbox:
            print("Outbox: " + ", ".join(f"{k}={v}" for k, v in sorted(outbox.items())))
        for table, n in sorted(stats["rows"].items()):
            print(f"  {table}: {n}")
    return 0 if stats["integrity_ok"] else 1


def _build_market_service(bus: Any) -> tuple[Any, Any | None]:
    """Create the Phase 5 market analysis service (gateway optional).

    One long-lived gateway is owned by the app shell and shared with the
    analysis pipeline; the Settings card keeps its one-shot probes (the
    engine phases consolidate ownership). The gateway's live state is
    mirrored onto the event bus so the status bar can react. The calendar
    store lives under the data dir and is fed by the MQL5 exporter CSV
    (SPEC C3.9).
    """
    import time as _time

    from app.analysis.broker_time import BrokerClock
    from app.analysis.service import MarketAnalysisService
    from app.calendar.events import EventStore
    from app.calendar.importer import ExporterFilePoller
    from app.mt5.gateway import MT5Gateway

    def _mirror_state(state: Any) -> None:
        bus.gateway_state_changed.emit(state.value, "")

    gateway: MT5Gateway | None = None
    try:
        gateway = MT5Gateway(
            mt5_factory=_import_mt5,
            request_timeout_s=30.0,
            on_state_change=_mirror_state,
            name="shared",
        )
        gateway.start()
    except Exception:  # pragma: no cover - never block startup on MT5
        import loguru

        loguru.logger.warning("market: MT5 gateway unavailable — analysis offline")
        gateway = None

    calendar_dir = default_data_dir() / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    store = EventStore(calendar_dir / "events.csv")
    poller = ExporterFilePoller(store, calendar_dir / "exporter.csv", interval_s=300.0)
    clock = BrokerClock(utc_now_fn=_time.time)
    return MarketAnalysisService(
        gateway,
        watched=("EURUSD", "GBPUSD", "XAUUSD"),
        clock=clock,
        calendar_store=store,
        calendar_poller=poller,
    ), gateway


def _maybe_auto_connect(gateway: Any, bus: Any) -> None:
    """Connect at startup when the account is configured and allowed to."""
    from app.core.settings import Mt5AccountSettings
    from app.mt5.credentials import CredentialStore, CredentialStoreError
    from app.mt5.models import ConnectRequest
    from app.ui.workers import ConnectWorker

    account = Mt5AccountSettings.load()
    if not account.is_configured or not account.auto_connect or gateway is None:
        return
    try:
        password = CredentialStore().get_password(account.login) or ""
    except CredentialStoreError:
        password = ""
    if not password:
        return
    request = ConnectRequest(
        login=account.login,
        password=password,
        server=account.server,
        terminal_path=account.terminal_path,
    )

    def _on_result(ok: bool, detail: str) -> None:
        import loguru

        if ok:
            loguru.logger.info("startup: auto-connect OK ({})", detail)
        else:
            loguru.logger.warning("startup: auto-connect failed: {}", detail)
        bus.gateway_state_changed.emit("connected" if ok else "disconnected", detail)

    worker = ConnectWorker(gateway, request)
    worker.result_ready.connect(_on_result)
    worker.start()
    # keep a module-level reference so the thread is not garbage collected
    _maybe_auto_connect._worker = worker  # type: ignore[attr-defined]


def _install_qt_message_filter() -> None:
    """Route Qt messages to loguru; drop known third-party noise.

    pyqtgraph connects to ``QStyleHints.colorSchemeChanged`` with a
    UniqueConnection to a plain function, which Qt 6 logs as a warning
    on every import. The message is harmless — we swallow exactly that
    text and forward everything else.
    """
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    def _handler(mode: Any, context: Any, message: str) -> None:
        import loguru

        if "unique connections require a pointer to member function" in message:
            return
        if mode == QtMsgType.QtWarningMsg:
            loguru.logger.warning("qt: {}", message)
        elif mode == QtMsgType.QtCriticalMsg:
            loguru.logger.error("qt: {}", message)
        elif mode == QtMsgType.QtFatalMsg:
            loguru.logger.critical("qt: {}", message)
        else:
            loguru.logger.debug("qt: {}", message)

    qInstallMessageHandler(_handler)


def run_gui(debug: bool = False) -> int:
    """Compose the application and start the Qt event loop."""
    log_state = init_logging(logs_dir=default_logs_dir(), debug=debug)

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from app.core.event_bus import EventBus
    from app.core.settings import UiSettings, load_check_updates
    from app.observability import crash_handler
    from app.observability.logger import log_startup, shutdown_logging
    from app.storage.service import StorageService
    from app.ui.i18n.translator import Translator
    from app.ui.main_window import MainWindow
    from app.ui.pages.logs import LogsPage
    from app.ui.theme.manager import ThemeManager

    crash_handler.install_crash_handler(
        reports_dir=default_crash_reports_dir(),
        ring=log_state.ring,
    )
    log_startup()

    # High-DPI: honor the OS scale factor exactly (Qt6 default, set
    # explicitly so a rounding policy inherited from the environment
    # never blurs the UI). Must happen before QApplication exists.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    existing = QApplication.instance()
    qt_app: QApplication = (
        existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    )
    qt_app.setApplicationName("MT5 Trading Workstation")
    qt_app.setOrganizationName("MT5TradingWorkstation")
    qt_app.setStyle("Fusion")
    _install_qt_message_filter()

    settings = UiSettings.load()
    translator = Translator(settings)
    theme_manager = ThemeManager(settings)
    bus = EventBus()

    theme_manager.apply()
    qt_app.setApplicationDisplayName(translator.translate("app.title"))
    qt_app.setLayoutDirection(translator.layout_direction())

    # Storage opens before the window so the Settings card can show live stats.
    storage: StorageService | None = None
    try:
        storage = StorageService(default_data_dir())
        storage.open()
        storage.audit.app_started(__version__)
    except Exception as exc:
        # Storage failures must never take the whole app down at startup.
        import loguru

        loguru.logger.opt(exception=True).error(
            "storage: starting in local-safe mode failed: {}", exc
        )
        storage = None

    logs_page = LogsPage(translator, log_state.ring, log_state.logs_dir)

    # Market analysis (Phase 5): the gateway is optional — the page shows an
    # offline empty state until the user connects (Settings → Connect, or
    # the Phase 6 auto-connect with saved credentials).
    market_service, shared_gateway = _build_market_service(bus)

    def _reset_market_on_disconnect(state: str, _detail: str) -> None:
        # A reconnect may land on a different broker account with different
        # symbol decorations; stale bars would silently mix two price feeds.
        if state == "disconnected":
            market_service.invalidate_symbols()

    def _kick_market_on_connect(state: str, _detail: str) -> None:
        # Skip the up-to-60s wait for the next refresh tick: fetch as soon
        # as the terminal session is live so the Market page fills fast.
        if state == "connected":
            market_service.refresh_now()

    bus.gateway_state_changed.connect(_reset_market_on_disconnect)
    bus.gateway_state_changed.connect(_kick_market_on_connect)

    window = MainWindow(
        bus=bus,
        settings=settings,
        translator=translator,
        theme_manager=theme_manager,
        logs_page=logs_page,
        storage=storage,
        market_analysis=market_service,
        shared_gateway=shared_gateway,
    )
    window.show()

    # Phase 6 conveniences: reconnect automatically, then ask for updates —
    # both after the window is visible so startup stays snappy.
    QTimer.singleShot(1500, lambda: _maybe_auto_connect(shared_gateway, bus))
    QTimer.singleShot(2000, lambda: _resume_pending_update(window))
    if load_check_updates():
        QTimer.singleShot(4000, lambda: _startup_update_check(window))

    exit_code = qt_app.exec()

    if storage is not None:
        storage.audit.app_stopped(__version__)
        storage.close()
    shutdown_logging()
    return exit_code


def _resume_pending_update(window: Any) -> None:
    """Surface an update staged by a previous session (no network, no re-download)."""
    settings_page = window._pages.get("settings")
    if settings_page is not None:
        settings_page.resume_pending_update()


def _startup_update_check(window: Any) -> None:
    """Ask GitHub whether a newer release exists; toast when it does."""
    from app.__version__ import __version__
    from app.updater.service import UpdateService
    from app.updater.worker import UpdateWorker

    service = UpdateService(current_version=__version__)
    if not service.enabled:
        return
    window._startup_update_worker = UpdateWorker(service, "check")

    def _on_check(result: object, _error: str) -> None:
        from app.updater.service import CheckResult

        if isinstance(result, CheckResult) and result.available and result.plan:
            window.toast(
                window._translator.translate("updates.available", version=result.plan.new_version),
                window._translator.translate("updates.whats_new"),
                "info",
            )
            settings_page = window._pages.get("settings")
            if settings_page is not None:
                settings_page.apply_check_result(result)

    window._startup_update_worker.check_finished.connect(_on_check)
    window._startup_update_worker.start()


def run_apply_update_cli(args: argparse.Namespace) -> int:
    """Installer mode: swap a staged update, relaunch, return an exit code.

    Deliberately prints nothing to stdout — the helper usually runs
    console-less and detached (an invalid stdout makes ``print`` raise).
    Diagnostics go to ``update-apply.log`` instead.
    """
    import pathlib

    from app.updater.apply import run_apply_update
    from app.updater.service import app_install_dir

    staging_text = args.update_staging.strip()
    if not staging_text:
        return 2
    staging = pathlib.Path(staging_text)
    return run_apply_update(
        app_install_dir(),
        staging,
        parent_pid=args.update_pid,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"MT5 Trading Workstation {__version__}")
        return 0
    if args.self_check:
        return run_self_check()
    if args.mt5_smoke_test:
        return run_mt5_smoke_test(args)
    if args.mt5_trade_test:
        return run_mt5_trade_test(args)
    if args.db_check:
        return run_db_check(args)
    if args.apply_update:
        return run_apply_update_cli(args)
    return run_gui(debug=args.debug)


if __name__ == "__main__":
    raise SystemExit(main())
