# Progress — MT5 Trading Workstation

> To continue work in a new chat, say:
> **"Read docs/SPEC.md and docs/PROGRESS.md and continue."**

## Phase status (SPEC G3)

| # | Phase | Status | Branch / PR |
|---|-------|--------|-------------|
| 1 | Foundation | **merged** | PR #1 |
| 2 | Observability | **merged** | PR #9 |
| 3 | MT5 connection (real) | **in review** | `phase/03-mt5-gateway` |
| 4 | Storage | not started | — |
| 5 | Market data & analysis | not started | — |
| 6 | Strategies & signals | not started | — |
| 7 | Risk | not started | — |
| 8 | Execution | not started | — |
| 9 | Backtesting | not started | — |
| 10 | ML | not started | — |
| 11 | Analytics & journal | not started | — |
| 12 | AI loop & Go-Live | not started | — |
| 13 | Reliability | not started | — |
| 14 | Release | not started | — |

## Phase 1 — Foundation (merged)

### Built

- Repository tooling: `pyproject.toml` (pinned deps, ruff/mypy/pytest config),
  `.editorconfig`, `.pre-commit-config.yaml`, `.gitignore`.
- Full specification in `docs/SPEC.md`; `docs/ARCHITECTURE.md`; this file;
  `CHANGELOG.md`; `README.md`; `SECURITY.md`; `LICENSE` (proprietary).
- GitHub files: `ci.yml`, `build.yml`, `release-please.yml`, `release.yml`,
  `codeql.yml`, `dependabot.yml`, PR template, 3 issue templates,
  `copilot-instructions.md`, `AGENTS.md` + `CLAUDE.md`.
- Design system: tokens (dark/light) → generated QSS; `ThemeManager`.
- i18n infrastructure: EN/FA string tables, `Translator`, RTL layout direction.
- UI shell: main window, grouped collapsible sidebar, 14 pages (13 designed
  empty states + working Settings), status bar (connection dot, mode badge,
  clock, toggles, kill-switch slot), Ctrl+K command palette.
- Entry points: `python -m app`, `--version`, `--self-check` (MetaTrader5
  import gate used by the build workflow).
- Tests: tokens/QSS, theme manager, i18n, settings, self-check, sidebar,
  command palette, main window smoke (pytest-qt), version sync guard.

### How to run / verify

```bat
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy app
pytest
python -m app
```

### Acceptance checklist (SPEC G3-1)

