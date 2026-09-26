"""Settings page — appearance, build info, and the MT5 connection card.

The connection card (Phase 3, SPEC G3-3) edits the persisted
:class:`~app.core.settings.Mt5AccountSettings`, stores the password only in
Windows Credential Manager via :class:`~app.mt5.credentials.CredentialStore`,
and runs :class:`~app.mt5.diagnostics.ConnectionProbe` behind a QTimer so
the UI thread never blocks (SPEC C3, I-8).
"""

from __future__ import annotations

import platform
import sys
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.__version__ import __version__
from app.core.event_bus import EventBus
from app.core.settings import Mt5AccountSettings
from app.mt5.credentials import CredentialStore, CredentialStoreError
from app.mt5.diagnostics import ConnectionProbe
from app.mt5.models import ConnectRequest
from app.ui.i18n.translator import Translator
from app.ui.theme.manager import ThemeManager

_PROBE_POLL_MS = 150


class SettingsPage(QWidget):
    """Appearance settings (theme, language), connection card, build info."""

    def __init__(
        self,
        translator: Translator,
        theme_manager: ThemeManager,
        parent: QWidget | None = None,
        *,
        account_settings: Mt5AccountSettings | None = None,
        credential_store: CredentialStore | None = None,
        mt5_factory: Any | None = None,
        bus: EventBus | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._theme_manager = theme_manager
        self._account_settings = account_settings or Mt5AccountSettings.load()
        self._credential_store = credential_store or CredentialStore()
        self._mt5_factory = mt5_factory
        self._bus = bus
        self._probe: ConnectionProbe | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.setSpacing(16)

        # -- appearance card -------------------------------------------------
        appearance = QFrame()
        appearance.setObjectName("Card")
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setContentsMargins(28, 24, 28, 24)
        appearance_layout.setSpacing(12)

        self._appearance_title = QLabel()
        self._appearance_title.setObjectName("CardTitle")

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        self._theme_label = QLabel()
        self._theme_combo = QComboBox()
        self._theme_combo.addItem("", "dark")
        self._theme_combo.addItem("", "light")
        self._theme_combo.setCurrentIndex(0 if theme_manager.current == "dark" else 1)
        self._theme_combo.activated.connect(self._on_theme_activated)

        self._language_label = QLabel()
        self._language_combo = QComboBox()
        self._language_combo.addItem("", "en")
        self._language_combo.addItem("", "fa")
        self._language_combo.setCurrentIndex(0 if translator.language == "en" else 1)
        self._language_combo.activated.connect(self._on_language_activated)

        form.addRow(self._theme_label, self._theme_combo)
        form.addRow(self._language_label, self._language_combo)
        appearance_layout.addWidget(self._appearance_title)
        appearance_layout.addLayout(form)
        outer.addWidget(appearance)

        # -- connection card (Phase 3) ----------------------------------------
        connection = QFrame()
        connection.setObjectName("Card")
        connection_layout = QVBoxLayout(connection)
        connection_layout.setContentsMargins(28, 24, 28, 24)
        connection_layout.setSpacing(12)

        self._connection_title = QLabel()
        self._connection_title.setObjectName("CardTitle")

        connection_form = QFormLayout()
        connection_form.setContentsMargins(0, 0, 0, 0)
        connection_form.setSpacing(10)

        self._login_label = QLabel()
        self._login_edit = QLineEdit()
        self._login_edit.setPlaceholderText(
            translator.translate("settings.account.login.placeholder")
        )

        self._server_label = QLabel()
        self._server_edit = QLineEdit()
        self._server_edit.setPlaceholderText(
            translator.translate("settings.account.server.placeholder")
        )

        self._terminal_label = QLabel()
        self._terminal_edit = QLineEdit()
        self._terminal_edit.setPlaceholderText(
            translator.translate("settings.account.terminal_path.placeholder")
        )

        self._password_label = QLabel()
        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._save_password_button = QPushButton()
        self._save_password_button.setObjectName("Badge")
        self._save_password_button.clicked.connect(self._on_save_password)
        password_row = QHBoxLayout()
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.addWidget(self._password_edit, 1)
        password_row.addWidget(self._save_password_button)

        connection_form.addRow(self._login_label, self._login_edit)
        connection_form.addRow(self._server_label, self._server_edit)
        connection_form.addRow(self._terminal_label, self._terminal_edit)
        connection_form.addRow(self._password_label, password_row)
        connection_layout.addWidget(self._connection_title)
        connection_layout.addLayout(connection_form)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self._test_button = QPushButton()
        self._test_button.setObjectName("PrimaryButton")
        self._test_button.clicked.connect(self._on_test_connection)
        self._result_label = QLabel()
        self._result_label.setWordWrap(True)
        actions.addWidget(self._test_button)
        actions.addWidget(self._result_label, 1)
        connection_layout.addLayout(actions)
        outer.addWidget(connection)

        # -- about card ------------------------------------------------------
        about = QFrame()
        about.setObjectName("Card")
        about_layout = QVBoxLayout(about)
        about_layout.setContentsMargins(28, 24, 28, 24)
        about_layout.setSpacing(12)

        self._about_title = QLabel()
        self._about_title.setObjectName("CardTitle")

        self._version_label = QLabel()
        self._python_label = QLabel()

        about_layout.addWidget(self._about_title)
        about_layout.addWidget(self._version_label)
        about_layout.addWidget(self._python_label)
        outer.addWidget(about)
        outer.addStretch(1)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_PROBE_POLL_MS)
        self._poll_timer.timeout.connect(self._poll_probe)

        self._load_account_into_form()
        translator.language_changed.connect(lambda _lang: self.retranslate())
        self.retranslate()

    # -- wiring --------------------------------------------------------------
    def _on_theme_activated(self, index: int) -> None:
        value = self._theme_combo.itemData(index)
        if isinstance(value, str):
            self._theme_manager.set_theme(value)

    def _on_language_activated(self, index: int) -> None:
        value = self._language_combo.itemData(index)
        if isinstance(value, str):
            self._translator.set_language(value)

    # -- connection card --------------------------------------------------------
    def _load_account_into_form(self) -> None:
        account = self._account_settings
        if account.login:
            self._login_edit.setText(str(account.login))
        if account.server:
            self._server_edit.setText(account.server)
        if account.terminal_path:
            self._terminal_edit.setText(account.terminal_path)

    def _persist_account(self) -> int:
        """Copy the form into the settings store; return the parsed login."""
        raw_login = self._login_edit.text().strip()
        login = int(raw_login) if raw_login.isdigit() else 0
        self._account_settings.login = max(login, 0)
        self._account_settings.server = self._server_edit.text()
        self._account_settings.terminal_path = self._terminal_edit.text()
        return login

    def _on_save_password(self) -> None:
        tr = self._translator.translate
        raw_login = self._login_edit.text().strip()
        password = self._password_edit.text()
        if not raw_login.isdigit() or int(raw_login) <= 0:
            self._result_label.setText(tr("settings.account.bad_login"))
            return
        if not password:
            self._result_label.setText(tr("settings.account.no_password"))
            return
        try:
            self._credential_store.set_password(int(raw_login), password)
        except CredentialStoreError as exc:
            self._result_label.setText(tr("settings.account.password_failed", error=str(exc)))
            return
        self._password_edit.clear()
        self._result_label.setText(tr("settings.account.password_saved"))

    def _on_test_connection(self) -> None:
        tr = self._translator.translate
        if self._probe is not None:  # a test is already running
            return
        login = self._persist_account()
        server = self._account_settings.server
        if login <= 0 or not server:
            self._result_label.setText(tr("settings.account.enter_login_server"))
            return
        password = self._password_edit.text()
        if not password:
            try:
                password = self._credential_store.get_password(login) or ""
            except CredentialStoreError:
                password = ""
        if not password:
            self._result_label.setText(tr("settings.account.no_password"))
            return
        request = ConnectRequest(
            login=login,
            password=password,
            server=server,
            terminal_path=self._account_settings.terminal_path,
        )
        self._probe = ConnectionProbe(request, self._mt5_factory)
        self._probe.start()
        self._test_button.setEnabled(False)
        self._result_label.setText(
            tr("settings.account.testing", step=tr("settings.probe.step.connect"))
        )
        self._poll_timer.start()

    def _poll_probe(self) -> None:
        if self._probe is None:
            self._poll_timer.stop()
            return
        tr = self._translator.translate
        state = self._probe.poll()
        if not state.finished:
            self._result_label.setText(
                tr("settings.account.testing", step=tr(f"settings.probe.step.{state.step}"))
            )
            return
        self._poll_timer.stop()
        self._test_button.setEnabled(True)
        self._probe = None
        key = "settings.account.test_ok" if state.ok else "settings.account.test_failed"
        self._result_label.setText(tr(key, detail=state.detail))
        if self._bus is not None:
            self._bus.mt5_connection_changed.emit(state.ok, state.detail)

    # -- retranslation ---------------------------------------------------------
    def retranslate(self) -> None:
        """Refresh all texts for the current language."""
        tr = self._translator.translate
        self._appearance_title.setText(tr("settings.appearance"))
        self._theme_label.setText(tr("settings.theme"))
        self._language_label.setText(tr("settings.language"))
        self._about_title.setText(tr("settings.about"))

        self._theme_combo.setItemText(0, tr("settings.theme.dark"))
        self._theme_combo.setItemText(1, tr("settings.theme.light"))
        self._language_combo.setItemText(0, tr("settings.language.en"))
        self._language_combo.setItemText(1, tr("settings.language.fa"))
        # keep placeholder width stable with longest item
        self._theme_combo.setFixedWidth(220)
        self._language_combo.setFixedWidth(220)

        self._connection_title.setText(tr("settings.connection"))
        self._login_label.setText(tr("settings.account.login"))
        self._server_label.setText(tr("settings.account.server"))
        self._terminal_label.setText(tr("settings.account.terminal_path"))
        self._password_label.setText(tr("settings.account.password"))
        self._save_password_button.setText(tr("settings.account.save_password"))
        self._test_button.setText(tr("settings.account.test_connection"))
        self._login_edit.setPlaceholderText(tr("settings.account.login.placeholder"))
        self._server_edit.setPlaceholderText(tr("settings.account.server.placeholder"))
        self._terminal_edit.setPlaceholderText(tr("settings.account.terminal_path.placeholder"))

        self._version_label.setText(f"{tr('settings.version')}: {__version__}")
        self._python_label.setText(
            f"{tr('settings.python')}: {platform.python_version()} "
            f"({sys.platform}, {platform.machine()})"
        )
