"""Main window: sidebar + stacked pages + status bar + command palette."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt
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
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MainWindow")
        self._bus = bus
        self._settings = settings
        self._translator = translator
        self._theme_manager = theme_manager
        self._logs_page = logs_page

        self.setWindowTitle(translator.translate("app.title"))
        self.setMinimumSize(1024, 640)
        self.resize(1280, 800)

        # -- central layout: sidebar + pages ---------------------------------
        central = QWidget()
        central.setObjectName("CentralArea")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._sidebar = Sidebar(translator, settings, self)
        self._stack = QStackedWidget(central)

        self._pages: dict[str, QWidget] = {}
        for meta in PAGES:
            if meta.key == "settings":
                page: QWidget = SettingsPage(translator, theme_manager, self)
            elif meta.key == "logs" and logs_page is not None:
                page = logs_page
            else:
                page = EmptyStatePage(meta, translator, self)
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

        # -- command palette ----------------------------------------------------
        self._palette = CommandPalette(translator, self)

        # -- wiring ---------------------------------------------------------------
        self._sidebar.navigate.connect(self.switch_page)
        bus.navigate_requested.connect(self.switch_page)
        translator.language_changed.connect(self._on_language_changed)

        self._palette_shortcut = QShortcut(QKeySequence(self.PALETTE_SHORTCUT), self)
        self._palette_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._palette_shortcut.activated.connect(self.open_palette)

        # -- initial state ---------------------------------------------------------
        self.switch_page("dashboard")

    # -- public API ------------------------------------------------------------
    def open_palette(self) -> None:
        """Open the Ctrl+K command palette."""
        self._palette.set_commands(self._build_commands())
        self._palette.open_at(self)

    def switch_page(self, key: str) -> None:
        """Navigate to page ``key`` (raises ``KeyError`` for unknown keys)."""
        page_meta(key)  # validate
        self._stack.setCurrentWidget(self._pages[key])
        self._sidebar.set_active(key)

    @property
    def page_keys(self) -> tuple[str, ...]:
        """Keys of all registered pages, in display order."""
        return tuple(meta.key for meta in PAGES)

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

    def _on_language_changed(self, _language: str) -> None:
        self.setWindowTitle(self._translator.translate("app.title"))
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setLayoutDirection(self._translator.layout_direction())

    def _quit(self) -> None:
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()