- [x] App launches (`python -m app`) — verified by pytest-qt smoke tests.
- [x] Theme switches (status bar button, palette, settings page) — tested.
- [x] Lint/tests pass locally (ruff, mypy, pytest).
- [x] CI green on the PR (lint & test, build self-check, CodeQL).
- [x] Build artifact downloads and runs `--self-check` (verified on the PR).
- [x] Merged into `main` (PR #1).

### Known issues / limitations

- The packaged exe shows a console window alongside the GUI (needed for
  `--self-check` output); a windowed build ships in the Release phase.
- Sidebar buttons are text-only; icons arrive with the Phase 14 polish.
- Persian UI is functional (RTL direction + strings) but full RTL polish is
  Phase 14 per SPEC.
- Single-instance guard and `SetThreadExecutionState` arrive with the engine
  phases (SPEC D3.8) — nothing to protect yet in Phase 1.

### Next steps

1. ~~Merge Phase 1 PR after CI is green~~ — merged (PR #1).
2. ~~Phase 2 — Observability~~ — built, in review (see below).

## Phase 2 — Observability (current)

### Built

- **Structured logging** (`app/observability/logger.py`, SPEC E3.1–E3.4):
  loguru-based, non-blocking (`enqueue=True`). One lazy sink per category
  writing `logs/<category>/<date>.jsonl` (one JSON object per line with UTC
  time, level, category, module, function, line, thread, session id, trace
  id, message and optional signal/trade/ticket/symbol/strategy ids), a
  readable `logs/all.log`, and a colored stderr sink. Size rotation, daily
  files, zip compression, 30-day retention and a total size cap with an
  oldest-first prune (background maintenance thread).
- **16 categories** (app, mt5, market_data, analysis, strategy, ml, risk,
  execution, position, sync, backtest, ui, notify, llm, audit, perf) with
  runtime-changeable levels (`set_category_level`) and a debug window
  (`enable_debug_mode(minutes)`) that auto-reverts by expiry.
- **Correlation context** (`context.py`): per-run session id and trace ids
  on `ContextVar`s; `trace()` context manager; every record is stamped by
  the logging patcher.
- **Secret masking** (`masking.py`): `password/token/api_key/...` values in
  `key=value`, `key: value`, JSON, URL-query and `Bearer` shapes are
  redacted in every message, string extra, dict payload and crash report.
- **Crash handler** (`crash_handler.py`, SPEC E3.8): `sys.excepthook`,
  `threading.excepthook` and the Qt message handler write
  `crash_reports/crash_<UTC>_<kind>.json` (stack, last 200 log lines,
  versions, OS, session/trace, thread) and invoke a friendly dialog.
- **Watchdog** (`watchdog.py`, SPEC E3.9): workers register heartbeats;
  freeze > timeout → CRITICAL log + notify + optional restart callback;
  injectable clock for deterministic tests.
- **Logs page** (`app/ui/pages/logs.py`): live view of recent entries from
  the in-memory ring buffer with level/category/search filters, auto-refresh
  toggle and open-folder action; full EN/FA translations.
- Startup log (app/python/OS/session) per SPEC E3.12 (MT5 fields arrive in
  Phase 3); graceful `shutdown_logging()` on exit.
- Tests: masking (26), context (10), logging (17), crash handler (6),
  watchdog (10), logs page (12) — 118 total, 90% coverage.

### How to run / verify

```bat
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy app
pytest
python -m app        # Logs page now shows live entries; %LOCALAPPDATA%\MT5TradingWorkstation\logs
```

### Acceptance checklist (SPEC G3-2)

- [x] A forced exception produces a crash report — tested
      (`test_crash_handler.py`); secrets inside the report are masked.
- [x] Secrets are masked in logs — tested (`test_masking.py`,
      `test_logging.py`, end-to-end smoke run).
- [x] Lint/tests pass locally (ruff, mypy, pytest 118/118).
- [ ] CI green on the PR (checked when the PR runs).
- [ ] Build artifact runs `--self-check` (build workflow).

### Known issues / limitations

- The Logs page reads the in-memory ring (last 500 entries); disk search,
  the trace timeline and the debug bundle arrive in Phase 13 (SPEC G3-13).
- Supabase log shipping (WARNING+ → `app_logs`) arrives with Storage
  (Phase 4, SPEC E2).
- Watchdog restart callbacks are wired when the first worker (MT5 gateway)
  exists in Phase 3.

---

## Phase 3 — MT5 Gateway (in review)

### Built

- `app/mt5/gateway.py` — **MT5Gateway**: every MetaTrader5 call runs on one
  dedicated worker thread behind a command queue (SPEC C3, I-8); public API
  returns `concurrent.futures.Future` (CLI blocks on `.result`, the UI polls
  from a QTimer — the UI thread never blocks). Typed snapshots
  (`AccountSnapshot`, `TerminalSnapshot`, `SymbolSnapshot`, `TickSnapshot`,
  `RateBar`, `PositionSnapshot`, `OrderResultSnapshot`) leave the worker, so
  nothing above `app/mt5` imports the MT5 package.
- Connection lifecycle: `DISCONNECTED → CONNECTING → CONNECTED →
  RECONNECTING` with exponential backoff (initial 1 s → max 30 s, ×2);
  commands submitted while reconnecting fail fast; watchdog heartbeats beat
  from the worker loop (wired for the Phase-2 watchdog in a later phase).
- `app/mt5/errors.py` — error taxonomy (`AuthError`, `ConnectionLostError`,
  `TradeError` with ambiguity flag, `TerminalError`, …) + friendly bilingual
  (EN/FA) messages for terminal codes and 100xx trade retcodes.
- `app/mt5/credentials.py` — password storage ONLY in Windows Credential
  Manager via keyring (SERVICE `MT5TradingWorkstation`); friendly failure
  mapping; never logged (masked logins only, tail 3 digits).
- `app/mt5/symbols.py` — broker suffix resolution (exact → 25 suffix
  candidates → unique prefix), session cache, `resolve_all` for the
  watchlist; default watchlist EURUSD / GBPUSD / XAUUSD (SPEC B4).
- `app/mt5/diagnostics.py` — `run_smoke_test` (package → connect → account →
  terminal → symbols → M15 bars → latency ×3 → disconnect) producing a
  JSON-serializable `SmokeTestReport` saved to
  `logs/mt5_smoke_test_<ts>.json`; `ConnectionProbe` step machine (6 steps)
  for the Settings card, polled non-blocking.
- `app/mt5/demo_trade_test.py` — `--mt5-trade-test`: DEMO-only guard
  (refuses REAL/contest before building any order), volume cap 0.05 lots,
  server-side SL ≈2 % attached, open → close → verify-nothing-left.
- CLI: `--mt5-smoke-test [--login --server --terminal-path
  --password-stdin --symbols --json]` (exit 0/1, report file) and
  `--mt5-trade-test [--symbol --volume --yes]` (exit 0/1, refuses without
  `--yes` or on non-demo). Passwords come from the vault or stdin — never
  argv/env.
- UI: Settings page gained the **MT5 connection** card (login, server,
  terminal path, save-password-to-vault, Test connection with live step
  progress); the probe verdict flows over
  `EventBus.mt5_connection_changed` to the status bar dot/tooltip.
- `app/core/settings.py` — `Mt5AccountSettings` (login/server/terminal path;
  the password never touches QSettings).
- Tests: `tests/fakes/fake_mt5.py` — stateful module emulator (mini order
  engine, scriptable failures, per-call thread ids proving the
  single-thread contract). 109 new tests → 227 total, 92 % coverage.

### How to run / verify

```bat
python -m app --mt5-smoke-test --login 12345678 --server MetaQuotes-Demo --password-stdin
python -m app --mt5-trade-test --login 12345678 --server MetaQuotes-Demo --symbol EURUSD --yes
python -m app        # Settings page → MT5 connection → Test connection
```

### Acceptance checklist (SPEC G3-3)

- [x] All MT5 calls on a single dedicated thread — proven by per-call thread
      ids in `test_gateway.py`.
- [x] UI never blocks — probe polled by QTimer (offscreen-tested with qtbot).
- [x] Reconnect with exponential backoff + fail-fast during backoff — tested.
- [x] Friendly EN/FA messages for common failures — tested.
- [x] Password only in the OS vault; masked logins in logs/reports — tested.
- [x] Demo-only trade test refuses REAL accounts before any order — tested.
- [x] Lint/tests pass locally (ruff, ruff format, mypy, pytest 227/227, 92 %).
- [ ] CI green on the PR (checked when the PR runs).

### Known issues / limitations

- The gateway is not yet owned by the GUI shell; the engine phases (6–8)
  own the long-lived session. The Settings card uses one-shot probes.
- Watchdog restart wiring for the gateway lands with the engine phases.
- Bars/ticks utilities are minimal (Phase 5 builds the market-data layer on
  top of `rates_from_pos` / `tick`).

### Next steps

1. Merge Phase 3 PR after CI is green.
2. Phase 4 — Storage: SQLite WAL + migrations, Supabase outbox mirror,
   health events table (SPEC E).

---

