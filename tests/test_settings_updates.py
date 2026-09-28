"""Regression tests: Restart & install button and staged-update resume.

The v0.6.2-v0.6.4 in-app updater downloaded updates but repeatedly
failed to install them. The installer is now the app's own executable
in the ``--apply-update`` helper mode (no more cmd.exe / batch files);
the UI must spawn it directly, confirm it is actually running before
quitting, use the UAC worker for unwritable install dirs, and surface
every failure mode as a visible message.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.core.event_bus import EventBus
from app.core.settings import Mt5AccountSettings
from app.mt5.credentials import CredentialStore
from app.ui.pages.settings import SettingsPage
from app.updater.manifest import build_manifest
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

    def apply_update_command(self, version: str) -> list[str]:
        staging = self._app_dir.parent / f"{self._app_dir.name}.update-{version}"
        return [
            sys.executable,
            "--apply-update",
            "--update-staging",
            str(staging),
            "--update-pid",
            "12345",
        ]


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


def _stage_tree(updater: _StubUpdater, version: str) -> Path:
    """Create a complete staging tree the installer would accept."""
    staging = updater.app_dir.parent / f"{updater.app_dir.name}.update-{version}"
    staging.mkdir(parents=True)
    (staging / "app.exe").write_bytes(b"new")
    (staging / "manifest.json").write_text(
        json.dumps(build_manifest(staging, version)), encoding="utf-8"
    )
    return staging


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
    """Every failure mode must be visible; success spawns the helper exe."""

    def test_missing_staging_shows_message_not_silence(
        self,
        translator,
        theme_manager,
        account,
        qtbot,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def explode(*args: Any, **kwargs: Any) -> None:  # pragma: no cover
            raise AssertionError("Popen must not be called when staging is missing")

        monkeypatch.setattr(subprocess, "Popen", explode)
        monkeypatch.setattr("app.ui.pages.settings.os", types.SimpleNamespace(name="nt"))
        page = _make_page(
            translator, theme_manager, account, qtbot, _StubUpdater(tmp_path, "0.6.3")
        )
        page._staged_version = "0.6.3"
        page._on_restart_and_install()
        assert page._update_status_label.text() == translator.translate("updates.staged_missing")
        assert page._staged_version is None
        assert page._update_check_button.isEnabled()

    def test_spawns_helper_and_waits_before_quitting(
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
        _stage_tree(page._updater, "0.6.3")

        spawned: list[list[str]] = []
        scheduled: list[tuple[int, Callable[[], None]]] = []

        class _FakeProcess:
            def poll(self) -> int | None:
                return None  # still running

        def fake_popen(argv: list[str], **kwargs: Any) -> _FakeProcess:
            spawned.append(argv)
            return _FakeProcess()

        def fake_single_shot(ms: int, callback: Callable[[], None]) -> None:
            scheduled.append((ms, callback))

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr("app.ui.pages.settings.QTimer.singleShot", fake_single_shot)
        monkeypatch.setattr("app.ui.pages.settings.os", types.SimpleNamespace(name="nt"))
        page._staged_version = "0.6.3"
        page._on_restart_and_install()

        assert len(spawned) == 1
        command = spawned[0]
        assert command[1] == "--apply-update"
        assert command[command.index("--update-pid") + 1] == "12345"
        assert "0.6.3" in page._update_status_label.text()
        # the app quits only after the helper is confirmed alive
        assert len(scheduled) == 1
        # a live helper process → quit
        quit_calls: list[int] = []

        class _App:
            @staticmethod
            def quit() -> None:
                quit_calls.append(1)

            @staticmethod
            def processEvents() -> None:  # pytest-qt teardown calls this
                pass

        monkeypatch.setattr("PySide6.QtWidgets.QApplication.instance", lambda: _App())
        scheduled[0][1]()

        assert quit_calls == [1]

    def test_helper_dying_instantly_is_reported(
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
        _stage_tree(page._updater, "0.6.3")

        class _DeadProcess:
            returncode = 3

            def poll(self) -> int:
                return self.returncode

        monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: _DeadProcess())
        scheduled: list[Callable[[], None]] = []
        monkeypatch.setattr(
            "app.ui.pages.settings.QTimer.singleShot",
            lambda _ms, cb: scheduled.append(cb),
        )
        monkeypatch.setattr("app.ui.pages.settings.os", types.SimpleNamespace(name="nt"))
        page._staged_version = "0.6.3"
        page._on_restart_and_install()
        scheduled[0]()
        assert translator.translate("updates.install_start_failed").split("{")[0] in (
            page._update_status_label.text()
        )
        assert page._update_restart_button.isEnabled()

    def test_unwritable_app_dir_uses_elevation_worker(
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
        _stage_tree(page._updater, "0.6.3")
        received: list[list[str]] = []

        class _FakeSignal:
            def connect(self, _slot: Any) -> None:
                pass

        class _FakeElevateWorker:
            def __init__(self, command: list[str], parent: object = None) -> None:
                received.append(command)
                self.start_result = _FakeSignal()

            def start(self) -> None:
                pass

        monkeypatch.setattr("app.ui.pages.settings.ElevateWorker", _FakeElevateWorker)
        monkeypatch.setattr("app.ui.pages.settings.os", types.SimpleNamespace(name="nt"))
        monkeypatch.setattr("app.ui.pages.settings.app_dir_writable", lambda _dir: False)
        page._staged_version = "0.6.3"
        page._on_restart_and_install()
        assert len(received) == 1
        assert received[0][1] == "--apply-update"
        assert (
            str(translator.translate("updates.elevated").split("{")[0])
            in (page._update_status_label.text().split("v")[0])
            or "0.6.3" in page._update_status_label.text()
        )

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
        monkeypatch.setattr("app.ui.pages.settings.os", types.SimpleNamespace(name="posix"))
        page = _make_page(
            translator, theme_manager, account, qtbot, _StubUpdater(tmp_path, "0.6.3")
        )
        page._staged_version = "0.6.3"
        # os.name is "posix" on CI — the guidance branch fires naturally
        page._on_restart_and_install()
        assert page._update_status_label.text() == translator.translate(
            "updates.restart_windows_only"
        )


class TestElevateWorkerScript:
    """The PowerShell one-liner must report a declined UAC prompt."""

    def test_build_script_quotes_and_checks(self) -> None:
        from app.updater.worker import ElevateWorker

        worker = ElevateWorker(
            [r"C:\Apps\MT5TradingWorkstation.exe", "--apply-update", "--update-pid", "7"]
        )
        script = worker._build_script()
        assert "Start-Process" in script
        assert "-Verb RunAs" in script
        assert "-PassThru" in script
        assert "'--apply-update'" in script
        assert "exit 0" in script and "exit 1" in script

    def test_run_reports_failure_without_powershell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.updater.worker import ElevateWorker

        def boom(*_a: Any, **_k: Any) -> None:
            raise FileNotFoundError("powershell missing on this platform")

        monkeypatch.setattr("app.updater.worker.subprocess.run", boom)
        worker = ElevateWorker(["exe", "--apply-update"])
        results: list[tuple[bool, str]] = []
        worker.start_result.connect(lambda ok, err: results.append((ok, err)))
        worker.run()
        assert results == [(False, "powershell missing on this platform")]
