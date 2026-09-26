"""Regression tests: Restart & install button and staged-update resume.

The v0.6.2/v0.6.3 in-app updater downloaded updates but never installed
them, for two UI-side reasons (the batch-script root cause lives in
``app.updater.service`` and is covered by ``test_updater.py``):

- the settings page *silently returned* when the apply script was
  missing — the user clicked the button and nothing happened, with no
  message anywhere;
- a staged update was forgotten after an app restart, so the user had
  to download the very same delta all over again.

These tests pin: visible feedback for every failure mode, the exact
spawn command (``cmd.exe /c <script>``, never a bare .bat argv), the
elevation fallback when the install dir is not writable, and the
startup resume of a pending staged update.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.core.event_bus import EventBus
from app.core.settings import Mt5AccountSettings
from app.mt5.credentials import CredentialStore
from app.ui.pages.settings import SettingsPage
from tests.fakes.fake_mt5 import FakeKeyring, FakeMetaTrader5, FakeSymbolConfig


class _StubUpdater:
    """Scriptable UpdateService stand-in — no app dir probing, no network."""

    def __init__(
        self,
        app_dir: Path,
        pending: str | None = None,
        enabled: bool = True,
    ) -> None:
        self._app_dir = app_dir
        self._pending = pending
        self.enabled = enabled

    @property
    def app_dir(self) -> Path:
        return self._app_dir

    def pending_staged_update(self) -> str | None:
        return self._pending

    def apply_script_path(self, version: str) -> Path:
        return self._app_dir.parent / f"apply_update_{version}.bat"


def _make_page(
    translator: Any,
    theme_manager: Any,
    account: Mt5AccountSettings,
    qtbot: Any,
    updater: _StubUpdater,
) -> SettingsPage:
    factory: Callable[[], FakeMetaTrader5] = lambda: FakeMetaTrader5(  # noqa: E731
        symbols=[FakeSymbolConfig("EURUSD.m")]
    )
    page = SettingsPage(
        translator,
        theme_manager,
        None,
        account_settings=account,
        credential_store=CredentialStore(FakeKeyring()),
        mt5_factory=factory,
        bus=EventBus(),
        shared_gateway=None,
        updater=updater,  # type: ignore[arg-type]
    )
    qtbot.addWidget(page)
    return page


@pytest.fixture
def account(settings_path: str) -> Mt5AccountSettings:
    return Mt5AccountSettings.load(settings_path)


class TestResumePendingUpdate:
    """A staged update must be offered again after a restart."""

    def test_pending_staged_update_surfaces_restart_button(
        self, translator, theme_manager, account, qtbot, tmp_path
    ) -> None:
        page = _make_page(
            translator, theme_manager, account, qtbot, _StubUpdater(tmp_path, "0.6.3")
        )
        page.resume_pending_update()
        assert page._staged_version == "0.6.3"
        assert page._update_restart_button.isVisibleTo(page)
        assert page._update_restart_button.isEnabled()
        assert not page._update_check_button.isEnabled()
        assert "0.6.3" in page._update_status_label.text()

    def test_no_pending_update_keeps_button_hidden(
        self, translator, theme_manager, account, qtbot, tmp_path
    ) -> None:
        page = _make_page(translator, theme_manager, account, qtbot, _StubUpdater(tmp_path, None))
        page.resume_pending_update()
        assert page._staged_version is None
        assert not page._update_restart_button.isVisibleTo(page)
        assert page._update_check_button.isEnabled()

    def test_disabled_updater_is_a_noop(
        self, translator, theme_manager, account, qtbot, tmp_path
    ) -> None:
        page = _make_page(
            translator,
            theme_manager,
            account,
            qtbot,
            _StubUpdater(tmp_path, "0.6.3", enabled=False),
        )
        page.resume_pending_update()
        assert page._staged_version is None
        assert not page._update_restart_button.isVisibleTo(page)


class TestRestartAndInstall:
    """Every failure mode must be visible; success spawns cmd.exe /c."""

    def test_missing_script_shows_message_not_silence(
        self,
        translator,
        theme_manager,
        account,
        qtbot,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def explode(*args: Any, **kwargs: Any) -> None:  # pragma: no cover
            raise AssertionError("Popen must not be called when the script is missing")

        monkeypatch.setattr(subprocess, "Popen", explode)
        monkeypatch.setattr("app.ui.pages.settings.os.name", "nt")
        page = _make_page(
            translator, theme_manager, account, qtbot, _StubUpdater(tmp_path, "0.6.3")
        )
        page._staged_version = "0.6.3"
        page._on_restart_and_install()
        assert page._update_status_label.text() == translator.translate("updates.staged_missing")
        assert page._staged_version is None
        assert page._update_check_button.isEnabled()

    def test_spawns_cmd_explicitly_and_sets_restarting(
        self,
        translator,
        theme_manager,
        account,
        qtbot,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = _make_page(
            translator, theme_manager, account, qtbot, _StubUpdater(tmp_path, "0.6.3")
        )
        script = page._updater.apply_script_path("0.6.3")
        script.write_text("@echo off\r\n", encoding="ascii")

        spawned: list[list[str]] = []

        def fake_popen(argv: list[str], **kwargs: Any) -> None:
            spawned.append(argv)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        # keep the writability probe fast and local (tmp_path is writable)
        monkeypatch.setattr("app.ui.pages.settings.os.name", "nt")
        page._staged_version = "0.6.3"
        page._on_restart_and_install()
        assert len(spawned) == 1
        assert spawned[0][:2] == ["cmd.exe", "/c"]
        assert spawned[0][2] == str(script)
        assert "0.6.3" in page._update_status_label.text()

    def test_unwritable_app_dir_uses_elevation(
        self,
        translator,
        theme_manager,
        account,
        qtbot,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = _make_page(
            translator, theme_manager, account, qtbot, _StubUpdater(tmp_path, "0.6.3")
        )
        script = page._updater.apply_script_path("0.6.3")
        script.write_text("@echo off\r\n", encoding="ascii")

        spawned: list[list[str]] = []

        def fake_popen(argv: list[str], **kwargs: Any) -> None:
            spawned.append(argv)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr("app.ui.pages.settings.os.name", "nt")
        monkeypatch.setattr("app.ui.pages.settings.app_dir_writable", lambda _dir: False)
        page._staged_version = "0.6.3"
        page._on_restart_and_install()
        assert len(spawned) == 1
        assert spawned[0][0] == "powershell"
        assert "-Verb RunAs" in spawned[0][-1]
        assert str(script) in spawned[0][-1]

    def test_non_windows_shows_guidance(
        self,
        translator,
        theme_manager,
        account,
        qtbot,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def explode(*args: Any, **kwargs: Any) -> None:  # pragma: no cover
            raise AssertionError("Popen must not run off Windows")

        monkeypatch.setattr(subprocess, "Popen", explode)
        # force the off-Windows branch regardless of the host platform
        monkeypatch.setattr("app.ui.pages.settings.os.name", "posix")
        page = _make_page(
            translator, theme_manager, account, qtbot, _StubUpdater(tmp_path, "0.6.3")
        )
        page._staged_version = "0.6.3"
        # os.name is "posix" on CI — the guidance branch fires naturally
        page._on_restart_and_install()
        assert page._update_status_label.text() == translator.translate(
            "updates.restart_windows_only"
        )
