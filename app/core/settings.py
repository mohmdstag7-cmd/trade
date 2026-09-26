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
_KEY_MT5_AUTO_CONNECT = "mt5/auto_connect"

# -- Updates (Phase 6) ---------------------------------------------------------
_KEY_CHECK_UPDATES = "ui/check_updates"

# -- Cloud mirror (Phase 4) ---------------------------------------------------
_KEY_CLOUD_URL = "cloud/supabase_url"

#: The Supabase service key is NEVER stored here — only in the OS vault
#: via :class:`app.storage.vault.KeyringVault` (SPEC C11, I-6).


def load_cloud_url() -> str:
    """Configured Supabase project URL ('' when unset). Not a secret."""
    qs = QSettings(_ORG, _APP)
    return str(qs.value(_KEY_CLOUD_URL, "")).strip()


def save_cloud_url(url: str) -> None:
    """Persist the Supabase project URL (normalised without trailing slash)."""
    qs = QSettings(_ORG, _APP)
    qs.setValue(_KEY_CLOUD_URL, url.strip().rstrip("/"))
    qs.sync()


def load_check_updates() -> bool:
    """Whether to ask for a newer release at startup (default: on)."""
    qs = QSettings(_ORG, _APP)
    return bool(qs.value(_KEY_CHECK_UPDATES, True, type=bool))


def save_check_updates(value: bool) -> None:
    """Persist the startup update-check preference."""
    qs = QSettings(_ORG, _APP)
    qs.setValue(_KEY_CHECK_UPDATES, value)
    qs.sync()


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

    # -- updates ------------------------------------------------------------
    @property
    def check_updates_on_startup(self) -> bool:
        """Whether the app asks GitHub for a newer release at startup."""
        return bool(self._qs.value(_KEY_CHECK_UPDATES, True, type=bool))

    @check_updates_on_startup.setter
    def check_updates_on_startup(self, value: bool) -> None:
        self._qs.setValue(_KEY_CHECK_UPDATES, value)
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

    # -- auto connect ----------------------------------------------------------
    @property
    def auto_connect(self) -> bool:
        """Connect with the saved account automatically at startup."""
        return bool(self._qs.value(_KEY_MT5_AUTO_CONNECT, True, type=bool))

    @auto_connect.setter
    def auto_connect(self, value: bool) -> None:
        self._qs.setValue(_KEY_MT5_AUTO_CONNECT, value)
        self._qs.sync()

    # -- helpers ---------------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        """True when login and server are both present."""
        return self.login > 0 and bool(self.server)
