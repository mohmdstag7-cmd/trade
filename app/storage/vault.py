"""Tiny keyring vault for non-MT5 secrets (SPEC C11, E1).

The Supabase service key lives ONLY in the OS credential vault (Windows
Credential Manager on the user's PC) — never in QSettings, config files,
logs or exports. This mirrors :mod:`app.mt5.credentials` but for generic
named entries, so each integration stores its secret under one service.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.observability.logger import get_logger

log = get_logger("sync")

#: Service name under which entries appear in Windows Credential Manager.
SERVICE_NAME = "MT5TradingWorkstation"


class VaultError(Exception):
    """The OS credential vault could not be used."""


class KeyringLike(Protocol):
    """Minimal structural type of the ``keyring`` module we rely on."""

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def get_password(self, service: str, username: str) -> str | None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class KeyringVault:
    """Store/Load/Delete named secrets in the OS vault. Never logs values."""

    def __init__(self, keyring_module: Any | None = None) -> None:
        self._keyring_module: Any = keyring_module

    def _backend(self) -> KeyringLike:
        """Resolve the keyring backend lazily (import on first use)."""
        if self._keyring_module is None:
            import keyring

            self._keyring_module = keyring
        backend = getattr(self._keyring_module, "get_keyring", None)
        if callable(backend) and "fail" in type(backend()).__module__.lower():
            raise VaultError(
                "No OS credential vault is available. On Windows secrets are "
                "stored in Credential Manager; there it always works."
            )
        return self._keyring_module  # type: ignore[no-any-return]

    # -- API ---------------------------------------------------------------
    def set(self, name: str, secret: str) -> None:
        """Persist ``secret`` under ``name``. Empty secrets are rejected."""
        if not secret:
            msg = "refusing to store an empty secret"
            raise VaultError(msg)
        try:
            self._backend().set_password(SERVICE_NAME, name, secret)
        except VaultError:
            raise
        except Exception as exc:
            msg = f"could not save '{name}' to the OS vault: {exc}"
            raise VaultError(msg) from exc
        log.debug("vault: stored secret '{}'", name)

    def get(self, name: str) -> str | None:
        """Return the secret or ``None`` when absent."""
        try:
            return self._backend().get_password(SERVICE_NAME, name)
        except VaultError:
            raise
        except Exception as exc:
            msg = f"could not read '{name}' from the OS vault: {exc}"
            raise VaultError(msg) from exc

    def delete(self, name: str) -> None:
        """Remove the entry if present (missing entries are fine)."""
        try:
            self._backend().delete_password(SERVICE_NAME, name)
        except VaultError:
            raise
        except Exception as exc:
            msg = f"could not delete '{name}' from the OS vault: {exc}"
            raise VaultError(msg) from exc
        log.debug("vault: deleted secret '{}'", name)

    def has(self, name: str) -> bool:
        """True when a non-empty secret exists (without exposing it)."""
        return bool(self.get(name))
