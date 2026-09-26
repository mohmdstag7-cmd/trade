"""Settings page — appearance, updates, MT5 connection (probe + persistent
connect), storage & sync, about. v2: centered scroll column, consistent
card rhythm, in-app delta updates and a persistent Connect/Disconnect
that drives the shared gateway.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.__version__ import __version__
from app.core.event_bus import EventBus
from app.core.settings import (
    Mt5AccountSettings,
    load_check_updates,
    load_cloud_url,
    save_check_updates,
    save_cloud_url,
)
from app.mt5.credentials import CredentialStore, CredentialStoreError
from app.mt5.diagnostics import ConnectionProbe
from app.mt5.models import ConnectRequest
from app.storage.cloud_probe import CloudProbe
from app.storage.mirror import SUPABASE_KEY_ENTRY
from app.storage.vault import KeyringVault, VaultError
from app.ui.i18n.translator import Translator
from app.ui.theme.manager import ThemeManager
from app.ui.workers import ConnectWorker
from app.updater.service import (
    CheckResult,
    UpdateService,
    app_dir_writable,
    read_apply_log_tail,
)
from app.updater.worker import UpdateWorker

_PROBE_POLL_MS = 150
_STORAGE_POLL_MS = 2000

#: characters that never appear in legitimate MT5 server names ("FIBOGroup-MT5
#: Server", "MetaQuotes-Demo"); their presence usually means a secret (the
#: password) was pasted into the Server field by mistake.
_SERVER_SUSPICIOUS_RE = re.compile(r"[&!@$%^*+=~|<>{}\[\];?`#\"']")

#: Maximum width of the centered settings column.
COLUMN_WIDTH = 780


def _card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("Card")
    frame.setMaximumWidth(COLUMN_WIDTH)
    policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    frame.setSizePolicy(policy)
    return frame


class SettingsPage(QWidget):
    """Appearance settings, connection card, storage card, about."""

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
        storage: Any | None = None,
        shared_gateway: Any | None = None,
        updater: UpdateService | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._theme_manager = theme_manager
        self._account_settings = account_settings or Mt5AccountSettings.load()
        self._credential_store = credential_store or CredentialStore()
        self._mt5_factory = mt5_factory
        self._bus = bus
        self._storage = storage
        self._shared_gateway = shared_gateway
        self._updater = updater or UpdateService(current_version=__version__)
        self._update_worker: UpdateWorker | None = None
        self._staged_version: str | None = None
        self._probe: ConnectionProbe | None = None
        self._cloud_probe: CloudProbe | None = None
        self._connect_worker: ConnectWorker | None = None
        self._vault = KeyringVault()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        host = QWidget()
        host.setStyleSheet("background: transparent;")
        scroll.setWidget(host)

        column = QVBoxLayout(host)
        column.setContentsMargins(32, 28, 32, 28)
        column.setSpacing(16)

        # centered fixed-width column that degrades gracefully on narrow windows
        self._column = QVBoxLayout()
        self._column.setSpacing(16)
        self._column.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addLayout(self._column, 0)
        column.addStretch(1)

        # -- appearance card -------------------------------------------------
        appearance = _card()
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setContentsMargins(24, 20, 24, 20)
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
        self._theme_combo.setMinimumWidth(200)
        self._theme_combo.activated.connect(self._on_theme_activated)

        self._language_label = QLabel()
        self._language_combo = QComboBox()
        self._language_combo.addItem("", "en")
        self._language_combo.addItem("", "fa")
        self._language_combo.setCurrentIndex(0 if translator.language == "en" else 1)
        self._language_combo.setMinimumWidth(200)
        self._language_combo.activated.connect(self._on_language_activated)

        form.addRow(self._theme_label, self._theme_combo)
        form.addRow(self._language_label, self._language_combo)
        appearance_layout.addWidget(self._appearance_title)
        appearance_layout.addLayout(form)
        self._column.addWidget(appearance)

        # -- updates card (Phase 6) -------------------------------------------
        self._build_updates_card()

        # -- connection card (Phase 3 probe + Phase 6 persistent connect) ------
        connection = _card()
        connection_layout = QVBoxLayout(connection)
        connection_layout.setContentsMargins(24, 20, 24, 20)
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
        self._server_hint = QLabel()
        self._server_hint.setObjectName("FaintLabel")
        self._server_hint.setWordWrap(True)
        self._server_hint.hide()
        self._server_edit.textChanged.connect(self._update_server_hint)

        self._terminal_label = QLabel()
        self._terminal_edit = QLineEdit()
        self._terminal_edit.setPlaceholderText(
            translator.translate("settings.account.terminal_path.placeholder")
        )

        self._password_label = QLabel()
        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._save_password_button = QPushButton()
        self._save_password_button.setObjectName("GhostButton")
        self._save_password_button.clicked.connect(self._on_save_password)
        password_row = QHBoxLayout()
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.addWidget(self._password_edit, 1)
        password_row.addWidget(self._save_password_button)

        connection_form.addRow(self._login_label, self._login_edit)
        connection_form.addRow(self._server_label, self._server_edit)
        connection_form.addRow(self._server_hint)
        connection_form.addRow(self._terminal_label, self._terminal_edit)
        connection_form.addRow(self._password_label, password_row)
        connection_layout.addWidget(self._connection_title)
        connection_layout.addLayout(connection_form)

        self._auto_connect_check = QCheckBox()
        self._auto_connect_check.setChecked(self._account_settings.auto_connect)
        self._auto_connect_check.toggled.connect(self._on_auto_connect_toggled)
        connection_layout.addWidget(self._auto_connect_check)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self._connect_button = QPushButton()
        self._connect_button.setObjectName("AccentButton")
        self._connect_button.clicked.connect(self._on_connect_clicked)
        self._test_button = QPushButton()
        self._test_button.setObjectName("GhostButton")
        self._test_button.clicked.connect(self._on_test_connection)
        self._result_label = QLabel()
        self._result_label.setWordWrap(True)
        actions.addWidget(self._connect_button)
        actions.addWidget(self._test_button)
        actions.addWidget(self._result_label, 1)
        connection_layout.addLayout(actions)
        self._column.addWidget(connection)

        # -- storage & sync card (Phase 4) --------------------------------------
        if self._storage is not None:
            self._build_storage_card()

        # -- about card ------------------------------------------------------
        about = _card()
        about_layout = QVBoxLayout(about)
        about_layout.setContentsMargins(24, 20, 24, 20)
        about_layout.setSpacing(12)

        self._about_title = QLabel()
        self._about_title.setObjectName("CardTitle")

        self._version_label = QLabel()
        self._version_label.setObjectName("MonoLabel")
        self._python_label = QLabel()
        self._python_label.setObjectName("FaintLabel")

        about_layout.addWidget(self._about_title)
        about_layout.addWidget(self._version_label)
        about_layout.addWidget(self._python_label)
        self._column.addWidget(about)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_PROBE_POLL_MS)
        self._poll_timer.timeout.connect(self._poll_probe)

        self._storage_timer: QTimer | None = None
        if self._storage is not None:
            self._storage_timer = QTimer(self)
            self._storage_timer.setInterval(_STORAGE_POLL_MS)
            self._storage_timer.timeout.connect(self._refresh_storage_stats)
            self._storage_timer.start()
            self._load_cloud_into_form()

        self._load_account_into_form()
        translator.language_changed.connect(lambda _lang: self.retranslate())
        self.retranslate()

    # -- updates card (Phase 6) ------------------------------------------------
    def _build_updates_card(self) -> None:
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._updates_title = QLabel()
        self._updates_title.setObjectName("CardTitle")
        layout.addWidget(self._updates_title)

        self._update_version_label = QLabel()
        self._update_version_label.setObjectName("MonoLabel")
        layout.addWidget(self._update_version_label)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._update_check_button = QPushButton()
        self._update_check_button.setObjectName("AccentButton")
        self._update_check_button.clicked.connect(self._on_check_updates)
        row.addWidget(self._update_check_button)
        self._update_restart_button = QPushButton()
        self._update_restart_button.setObjectName("DangerButton")
        self._update_restart_button.setVisible(False)
        self._update_restart_button.clicked.connect(self._on_restart_and_install)
        row.addWidget(self._update_restart_button)
        self._update_status_label = QLabel()
        self._update_status_label.setWordWrap(True)
        row.addWidget(self._update_status_label, 1)
        layout.addLayout(row)

        self._update_progress = QProgressBar()
        self._update_progress.setVisible(False)
        self._update_progress.setRange(0, 100)
        layout.addWidget(self._update_progress)

        self._update_startup_check = QCheckBox()
        self._update_startup_check.setChecked(load_check_updates())
        self._update_startup_check.toggled.connect(self._on_startup_check_toggled)
        layout.addWidget(self._update_startup_check)
        if not self._updater.enabled:
            self._update_check_button.setEnabled(False)
            self._update_status_label.setText(self._tr("updates.dev_mode"))
        self._column.addWidget(card)

    def _tr(self, key: str, **params: Any) -> str:
        return self._translator.translate(key, **params)

    def _on_startup_check_toggled(self, checked: bool) -> None:
        save_check_updates(checked)

    def _on_check_updates(self) -> None:
        if self._update_worker is not None:
            return
        self._update_check_button.setEnabled(False)
        self._update_status_label.setText(self._tr("updates.checking"))
        self._update_worker = UpdateWorker(self._updater, "update")
        self._update_worker.check_finished.connect(self._on_update_check_done)
        self._update_worker.progress.connect(self._on_update_progress)
        self._update_worker.stage_finished.connect(self._on_update_staged)
        self._update_worker.start()

    def _on_update_check_done(self, result: object, error: str) -> None:
        tr = self._tr
        if error:
            self._update_status_label.setText(tr("updates.failed", error=error))
            self._update_check_button.setEnabled(True)
            return
        assert isinstance(result, CheckResult)
        if result.error:
            self._update_status_label.setText(tr("updates.failed", error=result.error))
            self._update_check_button.setEnabled(True)
            return
        if not result.available:
            self._update_status_label.setText(tr("updates.up_to_date", version=__version__))
            self._update_check_button.setEnabled(True)

    def _on_update_progress(self, done: int, total: object) -> None:
        self._update_progress.setVisible(True)
        if isinstance(total, int) and total > 0:
            self._update_progress.setRange(0, 100)
            pct = min(100, int(done * 100 / total))
            self._update_progress.setValue(pct)
            self._update_status_label.setText(self._tr("updates.downloading", pct=pct))
        else:
            self._update_progress.setRange(0, 0)  # busy indicator

    def _on_update_staged(self, ok: bool, message: str, _staging: object) -> None:
        self._update_worker = None
        self._update_progress.setVisible(False)
        self._update_progress.setRange(0, 100)
        if ok:
            self._staged_version = message
            self._update_status_label.setText(self._tr("updates.staged", version=message))
            self._update_restart_button.setVisible(True)
            self._update_check_button.setEnabled(False)
        else:
            self._update_status_label.setText(self._tr("updates.failed", error=message))
            self._update_check_button.setEnabled(True)

    def _on_restart_and_install(self) -> None:
        """Launch the apply script detached, then quit so it can swap files.

        Never a silent no-op: every failure mode (staged version missing,
        script missing, spawn error) lands a visible message on the
        updates card — the old version returned silently and users kept
        clicking a button that did nothing.
        """
        if self._staged_version is None:
            return
        if os.name != "nt":
            self._update_status_label.setText(self._tr("updates.restart_windows_only"))
            return
        script = self._updater.apply_script_path(self._staged_version)
        if not script.is_file():
            self._update_status_label.setText(self._tr("updates.staged_missing"))
            self._update_restart_button.setVisible(False)
            self._update_check_button.setEnabled(True)
            self._staged_version = None
            return
        self._update_status_label.setText(
            self._tr("updates.restarting", version=self._staged_version)
        )
        self._update_restart_button.setEnabled(False)
        try:
            if app_dir_writable(self._updater.app_dir):
                subprocess.Popen(
                    ["cmd.exe", "/c", str(script)],
                    close_fds=True,
                    cwd=str(script.parent),
                    creationflags=0x00000008 | 0x00000200,
                )
            else:
                # Installer install (e.g. Program Files): robocopy needs
                # elevation, so relaunch the script via UAC.
                path_text = str(script).replace("'", "''")
                ps_command = f"Start-Process -FilePath '{path_text}' -Verb RunAs"
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", ps_command],
                    close_fds=True,
                    creationflags=0x00000008 | 0x00000200,
                )
                self._update_status_label.setText(
                    self._tr("updates.elevated", version=self._staged_version)
                )
        except OSError as exc:
            self._update_status_label.setText(self._tr("updates.failed", error=str(exc)))
            self._update_restart_button.setEnabled(True)
            return
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.quit()

    def resume_pending_update(self) -> None:
        """Offer an already-staged update after a restart (no re-download).

        Called at startup, before any network access. A fully staged tree
        (apply script + verified manifest) from a previous session is
        surfaced as "ready to install" — previously the app silently
        forgot about it and the user had to download the same delta all
        over again.
        """
        if not self._updater.enabled:
            return
        version = self._updater.pending_staged_update()
        if version is None:
            return
        self._staged_version = version
        self._update_check_button.setEnabled(False)
        self._update_restart_button.setEnabled(True)
        self._update_restart_button.setVisible(True)
        if "apply FAILED" in read_apply_log_tail(lines=50):
            self._update_status_label.setText(self._tr("updates.last_failed", version=version))
        else:
            self._update_status_label.setText(self._tr("updates.resume_ready", version=version))

    def apply_check_result(self, result: CheckResult) -> None:
        """Reflect a startup check performed by the app shell."""
        if result.available and result.plan is not None:
            self._update_status_label.setText(
                self._tr("updates.available", version=result.plan.new_version)
            )
        elif result.error:
            self._update_status_label.setText(self._tr("updates.failed", error=result.error))

    # -- wiring --------------------------------------------------------------
    def _on_theme_activated(self, index: int) -> None:
        value = self._theme_combo.itemData(index)
        if isinstance(value, str):
            self._theme_manager.set_theme(value)

    def _on_language_activated(self, index: int) -> None:
        value = self._language_combo.itemData(index)
        if isinstance(value, str):
            self._translator.set_language(value)

    # -- connection card: persistent connect (Phase 6) ----------------------------
    def _on_auto_connect_toggled(self, checked: bool) -> None:
        self._account_settings.auto_connect = checked

    def _on_connect_clicked(self) -> None:
        tr = self._translator.translate
        if self._shared_gateway is None:
            self._result_label.setText(tr("settings.account.test_failed", detail="gateway offline"))
            return
        if self._connect_worker is not None:  # already busy
            return
        if self._shared_gateway.state.value == "connected":
            self._start_connect_worker(mode="disconnect")
            return
        login = self._persist_account()
        server = self._account_settings.server
        if login <= 0 or not server:
            self._result_label.setText(tr("connect.missing_credentials"))
            return
        password = self._password_edit.text()
        if not password:
            try:
                password = self._credential_store.get_password(login) or ""
            except CredentialStoreError:
                password = ""
        if not password:
            self._result_label.setText(tr("connect.no_password"))
            return
        request = ConnectRequest(
            login=login,
            password=password,
            server=server,
            terminal_path=self._account_settings.terminal_path,
        )
        self._start_connect_worker(mode="connect", request=request)

    def _start_connect_worker(
        self,
        mode: str,
        request: ConnectRequest | None = None,
    ) -> None:
        tr = self._translator.translate
        self._connect_button.setEnabled(False)
        if mode == "connect":
            self._result_label.setText(tr("connect.connecting"))
            self._connect_worker = ConnectWorker(self._shared_gateway, request)
        else:
            self._connect_worker = ConnectWorker(self._shared_gateway, None, mode="disconnect")
        self._connect_worker.result_ready.connect(self._on_connect_result)
        self._connect_worker.start()

    def _on_connect_result(self, ok: bool, detail: str) -> None:
        tr = self._translator.translate
        worker, self._connect_worker = self._connect_worker, None
        if worker is not None:
            worker.wait()
            worker.deleteLater()
        self._connect_button.setEnabled(True)
        if ok:
            self._result_label.setText(tr("connect.done"))
        else:
            self._result_label.setText(tr("connect.wait_failed", detail=detail))
        # the gateway's own state callback refreshes the status bar via the bus

    # -- connection card: one-shot probe (Phase 3) ------------------------------
    def _load_account_into_form(self) -> None:
        account = self._account_settings
        if account.login:
            self._login_edit.setText(str(account.login))
        if account.server:
            self._server_edit.setText(account.server)
        if account.terminal_path:
            self._terminal_edit.setText(account.terminal_path)
        self._update_server_hint()

    def _update_server_hint(self) -> None:
        """Warn live when the server field does not look like an MT5 server.

        A common slip is pasting the password (or a random secret) into the
        Server field; the login then burns a silent timeout with zero feedback.
        The hint is advisory — the user can still connect to odd-but-real names.
        """
        tr = self._translator.translate
        server = self._server_edit.text().strip()
        suspicious = bool(server) and _SERVER_SUSPICIOUS_RE.search(server) is not None
        self._server_hint.setText(tr("settings.account.server.suspicious"))
        self._server_hint.setVisible(suspicious)

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
        if self._storage is not None:
            self._storage.audit.mt5_credentials_changed("saved")

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

    # -- storage & sync card -----------------------------------------------------
    def _build_storage_card(self) -> None:
        tr = self._translator.translate
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._storage_title = QLabel()
        self._storage_title.setObjectName("CardTitle")
        layout.addWidget(self._storage_title)

        # read-only status grid
        self._db_label = QLabel()
        self._db_label.setWordWrap(True)
        self._size_label = QLabel()
        self._size_label.setObjectName("FaintLabel")
        self._schema_label = QLabel()
        self._integrity_label = QLabel()
        self._backups_label = QLabel()
        self._outbox_label = QLabel()
        self._last_sync_label = QLabel()
        self._cloud_label = QLabel()
        for widget in (
            self._db_label,
            self._size_label,
            self._schema_label,
            self._integrity_label,
            self._backups_label,
            self._outbox_label,
            self._last_sync_label,
            self._cloud_label,
        ):
            layout.addWidget(widget)

        # cloud configuration form
        self._cloud_url_label = QLabel()
        self._cloud_url_edit = QLineEdit()
        self._cloud_url_edit.setPlaceholderText(tr("settings.cloud.url.placeholder"))

        self._cloud_key_label = QLabel()
        self._cloud_key_edit = QLineEdit()
        self._cloud_key_edit.setEchoMode(QLineEdit.EchoMode.Password)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addRow(self._cloud_url_label, self._cloud_url_edit)
        form.addRow(self._cloud_key_label, self._cloud_key_edit)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self._cloud_save_button = QPushButton()
        self._cloud_save_button.setObjectName("AccentButton")
        self._cloud_save_button.clicked.connect(self._on_save_cloud)
        self._cloud_test_button = QPushButton()
        self._cloud_test_button.setObjectName("GhostButton")
        self._cloud_test_button.clicked.connect(self._on_test_cloud)
        self._cloud_remove_button = QPushButton()
        self._cloud_remove_button.setObjectName("DangerButton")
        self._cloud_remove_button.clicked.connect(self._on_remove_key)
        buttons.addWidget(self._cloud_save_button)
        buttons.addWidget(self._cloud_test_button)
        buttons.addWidget(self._cloud_remove_button)
        self._cloud_result_label = QLabel()
        self._cloud_result_label.setWordWrap(True)
        buttons.addWidget(self._cloud_result_label, 1)
        layout.addLayout(buttons)

        self._column.addWidget(card)

    def _load_cloud_into_form(self) -> None:
        url = load_cloud_url()
        if url:
            self._cloud_url_edit.setText(url)

    def _refresh_storage_stats(self) -> None:
        """Periodic refresh of the read-only status labels (UI never blocks)."""
        if self._storage is None:
            return
        tr = self._translator.translate
        try:
            stats = self._storage.stats()
            sync = self._storage.sync_status()
        except Exception:  # database closed / shutting down
            return
        size_kb = stats["size_bytes"] / 1024
        wal_kb = stats["wal_size_bytes"] / 1024
        self._db_label.setText(f"{tr('settings.storage.db')}: {stats['path']}")
        self._size_label.setText(
            f"{tr('settings.storage.size')}: {size_kb:.1f} KB (+{wal_kb:.1f} KB WAL)"
        )
        self._schema_label.setText(f"{tr('settings.storage.schema')}: {stats['version']}")
        integrity_key = (
            "settings.storage.integrity_ok"
            if stats["integrity_ok"]
            else "settings.storage.integrity_bad"
        )
        self._integrity_label.setText(f"{tr('settings.storage.integrity')}: {tr(integrity_key)}")
        self._backups_label.setText(f"{tr('settings.storage.backups')}: {stats['backups']}")
        outbox = stats["outbox"]
        pending = outbox.get("pending", 0) + outbox.get("in_flight", 0)
        queue_text = (
            f"{tr('settings.storage.outbox')}: {pending} {tr('settings.storage.pending_suffix')}"
        )
        if outbox.get("dead"):
            queue_text += f" · {outbox['dead']} {tr('settings.storage.dead_suffix')}"
        self._outbox_label.setText(queue_text)
        last = sync.get("last_synced_at") or tr("settings.storage.never")
        self._last_sync_label.setText(f"{tr('settings.storage.last_sync')}: {last}")
        cloud_key = (
            "settings.storage.cloud_on" if sync.get("enabled") else "settings.storage.cloud_off"
        )
        self._cloud_label.setText(f"{tr('settings.storage.cloud')}: {tr(cloud_key)}")

    def _on_save_cloud(self) -> None:
        tr = self._translator.translate
        url = self._cloud_url_edit.text().strip()
        if not url.startswith("https://"):
            self._cloud_result_label.setText(tr("settings.cloud.bad_url"))
            return
        key = self._cloud_key_edit.text()
        if key:
            try:
                self._vault.set(SUPABASE_KEY_ENTRY, key)
            except VaultError as exc:
                self._cloud_result_label.setText(tr("settings.cloud.key_failed", error=str(exc)))
                return
            self._cloud_key_edit.clear()
        save_cloud_url(url)
        if self._storage is not None:
            self._storage.apply_cloud_config()
            self._storage.audit.cloud_config_changed(
                {"url": url, "key": "changed" if key else "kept"}
            )
            self._refresh_storage_stats()
        self._cloud_result_label.setText(tr("settings.cloud.saved"))

    def _on_remove_key(self) -> None:
        tr = self._translator.translate
        try:
            self._vault.delete(SUPABASE_KEY_ENTRY)
        except VaultError as exc:
            self._cloud_result_label.setText(tr("settings.cloud.key_failed", error=str(exc)))
            return
        save_cloud_url("")
        if self._storage is not None:
            self._storage.apply_cloud_config()
            self._storage.audit.cloud_config_changed({"key": "removed"})
            self._refresh_storage_stats()
        self._cloud_result_label.setText(tr("settings.cloud.key_removed"))

    def _on_test_cloud(self) -> None:
        tr = self._translator.translate
        if self._cloud_probe is not None:  # a test is already running
            return
        url = self._cloud_url_edit.text().strip()
        if not url.startswith("https://"):
            self._cloud_result_label.setText(tr("settings.cloud.bad_url"))
            return
        key = self._cloud_key_edit.text()
        if not key:
            try:
                key = self._vault.get(SUPABASE_KEY_ENTRY) or ""
            except VaultError:
                key = ""
        if not key:
            self._cloud_result_label.setText(tr("settings.cloud.no_key"))
            return
        self._cloud_probe = CloudProbe(url, key)
        self._cloud_probe.start()
        self._cloud_test_button.setEnabled(False)
        self._cloud_result_label.setText(tr("settings.cloud.testing"))
        self._poll_cloud_probe()

    def _poll_cloud_probe(self) -> None:
        if self._cloud_probe is None:
            return
        tr = self._translator.translate
        state = self._cloud_probe.poll()
        if not state.finished:
            QTimer.singleShot(200, self._poll_cloud_probe)
            return
        self._cloud_probe = None
        self._cloud_test_button.setEnabled(True)
        key = "settings.cloud.test_ok" if state.ok else "settings.cloud.test_failed"
        self._cloud_result_label.setText(tr(key, detail=state.detail))

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

        self._connection_title.setText(tr("settings.connection"))
        self._login_label.setText(tr("settings.account.login"))
        self._server_label.setText(tr("settings.account.server"))
        self._terminal_label.setText(tr("settings.account.terminal_path"))
        self._password_label.setText(tr("settings.account.password"))
        self._save_password_button.setText(tr("settings.account.save_password"))
        self._test_button.setText(tr("settings.account.test_connection"))
        self._login_edit.setPlaceholderText(tr("settings.account.login.placeholder"))
        self._server_edit.setPlaceholderText(tr("settings.account.server.placeholder"))
        self._update_server_hint()
        self._terminal_edit.setPlaceholderText(tr("settings.account.terminal_path.placeholder"))
        self._auto_connect_check.setText(tr("connect.auto"))
        connected = (
            self._shared_gateway is not None and self._shared_gateway.state.value == "connected"
        )
        self._connect_button.setText(
            tr("connect.disconnect") if connected else tr("connect.connect")
        )

        self._version_label.setText(f"{tr('settings.version')}: {__version__}")
        self._python_label.setText(
            f"{tr('settings.python')}: {platform.python_version()} "
            f"({sys.platform}, {platform.machine()})"
        )

        self._updates_title.setText(tr("updates.title"))
        self._update_version_label.setText(tr("updates.current", version=__version__))
        self._update_check_button.setText(tr("updates.check"))
        self._update_restart_button.setText(tr("updates.restart"))
        self._update_startup_check.setText(tr("updates.startup"))

        if self._storage is not None:
            self._storage_title.setText(tr("settings.storage.title"))
            self._cloud_url_label.setText(tr("settings.cloud.url"))
            self._cloud_key_label.setText(tr("settings.cloud.key"))
            self._cloud_url_edit.setPlaceholderText(tr("settings.cloud.url.placeholder"))
            self._cloud_save_button.setText(tr("settings.cloud.save"))
            self._cloud_test_button.setText(tr("settings.cloud.test"))
            self._cloud_remove_button.setText(tr("settings.cloud.remove_key"))
            self._refresh_storage_stats()
