"""Supabase mirror — the cloud side of the outbox pattern (SPEC E1).

The mirror receives *business data* and *WARNING+ logs* upserted by their
UUID primary keys, which keeps the sync duplicate-free after any number of
offline sessions (SPEC G3-4 acceptance: "offline writes sync later without
duplicates").

Design notes:
- The ``supabase`` package is imported lazily so the app runs fully
  offline/local when no cloud is configured.
- Errors are classified (:class:`MirrorError.kind`) so the outbox worker
  can distinguish a paused free-tier project (global cooldown) from a
  transient network blip (per-row retry).
- The service key never appears in logs or exceptions (SPEC I-6).
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from app.observability.logger import get_logger
from app.storage.vault import KeyringVault

log = get_logger("sync")

#: Vault entry name for the Supabase service key.
SUPABASE_KEY_ENTRY = "supabase.service_key"


class MirrorError(Exception):
    """A mirror operation failed; ``kind`` drives the retry policy."""

    #: auth → bad/missing key or paused project; network → transient;
    #: server → 5xx; client → schema/request problem (retry unlikely).
    def __init__(self, message: str, kind: str = "network") -> None:
        super().__init__(message)
        self.kind = kind


class MirrorSink(Protocol):
    """Anything that can upsert rows into the cloud mirror."""

    def upsert(self, table: str, rows: list[dict[str, Any]]) -> None: ...


class SupabaseMirror:
    """Upsert rows into Supabase via the PostgREST client."""

    def __init__(
        self,
        url: str,
        key: str,
        client_factory: Any | None = None,
    ) -> None:
        self._url = url.strip().rstrip("/")
        self._key = key
        self._client_factory = client_factory or self._default_client_factory
        self._client: Any | None = None

    # -- client -------------------------------------------------------------
    @staticmethod
    def _default_client_factory(url: str, key: str) -> Any:
        """Build a real Supabase client (lazy import keeps offline mode)."""
        try:
            from supabase import create_client
        except Exception as exc:  # pragma: no cover - depends on install
            msg = (
                "The Supabase package is not available. Install the app "
                "requirements or run in local-only mode."
            )
            raise MirrorError(msg, "client") from exc
        try:
            return create_client(url, key)
        except Exception as exc:
            msg = f"could not create the Supabase client for the given URL: {exc}"
            raise MirrorError(msg, "client") from exc

    def _get_client(self) -> Any:
        if self._client is None:
            if not self._url or not self._key:
                msg = "Supabase URL or key is missing"
                raise MirrorError(msg, "auth")
            try:
                self._client = self._client_factory(self._url, self._key)
            except MirrorError:
                raise
            except Exception as exc:
                msg = f"could not create the Supabase client: {exc}"
                raise MirrorError(msg, "client") from exc
        return self._client

    # -- operations --------------------------------------------------------------
    def upsert(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Upsert a batch of rows (idempotent by the ``id`` column).

        SQLite stores JSON-shaped columns (``*_json``) as TEXT; the cloud
        schema declares them ``jsonb``. Sending the raw string would
        double-encode (PostgREST stores the STRING ``"[{...}]"`` instead
        of the array), so string-typed ``*_json`` fields are parsed back
        into real JSON values before transport.
        """
        if not rows:
            return
        rows = [self._decode_json_columns(row) for row in rows]
        try:
            client = self._get_client()
            query = client.table(table).upsert(rows, on_conflict="id")
            query.execute()
        except MirrorError:
            raise
        except Exception as exc:
            raise self._classify(exc) from exc

    _JSON_SUFFIX = "_json"

    @classmethod
    def _decode_json_columns(cls, row: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in row.items():
            if (
                key.endswith(cls._JSON_SUFFIX)
                and isinstance(value, str)
                and value[:1] in ("{", "[")
            ):
                try:
                    out[key] = json.loads(value)
                    continue
                except ValueError:
                    pass  # not valid JSON after all — send as-is
            out[key] = value
        return out

    def probe(self) -> float:
        """Round-trip sanity check. Returns latency in ms or raises."""
        start = time.monotonic()
        try:
            client = self._get_client()
            client.table("health_checks").select("id").limit(1).execute()
        except MirrorError:
            raise
        except Exception as exc:
            raise self._classify(exc) from exc
        return (time.monotonic() - start) * 1000.0

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def _classify(exc: Exception) -> MirrorError:
        """Map client errors onto the retry taxonomy without leaking keys."""
        text = str(exc)
        lowered = text.lower()
        if any(
            t in lowered for t in ("401", "403", "unauthorized", "forbidden", "invalid api key")
        ):
            return MirrorError(
                "Supabase rejected the credentials. Check the URL and the "
                "service key on the Settings page.",
                "auth",
            )
        if any(
            t in lowered for t in ("timeout", "connection", "unreachable", "dns", "getaddrinfo")
        ):
            return MirrorError("Could not reach Supabase (network).", "network")
        if any(t in lowered for t in ("500", "502", "503", "504", "paused", "temporarily")):
            return MirrorError(
                "Supabase is temporarily unavailable (project may be paused).",
                "server",
            )
        if any(
            t in lowered for t in ("400", "422", "schema", "column", "relation", "does not exist")
        ):
            return MirrorError(
                "Supabase rejected the data (schema mismatch?). Run the latest "
                "supabase/schema.sql in your project.",
                "client",
            )
        return MirrorError(f"Supabase mirror failed: {text}", "network")


class NullMirror:
    """A sink that drops everything — used when cloud sync is disabled."""

    def upsert(self, table: str, rows: list[dict[str, Any]]) -> None:
        return None


def load_supabase_config(url: str, *, keyring_module: Any | None = None) -> tuple[str, str] | None:
    """Read (url, key) with the key from the vault; ``None`` when unset.

    ``url`` comes from QSettings (not a secret); the key NEVER does.
    """
    if not url.strip():
        return None
    vault = KeyringVault(keyring_module)
    key = vault.get(SUPABASE_KEY_ENTRY)
    if not key:
        return None
    return url.strip().rstrip("/"), key
