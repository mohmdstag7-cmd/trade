"""UI tests for the Settings page Storage & Sync card (Phase 4)."""

from __future__ import annotations

import pathlib
import uuid
from typing import Any

import pytest

from app.core.settings import Mt5AccountSettings, UiSettings, save_cloud_url
from app.mt5.credentials import CredentialStore
from app.storage.cloud_probe import CloudProbe
from app.storage.mirror import MirrorError
from app.storage.service import StorageService
from app.ui.i18n.translator import Translator
from app.ui.pages.settings import SettingsPage
from app.ui.theme.manager import ThemeManager


class FakeVault:
    """Dict-backed stand-in for KeyringVault (same tiny API)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, name: str, secret: str) -> None:
        if not secret:
            raise ValueError("empty")
        self.store[name] = secret

    def get(self, name: str) -> str | None:
        return self.store.get(name)

    def delete(self, name: str) -> None:
        self.store.pop(name, None)

    def has(self, name: str) -> bool:
        return name in self.store


class FakeKeyringModule:
    def set_password(self, service: str, username: str, password: str) -> None:
        self.store = getattr(self, "store", {})
        self.store[f"{service}/{username}"] = password

    def get_password(self, service: str, username: str) -> str | None:
        return getattr(self, "store", {}).get(f"{service}/{username}")

    def delete_password(self, service: str, username: str) -> None:
        getattr(self, "store", {}).pop(f"{service}/{username}", None)

    def get_keyring(self) -> object:
        return self


@pytest.fixture
def storage(tmp_path: pathlib.Path) -> StorageService:
    service = StorageService(tmp_path / f"appdata-{uuid.uuid4().hex[:8]}")
    service.open()
    yield service
    service.close()


@pytest.fixture
def fake_vault(monkeypatch: pytest.MonkeyPatch) -> FakeVault:
    """Fake vault AND hermetic cloud-url store (never touches real QSettings)."""
    vault = FakeVault()
    cloud_store: dict[str, str] = {}
    from app.ui.pages import settings as settings_mod

    monkeypatch.setattr(settings_mod, "KeyringVault", lambda: vault)
    monkeypatch.setattr("app.storage.service.KeyringVault", lambda: vault)
    monkeypatch.setattr(settings_mod, "load_cloud_url", lambda: cloud_store.get("url", ""))
    monkeypatch.setattr(settings_mod, "save_cloud_url", lambda u: cloud_store.__setitem__("url", u))
    import app.core.settings as core_settings

    monkeypatch.setattr(core_settings, "load_cloud_url", lambda: cloud_store.get("url", ""))
    monkeypatch.setattr(
        core_settings, "save_cloud_url", lambda u: cloud_store.__setitem__("url", u)
    )
    return vault


@pytest.fixture
def page_factory(qtbot: Any, tmp_path: pathlib.Path):
    counter = {"n": 0}

    def make(storage: StorageService | None) -> SettingsPage:
        counter["n"] += 1
        ini = str(tmp_path / f"settings-{counter['n']}.ini")
        ui = UiSettings.load(ini)
        translator = Translator(ui)
        theme = ThemeManager(ui)
        page = SettingsPage(
            translator,
            theme,
            None,
            account_settings=Mt5AccountSettings.load(ini),
            credential_store=CredentialStore(FakeKeyringModule()),
            storage=storage,
        )
        qtbot.addWidget(page)
        return page

    return make


class TestStorageCard:
    def test_card_present_with_storage(
        self, qtbot: Any, page_factory: Any, storage: StorageService
    ) -> None:
        page = page_factory(storage)
        assert page._storage_title.text() == "Storage & Sync"
        assert "Schema version: 2" in page._schema_label.text()

    def test_card_absent_without_storage(self, qtbot: Any, page_factory: Any) -> None:
        page = page_factory(None)
        assert not hasattr(page, "_storage_title")

    def test_stats_labels_update(
        self, qtbot: Any, page_factory: Any, storage: StorageService
    ) -> None:
        page = page_factory(storage)
        storage.trades.insert({"symbol": "EURUSD"})  # queues an outbox row
        page._refresh_storage_stats()
        assert "1 pending" in page._outbox_label.text()
        assert "local-only" in page._cloud_label.text()

    def test_save_cloud_requires_https(
        self, qtbot: Any, page_factory: Any, storage: StorageService, fake_vault: FakeVault
    ) -> None:
        page = page_factory(storage)
        page._cloud_url_edit.setText("http://insecure.example")
        page._on_save_cloud()
        assert "https://" in page._cloud_result_label.text()

    def test_save_cloud_stores_url_and_key(
        self, qtbot: Any, page_factory: Any, storage: StorageService, fake_vault: FakeVault
    ) -> None:
        from app.core.settings import load_cloud_url as _load  # patched by the fixture

        page = page_factory(storage)
        page._cloud_url_edit.setText("https://myproject.supabase.co")
        page._cloud_key_edit.setText("super-secret-key")
        page._on_save_cloud()
        assert fake_vault.store["supabase.service_key"] == "super-secret-key"
        assert _load() == "https://myproject.supabase.co"
        assert storage.cloud_enabled() is True
        assert page._cloud_key_edit.text() == ""  # never echoed back
        assert "super-secret-key" not in page._cloud_result_label.text()

    def test_remove_key_clears_cloud(
        self, qtbot: Any, page_factory: Any, storage: StorageService, fake_vault: FakeVault
    ) -> None:
        page = page_factory(storage)
        fake_vault.store["supabase.service_key"] = "k"
        page._on_remove_key()
        assert "supabase.service_key" not in fake_vault.store
        assert storage.cloud_enabled() is False

    def test_audit_records_cloud_change(
        self, qtbot: Any, page_factory: Any, storage: StorageService, fake_vault: FakeVault
    ) -> None:
        page = page_factory(storage)
        page._cloud_url_edit.setText("https://myproject.supabase.co")
        page._cloud_key_edit.setText("k2")
        page._on_save_cloud()
        rows = storage.audit_repo.recent(5)
        assert any(str(r["action"]) == "settings.cloud.changed" for r in rows)
        # masked: the raw key must not appear in the audit payload
        payload = " ".join(str(r["after_json"]) for r in rows)
        assert "k2" not in payload

    def test_cloud_test_without_key_shows_hint(
        self, qtbot: Any, page_factory: Any, storage: StorageService, fake_vault: FakeVault
    ) -> None:
        page = page_factory(storage)
        page._cloud_url_edit.setText("https://myproject.supabase.co")
        page._on_test_cloud()
        assert "service key" in page._cloud_result_label.text().lower()


class TestCloudProbe:
    def test_probe_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class GoodMirror:
            def __init__(self, url: str, key: str) -> None:
                pass

            def probe(self) -> float:
                return 42.0

        monkeypatch.setattr("app.storage.cloud_probe.SupabaseMirror", GoodMirror)
        probe = CloudProbe("https://x.supabase.co", "key")
        probe.start()
        state = probe.wait(timeout_s=5)
        assert state.finished and state.ok
        assert "42" in state.detail

    def test_probe_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class BadMirror:
            def __init__(self, url: str, key: str) -> None:
                pass

            def probe(self) -> float:
                raise MirrorError("Could not reach Supabase (network).", "network")

        monkeypatch.setattr("app.storage.cloud_probe.SupabaseMirror", BadMirror)
        probe = CloudProbe("https://x.supabase.co", "key")
        probe.start()
        state = probe.wait(timeout_s=5)
        assert state.finished and not state.ok
        assert "Supabase" in state.detail


def test_save_cloud_url_roundtrip(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod, "_ORG", f"org-{uuid.uuid4().hex[:8]}")
    monkeypatch.setattr(settings_mod, "_APP", f"app-{uuid.uuid4().hex[:8]}")
    save_cloud_url("https://x.supabase.co/")
    assert settings_mod.load_cloud_url() == "https://x.supabase.co"
