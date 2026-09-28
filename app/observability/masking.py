"""Secret masking (SPEC C-security, E3): redaction filter + tests.

Secrets must never reach logs, exports, crash reports or debug bundles.
This module provides:

- :func:`mask_text` — redacts secret *values* from free-form strings by
  recognising ``key=value``, ``key: value`` and JSON ``"key": "value"``
  shapes for well-known secret keys (including prefixed forms such as
  ``access_token`` / ``mt5_password``), ``Authorization: Bearer`` headers
  and ``scheme://user:password@host`` URL userinfo.
- :func:`mask_dict` — recursively redacts values of secret-keyed fields in
  structured payloads (request/response logging in later phases).
- :func:`mask_extras` — masks string values of a loguru ``extra`` mapping.

The masking is intentionally aggressive: any key that *looks* like a
credential is redacted even when the value is harmless. A false positive
costs one log line; a false negative leaks a password.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

#: Keys whose values must never be logged (case-insensitive). Matched
#: exactly (after normalisation) against structured-payload keys.
SECRET_KEYS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "passphrase",
    "secret",
    "client_secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "login_password",
    "supabase_key",
    "supabase_service_key",
    "service_key",
    "service_role",
    "service_role_key",
    "anon_key",
    "private_key",
    "secret_key",
    "access_key",
    "refresh_key",
    "session_key",
    "id_token",
    "access_token",
    "refresh_token",
    "session_token",
    "auth_token",
    "bearer_token",
)

#: Key suffixes that mark a credential even when prefixed, e.g.
#: ``mt5_password``, ``broker_api_key``, ``x_secret``. The leading
#: underscore keeps innocent words like ``monkey``/``whiskey`` safe.
_SECRET_KEY_SUFFIXES: tuple[str, ...] = (
    "_password",
    "_passwd",
    "_pwd",
    "_passphrase",
    "_secret",
    "_token",
    "_credential",
    "_credentials",
    "_api_key",
    "_apikey",
    "_access_key",
    "_auth",
    "_key",
)

# Core alternation used by the text patterns. ``(?:[\w-]+[_-])?`` lets the
# pattern match prefixed forms (access_token, mt5_password, x-api-key)
# while still requiring the *core* word to end the identifier, so words
# like ``tokenize`` or ``monkey`` never match.
_CORE_WORDS = (
    "access_token",
    "refresh_token",
    "session_token",
    "id_token",
    "auth_token",
    "bearer_token",
    "login_password",
    "supabase_service_key",
    "supabase_key",
    "service_role_key",
    "service_key",
    "anon_key",
    "service_role",
    "private_key",
    "secret_key",
    "access_key",
    "refresh_key",
    "session_key",
    "api_key",
    "apikey",
    "api-key",
    "password",
    "passwd",
    "pwd",
    "passphrase",
    "secret",
    "credential",
    "credentials",
    "authorization",
    "token",
)
_CORE_ALTERNATION = "|".join(re.escape(word) for word in _CORE_WORDS)
_SECRET_KEY_PATTERN = rf"(?i)((?:[\w-]+[_-])?(?:{_CORE_ALTERNATION}))"

# password=hunter2 | password: hunter2 | password=hunter2,more — the value
# may also be an already-bearer-masked pair ("Bearer [REDACTED]") so the
# Authorization header collapses into a single redaction, never two.
_KV_PATTERN = re.compile(
    rf"{_SECRET_KEY_PATTERN}(\s*[=:]\s*)(?!\s)"
    rf"(?:[Bb]earer\s+[^\s,;&)\]}}]+|'[^']*'|\"[^\"]*\"|[^\s,;&)\]}}]+)"
)
# JSON style: "password": "hunter2" — (?:...) keeps the alternation inside
# the quotes so the colon part applies to every key, not just the last one.
_JSON_PATTERN = re.compile(rf'(?i)("(?:{_CORE_ALTERNATION})"\s*:\s*)"[^"]*"')
# Authorization: Bearer eyJ... (header style, any capitalisation). Consumes
# the whole header value in one pass so a second KV pass cannot double-
# redact it into "[REDACTED] [REDACTED]".
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer)(\s+)([A-Za-z0-9._~+/=-]{8,})")
# URL query: ?token=...&x
_URL_SECRET_PATTERN = re.compile(rf"{_SECRET_KEY_PATTERN}=([A-Za-z0-9._~+/=-]+)")
# URL userinfo: https://user:pass@host — the password component is redacted.
_URL_USERINFO_PATTERN = re.compile(r"(?i)\b([\w+.-]+)://([^/@\s:]+):([^@\s]+?)@")


def mask_text(text: str) -> str:
    """Redact secret values inside a free-form string."""
    if not text:
        return text

    # Bearer header values first, so no later pass can leak the token.
    text = _BEARER_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    # URL userinfo (postgres://user:password@host).
    text = _URL_USERINFO_PATTERN.sub(r"\1://\2:" + REDACTED + "@", text)
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
    if normalized in SECRET_KEYS:
        return True
    return any(normalized.endswith(suffix) for suffix in _SECRET_KEY_SUFFIXES)


def mask_extras(extras: dict[str, Any]) -> dict[str, Any]:
    """Mask loguru extras in place (returns the same mapping)."""
    for key, value in extras.items():
        if _is_secret_key(str(key)):
            extras[key] = REDACTED
        elif isinstance(value, str):
            extras[key] = mask_text(value)
    return extras
