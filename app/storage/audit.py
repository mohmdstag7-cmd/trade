"""Audit service — every user/system action with before → after (SPEC E3.13).

Actions land in ``audit_log`` (and are mirrored to Supabase by the outbox).
Values pass through the observability masker first, so a password typed on
the Settings page can never reach the audit trail, logs or the cloud
(SPEC I-6).
"""

from __future__ import annotations

from typing import Any

from app.observability.logger import get_logger
from app.observability.masking import mask_dict
from app.storage.db import Database
from app.storage.repositories import AuditLogRepository

log = get_logger("audit")


class AuditService:
    """Thin, typed façade over :class:`AuditLogRepository`."""

    def __init__(self, db: Database) -> None:
        self._repo = AuditLogRepository(db)

    # -- generic ---------------------------------------------------------------
    def record(
        self,
        action: str,
        *,
        source: str = "user",
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> str:
        """Record one action with masked before/after payloads."""
        return self._repo.record(
            action,
            source=source,
            before=mask_dict(before) if before else None,
            after=mask_dict(after) if after else None,
        )

    def system(self, action: str, details: dict[str, Any] | None = None) -> str:
        return self.record(action, source="system", after=details)

    # -- typed actions (grep-friendly) --------------------------------------------
    def app_started(self, version: str) -> str:
        return self.system("app.started", {"version": version})

    def app_stopped(self, version: str) -> str:
        return self.system("app.stopped", {"version": version})

    def setting_changed(self, key: str, before: Any | None = None, after: Any | None = None) -> str:
        return self.record(
            "settings.changed",
            before={"key": key, "value": before},
            after={"key": key, "value": after},
        )

    def mt5_credentials_changed(self, action: str) -> str:
        """``saved`` / ``deleted`` — never the secret itself."""
        return self.system(f"settings.mt5.password.{action}")

    def cloud_config_changed(self, changes: dict[str, Any]) -> str:
        return self.record("settings.cloud.changed", after=changes)

    def kill_switch(self, details: dict[str, Any] | None = None) -> str:
        return self.system("risk.kill_switch", details)
