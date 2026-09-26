"""Typed, persisted application settings backed by QSettings.

Design decisions:
- QSettings with the INI backend keeps Phase 1 dependency-free; the pydantic
  settings stack arrives with engine configuration (Phase 6+).
- Values are validated on read; invalid or missing values fall back to
  defaults so a corrupted settings file can never crash the app.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

THEME_DARK = "dark"
THEME_LIGHT = "light"
VALID_THEMES = (THEME_DARK, THEME_LIGHT)

LANGUAGE_EN = "en"
LANGUAGE_FA = "fa"
VALID_LANGUAGES = (LANGUAGE_EN, LANGUAGE_FA)

_ORG = "MT5TradingWorkstation"
_APP = "workstation"

_KEY_THEME = "ui/theme"
_KEY_LANGUAGE = "ui/language"
_KEY_SIDEBAR_COLLAPSED = "ui/sidebar_collapsed"

# -- MT5 connection (Phase 3) -----------------------------------------------
_KEY_MT5_LOGIN = "mt5/login"
_KEY_MT5_SERVER = "mt5/server"
_KEY_MT5_TERMINAL_PATH = "mt5/terminal_path"

#: The password is NEVER stored here — only in Windows Credential Manager
#: via :class:`app.mt5.credentials.CredentialStore` (SPEC C11, I-6).


class UiSettings:
    """Typed wrapper around the persisted UI settings."""

    def __init__(self, qsettings: QSettings) -> None:
        self._qs = qsettings

    @classmethod
    def load(cls, path: str | None = None) -> UiSettings:
        """Load settings. ``path`` is used by tests; default is the platform store."""
        if path is None:
            return cls(QSettings(_ORG, _APP))
        return cls(QSettings(path, QSettings.Format.IniFormat))

    # -- theme ------------------------------------------------------------
    @property
    def theme(self) -> str:
        value = str(self._qs.value(_KEY_THEME, THEME_DARK))
        return value if value in VALID_THEMES else THEME_DARK

    @theme.setter
    def theme(self, value: str) -> None:
        if value not in VALID_THEMES:
            msg = f"invalid theme: {value!r}"
            raise ValueError(msg)
        self._qs.setValue(_KEY_THEME, value)
        self._qs.sync()

    # -- language ---------------------------------------------------------
    @property
    def language(self) -> str:
        value = str(self._qs.value(_KEY_LANGUAGE, LANGUAGE_EN))
        return value if value in VALID_LANGUAGES else LANGUAGE_EN

    @language.setter
    def language(self, value: str) -> None:
        if value not in VALID_LANGUAGES:
            msg = f"invalid language: {value!r}"
            raise ValueError(msg)
        self._qs.setValue(_KEY_LANGUAGE, value)
        self._qs.sync()

    # -- sidebar ----------------------------------------------------------
    @property
    def sidebar_collapsed(self) -> bool:
        return bool(self._qs.value(_KEY_SIDEBAR_COLLAPSED, False, type=bool))

    @sidebar_collapsed.setter
    def sidebar_collapsed(self, value: bool) -> None:
        self._qs.setValue(_KEY_SIDEBAR_COLLAPSED, value)
        self._qs.sync()

    # -- maintenance ------------------------------------------------------
    def sync(self) -> None:
        """Flush pending changes to the backing store."""
        self._qs.sync()


class Mt5AccountSettings:
    """Typed, persisted MT5 connection parameters (no password here).

    The password travels only between Windows Credential Manager and an
    in-memory :class:`~app.mt5.models.ConnectRequest` built by the caller.
    """

    def __init__(self, qsettings: QSettings) -> None:
        self._qs = qsettings

    @classmethod
    def load(cls, path: str | None = None) -> Mt5AccountSettings:
        """Load settings; ``path`` is for tests (same convention as UiSettings)."""
        if path is None:
            return cls(QSettings(_ORG, _APP))
        return cls(QSettings(path, QSettings.Format.IniFormat))

    # -- login ---------------------------------------------------------------
    @property
    def login(self) -> int:
        """Account number; ``0`` means not configured yet."""
        try:
            return int(str(self._qs.value(_KEY_MT5_LOGIN, 0)).strip() or 0)
        except (TypeError, ValueError):
            return 0

    @login.setter
    def login(self, value: int) -> None:
        if value < 0:
            msg = f"invalid login: {value!r}"
            raise ValueError(msg)
        self._qs.setValue(_KEY_MT5_LOGIN, value)
        self._qs.sync()

    # -- server -------------------------------------------------------------
    @property
    def server(self) -> str:
        """Broker server name, e.g. ``MetaQuotes-Demo``."""
        return str(self._qs.value(_KEY_MT5_SERVER, "")).strip()

    @server.setter
    def server(self, value: str) -> None:
        self._qs.setValue(_KEY_MT5_SERVER, value.strip())
        self._qs.sync()

    # -- terminal path --------------------------------------------------------
    @property
    def terminal_path(self) -> str:
        """Optional explicit path to ``terminal64.exe`` (empty = autodetect)."""
        return str(self._qs.value(_KEY_MT5_TERMINAL_PATH, "")).strip()

    @terminal_path.setter
    def terminal_path(self, value: str) -> None:
        self._qs.setValue(_KEY_MT5_TERMINAL_PATH, value.strip())
        self._qs.sync()

    # -- helpers ---------------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        """True when login and server are both present."""
        return self.login > 0 and bool(self.server)
