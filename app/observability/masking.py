"""Secret masking (SPEC C-security, E3): redaction filter + tests.

Secrets must never reach logs, exports, crash reports or debug bundles.
This module provides:

- :func:`mask_text` — redacts secret *values* from free-form strings by
  recognising ``key=value``, ``key: value`` and JSON ``"key": "value"``
  shapes for well-known secret keys, plus ``Bearer`` tokens.
- :func:`mask_dict` — recursively redacts values of secret-keyed fields in
  structured payloads (request/response logging in later phases).
- :func:`mask_extras` — masks string values of a loguru ``extra`` mapping.

The masking is intentionally conservative: any key that *looks* like a
credential is redacted even when the value is harmless. A false positive
costs one log line; a false negative leaks a password.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

#: Keys whose values must never be logged (case-insensitive).
SECRET_KEYS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "passphrase",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "auth",
    "bearer",
    "credential",
    "credentials",
    "login_password",
    "supabase_key",
    "anon_key",
    "service_role",
    "private_key",
    "access_key",
    "refresh_key",
    "session_key",
)

_KEY_ALTERNATION = "|".join(re.escape(k) for k in SECRET_KEYS)

# password=hunter2 | password: hunter2 | password=hunter2,more
_KV_PATTERN = re.compile(
    rf"(?i)\b({_KEY_ALTERNATION})\b(\s*[=:]\s*)(?!\s)('[^']*'|\"[^\"]*\"|[^\s,;&)\]}}]+)"
)
# JSON style: "password": "hunter2" — (?:...) keeps the alternation inside
# the quotes so the colon part applies to every key, not just the last one.
_JSON_PATTERN = re.compile(rf'(?i)("(?:{_KEY_ALTERNATION})"\s*:\s*)"[^"]*"')
# Authorization: Bearer eyJ... (header style, any capitalisation)
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer)(\s+)([A-Za-z0-9._~+/=-]{8,})")
# URL query: ?token=...&x  (covered by _KV_PATTERN for plain keys)
_URL_SECRET_PATTERN = re.compile(rf"(?i)\b({_KEY_ALTERNATION})=([A-Za-z0-9._~+/=-]+)")


def mask_text(text: str) -> str:
    """Redact secret values inside a free-form string."""
    if not text:
        return text

    # Bearer first, so the KV pass cannot consume the token as a plain value.
    text = _BEARER_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _JSON_PATTERN.sub(lambda m: f'{m.group(1)}"{REDACTED}"', text)
    text = _KV_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _URL_SECRET_PATTERN.sub(r"\1=" + REDACTED, text)
    return text


def mask_dict(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``payload`` with secret-keyed values redacted."""
    if payload is None:
        return {}
    masked_payload: dict[str, Any] = _mask_value(payload)
    return masked_payload


def _mask_value(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            if _is_secret_key(str(key)):
                masked[key] = REDACTED
            else:
                masked[key] = _mask_value(item)
        return masked
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_mask_value(item) for item in value)
    if isinstance(value, str):
        return mask_text(value)
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in SECRET_KEYS


def mask_extras(extras: dict[str, Any]) -> dict[str, Any]:
    """Mask string-valued loguru extras in place (returns the same mapping)."""
    for key, value in extras.items():
        if isinstance(value, str):
            extras[key] = mask_text(value)
    return extras
