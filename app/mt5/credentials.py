"""Account credentials stored in Windows Credential Manager (SPEC C11).

Passwords live ONLY in the OS credential vault (``keyring``); they are never
written to QSettings, files, logs or exports. The store is deliberately tiny:

- :meth:`CredentialStore.set_password` / :meth:`get_password` /
  :meth:`delete_password` / :meth:`has_password`
- one entry per account number under the service name
  ``MT5TradingWorkstation``.

The backing ``keyring`` module is injected (default: the real one) so tests
run headless without touching the OS vault, and a broken vault produces a
friendly :class:`CredentialStoreError` instead of a raw traceback.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.observability.logger import get_logger

log = get_logger("mt5")

#: Service name under which entries appear in Windows Credential Manager.
SERVICE_NAME = "MT5TradingWorkstation"


class CredentialStoreError(Exception):
    """The OS credential vault could not be used."""


class KeyringLike(Protocol):
    """Minimal structural type of the ``keyring`` module we rely on."""

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def get_password(self, service: str, username: str) -> str | None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class CredentialStore:
    """Store/Load/Delete the MT5 account password per login number."""

    def __init__(self, keyring_module: Any | None = None) -> None:
        self._keyring_module: Any = keyring_module

    # -- backend -----------------------------------------------------------
    def _backend(self) -> KeyringLike:
        """Resolve the keyring backend lazily (import on first use)."""
        if self._keyring_module is None:
            import keyring

            self._keyring_module = keyring
        backend = getattr(self._keyring_module, "get_keyring", None)
        if callable(backend) and "fail" in type(backend()).__module__.lower():
            # keyring.fail.Keyring: no OS vault available (headless / unsupported OS)
            raise CredentialStoreError(
                "No OS credential vault is available. On Windows the password is "
                "stored in Credential Manager; there it always works."
            )
        return self._keyring_module  # type: ignore[no-any-return]

    # -- API -----------------------------------------------------------------
    def set_password(self, login: int | str, password: str) -> None:
        """Persist ``password`` for ``login``. Never logs the value."""
        if not password:
            msg = "refusing to store an empty password"
            raise CredentialStoreError(msg)
        try:
            self._backend().set_password(SERVICE_NAME, str(login), password)
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError(
                f"could not save the password to the OS vault: {exc}"
            ) from exc
        log.debug("credentials: stored password for login {}", _mask_login(login))

    def get_password(self, login: int | str) -> str | None:
        """Return the stored password for ``login`` or ``None``."""
        try:
            password = self._backend().get_password(SERVICE_NAME, str(login))
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError(
                f"could not read the password from the OS vault: {exc}"
            ) from exc
        log.debug("credentials: fetched password for login {}", _mask_login(login))
        return password

    def delete_password(self, login: int | str) -> None:
        """Remove the stored password for ``login`` (idempotent)."""
        try:
            self._backend().delete_password(SERVICE_NAME, str(login))
        except Exception as exc:
            # Some backends raise when the entry is missing; treat as deleted.
            if _is_missing_entry_error(exc):
                log.debug("credentials: no stored password for login {}", _mask_login(login))
                return
            raise CredentialStoreError(
                f"could not delete the password from the OS vault: {exc}"
            ) from exc
        log.info("credentials: deleted stored password for login {}", _mask_login(login))

    def has_password(self, login: int | str) -> bool:
        """True when a password exists for ``login``."""
        try:
            return self.get_password(login) is not None
        except CredentialStoreError:
            return False


def _mask_login(login: int | str) -> str:
    """Keep only the last 3 digits of the login in log lines."""
    text = str(login)
    return f"***{text[-3:]}" if len(text) > 3 else "***"


def _is_missing_entry_error(exc: Exception) -> bool:
    """True when the keyring error means 'no such entry'."""
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return "notfound" in name or "no password" in message or "not found" in message


__all__ = [
    "SERVICE_NAME",
    "CredentialStore",
    "CredentialStoreError",
]
