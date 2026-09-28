"""Main window: sidebar + stacked pages + status bar + command palette + toasts.

v2 (Phase 6): window geometry/maximized state persists across sessions,
a toast host overlays transient notifications, the shared gateway's live
connection state reaches the status bar, and every page receives the
theme manager for icon theming.
"""

from __future__ import annotations

import contextlib
from functools import partial
from typing import Any

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from app.__version__ import __version__
from app.core.event_bus import EventBus
from app.core.settings import UiSettings
from app.ui.i18n.translator import Translator
from app.ui.pages.base import PAGES, EmptyStatePage, page_meta
from app.ui.pages.settings import SettingsPage
from app.ui.theme.manager import ThemeManager
from app.ui.widgets.command_palette import Command, CommandPalette
from app.ui.widgets.sidebar import Sidebar
from app.ui.widgets.status_bar import StatusBar
from app.ui.widgets.toast import ToastHost

_ORG = "MT5TradingWorkstation"
_APP = "workstation"
_KEY_GEOMETRY = "ui/geometry"
_KEY_WINDOW_STATE = "ui/window_state"

#: Comfortable default; the stored geometry takes precedence when present.
_DEFAULT_SIZE = (1320, 840)
_MIN_SIZE = (1020, 640)


class MainWindow(QMainWindow):
    """Top-level window composing the whole UI shell."""

    #: Palette shortcut (SPEC F2).
    PALETTE_SHORTCUT = "Ctrl+K"

    def __init__(
        self,
        bus: EventBus,
        settings: UiSettings,
        translator: Translator,
        theme_manager: ThemeManager,
        logs_page: QWidget | None = None,
        parent: QWidget | None = None,
        *,
        storage: Any | None = None,
        market_analysis: Any | None = None,
        shared_gateway: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MainWindow")
        self._bus = bus
        self._settings = settings
        self._translator = translator
        self._theme_manager = theme_manager
        self._logs_page = logs_page
        self._storage = storage
        self._market_analysis = market_analysis
        self._shared_gateway = shared_gateway

        self.setWindowTitle(translator.translate("app.title"))
        self.setMinimumSize(*_MIN_SIZE)
        self.resize(*_DEFAULT_SIZE)

        # -- central layout: sidebar + pages ---------------------------------
        central = QWidget()
        central.setObjectName("CentralArea")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._sidebar = Sidebar(translator, settings, self, theme_manager=theme_manager)
        self._stack = QStackedWidget(central)

        self._pages: dict[str, QWidget] = {}
        for meta in PAGES:
            if meta.key == "settings":
                page: QWidget = SettingsPage(
                    translator,
                    theme_manager,
                    self,
                    bus=bus,
                    storage=storage,
                    shared_gateway=shared_gateway,
                )
            elif meta.key == "logs" and logs_page is not None:
                page = logs_page
            elif meta.key == "market":
                from app.ui.pages.market import MarketPage

                page = MarketPage(
                    translator,
                    theme_manager,
                    self,
                    service=market_analysis,
                )
            else:
                page = EmptyStatePage(meta, translator, self, theme_manager=theme_manager)
            self._pages[meta.key] = page
            self._stack.addWidget(page)

        layout.addWidget(self._sidebar)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        # -- status bar --------------------------------------------------------
        self._status_bar = StatusBar(
            translator,
            __version__,
            on_toggle_theme=theme_manager.toggle,
            on_toggle_language=self._toggle_language,
            parent=self,
        )
        self.setStatusBar(self._status_bar)

        # -- toasts --------------------------------------------------------------
        self._toasts = ToastHost(self, theme_manager)

        # -- command palette ----------------------------------------------------
        self._palette = CommandPalette(translator, self)

        # -- wiring ---------------------------------------------------------------
        self._sidebar.navigate.connect(self.switch_page)
        bus.navigate_requested.connect(self.switch_page)
        bus.mt5_connection_changed.connect(self._on_probe_result)
        bus.gateway_state_changed.connect(self._on_gateway_state)
        translator.language_changed.connect(self._on_language_changed)

        self._palette_shortcut = QShortcut(QKeySequence(self.PALETTE_SHORTCUT), self)
        self._palette_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._palette_shortcut.activated.connect(self.open_palette)

        # -- initial state ---------------------------------------------------------
        self._restore_window()
        self.switch_page("dashboard")

    # -- public API ------------------------------------------------------------
    def open_palette(self) -> None:
        """Open the Ctrl+K command palette."""
        self._palette.set_commands(self._build_commands())
        self._palette.open_at(self)

    def toast(self, title: str, body: str = "", variant: str = "info") -> None:
        """Show a transient toast notification."""
        self._toasts.show_toast(title, body, variant)

    def switch_page(self, key: str) -> None:
        """Navigate to page ``key`` (raises ``KeyError`` for unknown keys)."""
        page_meta(key)  # validate
        self._stack.setCurrentWidget(self._pages[key])
        self._sidebar.set_active(key)

    @property
    def page_keys(self) -> tuple[str, ...]:
        """Keys of all registered pages, in display order."""
        return tuple(meta.key for meta in PAGES)

    # -- window state -------------------------------------------------------------
    def _restore_window(self) -> None:
        """Restore the persisted geometry (falls back to the default size)."""
        qs = QSettings(_ORG, _APP)
        geometry = qs.value(_KEY_GEOMETRY)
        if isinstance(geometry, QByteArray) and self.restoreGeometry(geometry):
            state = qs.value(_KEY_WINDOW_STATE)
            if isinstance(state, QByteArray):
                self.restoreState(state)
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(_DEFAULT_SIZE[0], available.width() - 40)
            height = min(_DEFAULT_SIZE[1], available.height() - 40)
            self.resize(width, height)

    def _persist_window(self) -> None:
        qs = QSettings(_ORG, _APP)
        qs.setValue(_KEY_GEOMETRY, self.saveGeometry())
        qs.setValue(_KEY_WINDOW_STATE, self.saveState())
        qs.sync()

    def closeEvent(self, event: Any) -> None:
        """Persist geometry and stop/await owned QThread workers.

        Quitting while a parentless QThread runs would abort with
        "QThread destroyed while running" (qFatal) — the connect worker
        alone can wait up to 90 s on a cold terminal start. QThread.quit()
        only exits an event loop and does not interrupt a blocking
        gateway.wait_for_result() call, so we cooperatively interrupt,
        disconnect signals, and detach still-running threads.
        """
        self._persist_window()
        # Keep references to threads that are still running after timeout so
        # the QThread object is not destroyed while the OS thread is alive.
        closing_workers: list[Any] = getattr(self, "_closing_workers", [])
        # Ensure attribute exists for future closeEvents
        self._closing_workers = closing_workers
        settings_page = self._pages.get("settings")
        if settings_page is not None:
            for attr in ("_connect_worker", "_update_worker", "_elevation_worker"):
                worker = getattr(settings_page, attr, None)
                if worker is not None:
                    try:
                        # Try to unblock a blocking gateway call
                        gateway = (
                            getattr(worker, "_gateway", None)
                            or getattr(worker, "_shared_gateway", None)
                            or getattr(worker, "gateway", None)
                        )
                        if gateway is not None:
                            for meth in ("cancel", "disconnect", "close", "interrupt"):
                                if hasattr(gateway, meth):
                                    with contextlib.suppress(Exception):
                                        getattr(gateway, meth)()
                                    break
                        with contextlib.suppress(Exception):
                            worker.requestInterruption()
                        with contextlib.suppress(Exception):
                            worker.blockSignals(True)
                        for sig_name in (
                            "result_ready",
                            "check_finished",
                            "stage_finished",
                            "progress",
                            "start_result",
                        ):
                            sig = getattr(worker, sig_name, None)
                            if sig is not None:
                                with contextlib.suppress(Exception):
                                    sig.disconnect()
                        with contextlib.suppress(Exception):
                            worker.finished.disconnect()
                        worker.quit()
                        if worker.wait(3000):
                            with contextlib.suppress(RuntimeError):
                                worker.deleteLater()
                        else:
                            with contextlib.suppress(Exception):
                                worker.setParent(None)
                            with contextlib.suppress(Exception):
                                worker.finished.connect(worker.deleteLater)
                            closing_workers.append(worker)
                    except RuntimeError:
                        pass  # already gone
                    setattr(settings_page, attr, None)
        startup_worker = getattr(self, "_startup_update_worker", None)
        if startup_worker is not None:
            try:
                with contextlib.suppress(Exception):
                    startup_worker.requestInterruption()
                with contextlib.suppress(Exception):
                    startup_worker.blockSignals(True)
                with contextlib.suppress(Exception):
                    startup_worker.finished.disconnect()
                startup_worker.quit()
                if startup_worker.wait(3000):
                    with contextlib.suppress(RuntimeError):
                        startup_worker.deleteLater()
                else:
                    with contextlib.suppress(Exception):
                        startup_worker.setParent(None)
                    with contextlib.suppress(Exception):
                        startup_worker.finished.connect(startup_worker.deleteLater)
                    closing_workers.append(startup_worker)
            except RuntimeError:
                pass
            self._startup_update_worker = None
        super().closeEvent(event)

    # -- internals ---------------------------------------------------------------
    def _build_commands(self) -> list[Command]:
        tr = self._translator.translate
        commands: list[Command] = []
        for meta in PAGES:
            title = tr("command.goto", page=tr(meta.title_key))
            commands.append(
                Command(
                    id=f"goto.{meta.key}",
                    title=title,
                    callback=partial(self.switch_page, meta.key),
                )
            )
        commands.append(
            Command(
                id="toggle.theme",
                title=tr("command.toggle_theme"),
                callback=self._theme_manager.toggle,
            )
        )
        commands.append(
            Command(
                id="toggle.language",
                title=tr("command.toggle_language"),
                callback=self._toggle_language,
            )
        )
        commands.append(
            Command(
                id="app.quit",
                title=tr("command.quit"),
                callback=self._quit,
            )
        )
        return commands

    def _toggle_language(self) -> None:
        other = "fa" if self._translator.language == "en" else "en"
        self._translator.set_language(other)

    def _on_probe_result(self, ok: bool, detail: str) -> None:
        """One-shot probe result (Settings → Test connection)."""
        self._status_bar.set_connection_state(ok, detail)
        key = "toast.probe.ok" if ok else "toast.probe.failed"
        self.toast(
            self._translator.translate(key),
            detail,
            "ok" if ok else "bad",
        )

    def _on_gateway_state(self, state: str, detail: str) -> None:
        """Live shared-gateway state (connecting / connected / reconnecting…)."""
        self._status_bar.set_gateway_state(state, detail)

    def _on_language_changed(self, _language: str) -> None:
        self.setWindowTitle(self._translator.translate("app.title"))
        app = QApplication.instance()
        if isinstance(app, QApplication):
            direction = self._translator.layout_direction()
            app.setLayoutDirection(direction)
            # Regenerate QSS so physical border sides mirror under RTL.
            self._theme_manager.apply(rtl=direction == Qt.LayoutDirection.RightToLeft)
            # The window title updates above, but the platform display name
            # (taskbar grouping) kept the previous language all session.
            app.setApplicationDisplayName(self._translator.translate("app.title"))

    def _quit(self) -> None:
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()
