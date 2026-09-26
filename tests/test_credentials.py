"""CredentialStore tests — keyring contract without touching the OS vault."""

from __future__ import annotations

from typing import Any

import pytest

from app.mt5.credentials import CredentialStore, CredentialStoreError


class FakeKeyring:
    """Dict-backed keyring double (mimics the module API we use)."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)


class RaisingKeyring:
    def set_password(self, service: str, username: str, password: str) -> None:
        raise OSError("vault locked")

    def get_password(self, service: str, username: str) -> str | None:
        raise OSError("vault locked")

    def delete_password(self, service: str, username: str) -> None:
        raise OSError("vault locked")


class NotFoundKeyring(FakeKeyring):
    class NotFoundError(Exception):
        pass

    def delete_password(self, service: str, username: str) -> None:
        raise NotFoundKeyring.NotFoundError("entry not found")


@pytest.fixture
def keyring_module() -> Any:
    return FakeKeyring()


@pytest.fixture
def store(keyring_module: Any) -> CredentialStore:
    return CredentialStore(keyring_module)


class TestRoundtrip:
    def test_set_and_get(self, store: CredentialStore, keyring_module: Any) -> None:
        store.set_password(12345678, "hunter2")
        assert store.get_password(12345678) == "hunter2"
        assert keyring_module.store[("MT5TradingWorkstation", "12345678")] == "hunter2"

    def test_has_password(self, store: CredentialStore) -> None:
        assert store.has_password(12345678) is False
        store.set_password(12345678, "hunter2")
        assert store.has_password(12345678) is True

    def test_delete(self, store: CredentialStore) -> None:
        store.set_password(12345678, "hunter2")
        store.delete_password(12345678)
        assert store.get_password(12345678) is None

    def test_delete_missing_is_idempotent(self, store: CredentialStore) -> None:
        store.delete_password(999)  # no error

    def test_str_login_accepted(self, store: CredentialStore) -> None:
        store.set_password("12345678", "hunter2")
        assert store.get_password("12345678") == "hunter2"


class TestGuards:
    def test_empty_password_refused(self, store: CredentialStore) -> None:
        with pytest.raises(CredentialStoreError, match="empty"):
            store.set_password(12345678, "")

    def test_backend_failure_becomes_friendly(self) -> None:
        store = CredentialStore(RaisingKeyring())
        with pytest.raises(CredentialStoreError, match=r"vault locked|OS vault"):
            store.set_password(1, "x")
        with pytest.raises(CredentialStoreError):
            store.get_password(1)
        assert store.has_password(1) is False

    def test_fail_backend_detected(self) -> None:
        class FailModule:
            class _Fail:
                __module__ = "keyring.fail.Keyring"

            @staticmethod
            def get_keyring() -> Any:
                return FailModule._Fail()

        store = CredentialStore(FailModule())
        with pytest.raises(CredentialStoreError, match="No OS credential vault"):
            store.get_password(1)

    def test_notfound_delete_treated_as_deleted(self) -> None:
        store = CredentialStore(NotFoundKeyring())
        store.delete_password(123)  # no error
