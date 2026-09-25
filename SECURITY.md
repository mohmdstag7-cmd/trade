# Security Policy

## How secrets are handled in this project

This application handles **trading-account credentials** and **cloud service keys**.
The following rules are enforced by the architecture and reviewed in every PR:

1. **Secrets never live in code, config files, logs, exports, or debug bundles.**
   - MT5 login / password / server: stored only in the **Windows Credential Manager**
     (via the `keyring` package), never in plaintext files.
   - Supabase: only the **anon key** is configured in the app (protected by Row Level
     Security). The `service_role` key must never be placed in the app.
   - LLM API keys (optional feature): stored in the OS keyring, never sent anywhere
     except the user-configured OpenAI-compatible endpoint.
2. **Redaction.** A log redaction filter masks known secret fields
   (`password`, `secret`, `token`, `anon_key`, ...) in every log record, export, and
   debug bundle. Tests cover the redaction filter (Phase 2).
3. **Never commit secrets.** `.gitignore` blocks `.env`, key files, databases, and
   logs. CI runs CodeQL scanning weekly.
4. **Debug bundles are safe to share by design** — they contain logs (masked),
   crash reports, versions, and health state, but never credentials.

## Supported versions

Security fixes are applied to the latest release line only.

## Reporting a vulnerability

Please open a **private security advisory** via GitHub ("Security" → "Report a
vulnerability") or contact the repository owner directly. Do not open a public
issue for security problems.
