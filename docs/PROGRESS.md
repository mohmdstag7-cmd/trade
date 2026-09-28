# Progress — MT5 Trading Workstation

> To continue work in a new chat, say:
> **"Read docs/SPEC.md and docs/PROGRESS.md and continue."**

## Phase status (SPEC G3)

| # | Phase | Status | Branch / PR |
|---|-------|--------|-------------|
| 1 | Foundation | **merged** | PR #1 |
| 2 | Observability | **merged** | PR #9 |
| 3 | MT5 connection (real) | **merged** | PR #14 |
| 4 | Storage | **merged** | PR #17 |
| 5 | Market data & analysis | **merged** | PR #22 (`phase/05-market-data`) |
| 5.5 | UI v2 + in-app updates (user request) | **in review** | `phase/06-ui-and-updates` |
| 6 | Strategies & signals | not started | — (SPEC numbering unchanged) |
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
2. ~~Phase 2 — Observability~~ — merged (PR #9).

## Phase 2 — Observability (merged)

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
- [x] CI green on the PR — merged (PR #9).
- [x] Build artifact runs `--self-check` (build workflow) — v0.2.1+ artifacts verified.

### Known issues / limitations

- The Logs page reads the in-memory ring (last 500 entries); disk search,
  the trace timeline and the debug bundle arrive in Phase 13 (SPEC G3-13).
- Supabase log shipping (WARNING+ → `app_logs`) arrives with Storage
  (Phase 4, SPEC E2).
- Watchdog restart callbacks are wired when the first worker (MT5 gateway)
  exists in Phase 3.

---

## Phase 3 — MT5 Gateway (merged)

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
- [x] CI green on the PR — merged (PR #14).

### Known issues / limitations

- The gateway is not yet owned by the GUI shell; the engine phases (6–8)
  own the long-lived session. The Settings card uses one-shot probes.
- Watchdog restart wiring for the gateway lands with the engine phases.
- Bars/ticks utilities are minimal (Phase 5 builds the market-data layer on
  top of `rates_from_pos` / `tick`).

### Next steps

1. ~~Merge Phase 3 PR after CI is green.~~ — merged (PR #14).
2. ~~Phase 4 — Storage~~ — merged (PR #17), released as v0.4.0.
   Next: Phase 5 — Market data & analysis.

---
---

## Phase 4 — Storage (merged)

### Built

- `app/storage/db.py` — **Database**: SQLite in WAL mode, `synchronous=NORMAL`,
  foreign keys ON, busy-timeout 5 s; per-thread connections (threading.local);
  nested-transaction support (repositories compose into one atomic write);
  size/WAL/integrity introspection.
- `app/storage/migrations.py` — **MigrationRunner**: numbered, immutable,
  idempotent migrations with a `schema_migrations` ledger; each step runs
  transactionally (statements split via `sqlite3.complete_statement` —
  `executescript` would implicitly COMMIT and break atomicity). **M001**
  creates the full SPEC E2 schema: 21 business tables + outbox, UUID TEXT
  PKs, UTC ISO-8601 timestamps, time/symbol/strategy indexes.
- `app/storage/repositories.py` — typed repositories (signals with state
  transitions, trades with close/import, decision traces, audit log, health,
  mt5 requests, account snapshots, risk events, sessions, app logs + generic
  access). Every mirrored insert enqueues its outbox row in the SAME
  transaction; `import_row` uses deterministic UUID5 ids.
- `app/storage/outbox.py` — **OutboxWorker**: batch claim → upsert → ack;
  exponential backoff (5 s → 1 h) per row, dead-letter after 10 attempts,
  global 15 min cooldown on auth/server errors (paused free tier), in-flight
  requeue on start (crash recovery), `SyncStatus` snapshot for the UI.
- `app/storage/mirror.py` — **SupabaseMirror**: lazy client, upsert
  `on_conflict=id` (duplicate-free), error taxonomy
  (auth/network/server/client) with friendly bilingual messages; service key
  never logged. `NullMirror` for local-only mode.
- `app/storage/vault.py` — **KeyringVault**: named secrets in the OS vault
  (Windows Credential Manager); same fail-backend detection as MT5 creds.
- `app/storage/audit.py` — masked before→after audit trail with typed actions
  (app.started, settings.changed, settings.cloud.changed, risk.kill_switch…).
- `app/storage/backup.py` — daily online snapshot via the SQLite backup API,
  keep 7, skip-if-today-exists.
- `app/storage/cleanup.py` — retention: app_logs 30 d, mt5_requests 14 d,
  account_snapshots 30 d, performance_metrics 14 d, health_checks 30 d;
  trades/signals/audit_log/journal/decision_traces are protected forever;
  slow background scheduler.
- `app/storage/log_sink.py` — buffered WARNING+ loguru sink feeding
  `app_logs` (mirrored automatically); DEBUG/TRACE stay local.
- `app/storage/history_import.py` — closing deals (OUT/INOUT/OUT_BY) → trade
  rows with deterministic ids; overlapping re-imports never duplicate
  (G3-4 acceptance). New `gateway.history_deals` command + `DealSnapshot`
  + `FakeMetaTrader5.history_deals_get` with date filtering.
- `app/storage/service.py` — **StorageService** façade: migrate → backup →
  session row → workers; cloud config from QSettings URL + vault key;
  `apply_cloud_config()`; `stats()`/`sync_status()` snapshots.
- CLI: `--db-check [--json] [--data-dir]` (migrate + stats + integrity);
  `--self-check` now also proves the storage layer inside packaged builds.
- UI: Settings **Storage & Sync** card (DB path/size/WAL, schema version,
  integrity, backups kept, sync queue depth, last sync, cloud ON/OFF) +
  Supabase URL/key form with Save/Remove/Test (`CloudProbe` on a worker
  thread); audit events for cloud + password changes.
- `supabase/schema.sql` — the cloud side of the mirror: all tables
  (timestamptz, jsonb, indexes), RLS enabled everywhere (anon blocked —
  service_role key required), views `v_trade_full`,
  `v_daily_performance`, `v_performance_by_bucket`,
  `v_strategy_config_compare`; bilingual `supabase/README.md` setup guide.

### How to verify (your PC, Windows)

```bat
pip install -e ".[dev]"
python -m app --db-check              :: migrate + stats + integrity
python -m app --db-check --json       :: machine-readable
python -m app                         :: Settings page shows Storage & Sync
```

Cloud (optional): create a free project at supabase.com → run
`supabase/schema.sql` in its SQL Editor → paste URL + service key in the
Settings card → Save → Test. Offline writes sync later without duplicates.

### Acceptance (SPEC G3-4)

- [x] SQLite + idempotent migrations (ledger, transactional DDL).
- [x] Outbox pattern: row + queue entry commit atomically; worker drains
      with backoff; offline writes sync later **without duplicates**
      (upsert by UUID; deterministic ids for history import).
- [x] Supabase schema/views/RLS + setup guide; sync status in the UI;
      free-tier pause handled via global cooldown.
- [x] Audit log wired into settings changes and app lifecycle.
- [x] History import (deal history → trades, idempotent).
- [x] Daily backups (keep 7) + retention cleanup protecting business rows.


---
---

## Phase 5 — Market data & analysis (in review)

### Built

- `app/core/timeframes.py` — `Timeframe` (StrEnum): bar durations, gateway
  constant names, the analysis set (M15/H1/H4/D1).
- `app/analysis/indicators.py` — self-built on numpy (SPEC C1): SMA, EMA
  (SMA-seeded), Wilder RSI/ATR, ADX with ±DI, normalized slope. NaN until
  defined; closed bars only.
- `app/analysis/broker_time.py` — **BrokerClock**: detects the broker UTC
  offset from live ticks (15-min quantum + majority vote, clamped to
  UTC−12…+14), announces whole-hour DST changes, converts server epochs
  ⇄ UTC, exposes the broker trading day (SPEC C2.4).
- `app/analysis/market_data.py` — **MarketDataManager**: per
  (symbol, timeframe) closed-bar cache with incremental merge/dedupe and a
  bound; the still-forming bar never enters the series; sanity checks
  (missing bars, zero volume, spikes > N × ATR, weekend gaps, time jumps,
  stale ticks) with an `evaluable()` gate that blocks evaluation of a
  poisoned newest bar (SPEC C2.2–C2.3).
- `app/analysis/structure.py` — confirmed swings (strength bars each side,
  `confirmed_index` = no look-ahead), HH/HL/LH/LL, BOS/CHoCH from confirmed
  swings vs closed bars, trend/range classification (C3.2).
- `app/analysis/levels.py` — S/R clustering of swing prices in ATR
  tolerance, previous day/week H/L/C, session highs/lows (wrapping windows,
  broker wall time), adaptive round-number grid, nearest-levels helper
  (C3.3).
- `app/analysis/volatility.py` — ATR percentile over a trailing window
  (midrank), ADR excluding the forming day, % of ADR used today, regime
  buckets low/normal/high (C3.4).
- `app/analysis/sessions.py` — **SessionClock** over broker wall hours with
  wrapping windows: current session, next transition, per-session extremes
  (C3.5).
- `app/analysis/trend.py` — per-timeframe rule assessment (EMA structure,
  slope, ADX/±DI, RSI extremes) with reason keys; weighted overall bias
  score −100…+100 (M15 1, H1 2, H4 3, D1 4) (C3.1).
- `app/analysis/correlation.py` — rolling Pearson matrix of return tails +
  currency strength meter (base adds / quote subtracts; metals move the
  USD leg) (C3.6).
- `app/analysis/spread.py` — **SpreadMonitor**: rolling per-(symbol, hour)
  median; abnormal when > 2 × typical (C3.7).
- `app/analysis/patterns.py` — engulfing / pin bar / inside bar on closed
  bars; information and future ML features only (C3.8).
- `app/analysis/card.py` — **AnalysisCard**: rule-generated plain-language
  summary with i18n lines (EN/FA render), informational verdicts
  (wait / watch / bias_up / bias_down), event & spread risk gating (C3.10).
- `app/analysis/scanner.py` — per-symbol setup state (ready / forming /
  none) with a documented |bias| × proximity proxy score until the ML
  layer supplies calibrated probability × EV (C3.11).
- `app/calendar/` — economic calendar (C3.9): `EventStore` (idempotent
  merge, CSV round-trip, `upcoming()` / `risk_window()` queries),
  `CalendarImporter` (UTF-8/BOM tolerant) + `ExporterFilePoller` (mtime
  throttled); `mql5/CalendarExporter.mq5` — full EA source exporting
  `CalendarValueHistory` to `Common\Files\mt5_workstation_calendar.csv`.
- `app/analysis/service.py` — **MarketAnalysisService**: drives the whole
  C3 pipeline through gateway futures (UI thread never blocks); computes
  per-symbol snapshots only when a new closed bar appears; cross-symbol
  correlation/strength/scanner; offline-safe (empty state until the
  terminal connects).
- UI: `app/ui/widgets/chart.py` — pyqtgraph candlestick chart (cached
  `QPicture`, volume sub-plot, last-price line, theme tokens);
  `app/ui/pages/market.py` — the Market page (symbol/timeframe selectors,
  chart, analysis card, trend matrix, key levels, scanner strip, calendar
  countdown, spread/broker badges) driven by 5 s poll + 60 s refresh
  timers; status-bar clock now shows the broker wall time once the offset
  is detected.

### How to verify (your PC, Windows)

```bat
pip install -e ".[dev]"
python -m app                 :: Market page: connect first on Settings
:: after connecting: cards for EURUSD / GBPUSD / XAUUSD appear and refresh
:: on every closed bar; switch symbols/timeframes; check the spread badge
:: and the broker clock in the status bar.
```

Calendar (optional): attach `mql5/CalendarExporter.mq5` to any chart in
your terminal (read-only EA) — events appear under Market → Economic
calendar within minutes; or import a CSV manually.

### Acceptance (SPEC G3-5)

- [x] Data + sanity checks (C2.2–C2.3) with the evaluable gate — tested.
- [x] Broker time / UTC offset detection with DST handling (C2.4) — tested.
- [x] All C3 modules: trend matrix, structure, levels, volatility,
      sessions, correlation/strength, spread, patterns, calendar, cards,
      scanner — each unit-tested (C3.1–C3.11).
- [x] Chart renders candles + volume + last price, theme-aware.
- [x] Market page: cards for 3 symbols update on closed bars — verified
      in CI with the fake gateway (real-terminal check happens on your PC).

## Phase 5.5 — UI v2 + in-app updates (user request)

> Triggered by user feedback on 0.5.0: "the UI is not responsive, dimensions are
> off, not beautiful/professional enough" + "add in-app updates that download
> only the changed parts". Shipped as one phase: design-system v2, responsive
> shell, persistent MT5 connect (a functional gap discovered from the user's
> startup log) and a manifest-verified delta updater.

### Built

- **Design system v2** — `app/ui/theme/tokens.py` extended (semantic soft
  backgrounds, borders-strong, hover/selected, chart grid, mono stack, type +
  spacing + radius scales); `app/ui/theme/qss.py` rewritten to cover every
  widget class in use (buttons incl. Accent/Ghost/Danger/Badge/Segment variants,
  inputs + focus states, checkboxes/radios, tables/trees/lists + headers, tabs,
  progress, sliders, splitters, menus, tooltips, scrollbars, tri-state
  connection dot, semantic badges, toasts).
- **Icons** — `app/ui/icons.py`: qtawesome (MDI) with a graceful no-icon
  fallback; sidebar, collapse chevron and empty states theme their icons.
- **Toasts** — `app/ui/widgets/toast.py`: stacked auto-dismiss notifications
  anchored to the window corner (RTL-aware), used for probe results and update
  availability.
- **Responsive shell** — `main_window.py`: window geometry + maximized state
  persist (QSettings), min size 1020×640, toasts host; `market.py`: horizontal
  `QSplitter` (chart | analysis column) with min widths + stretch, segmented
  symbol selector, mono numerals for levels/trend; `settings.py`: centered
  780 px card column inside a scroll area (no more clipped forms on narrow
  windows); empty states got icon + chip headers.
- **High-DPI + clean logs** — PassThrough rounding policy set before
  QApplication; a Qt message handler routes Qt output into loguru and swallows
  pyqtgraph's harmless `QStyleHints` UniqueConnection warning (the one visible
  in the user's startup log).
- **Persistent MT5 connect (gap fix)** — Settings → Connect/Disconnect now
  drives the *shared* gateway via `ConnectWorker` (QThread; UI never blocks);
  auto-connect on startup with saved credentials (checkbox persisted); the
  gateway's live state (connecting/connected/reconnecting/disconnected) is
  mirrored on the EventBus into the status bar. Until 0.5.0 the GUI had no way
  to connect the shared gateway at all — Market stayed offline forever.
- **In-app delta updater** — `app/updater/` (version, manifest, github,
  service, worker):
  - CI writes `manifest.json` (SHA-256 + size of every file) into the portable
    build and attaches it to the release; `tools/build_delta.py` compares the
    previous release's portable zip and publishes
    `delta-v<old>-to-v<new>.zip` containing only changed files
    (+ `__delta__.json` removals).
  - The app checks `releases/latest/download/manifest.json` (no API rate
    limits, no token, repo is public), picks delta when possible with full-zip
    fallback, **verifies every staged byte against the new manifest**, and
    stages a sibling `MT5TradingWorkstation.update-<ver>/` folder.
  - Install = generated `apply_update_<ver>.bat`: waits for the app PID to
    exit, deletes the manifest-listed removed files, `robocopy /E` staging →
    app dir, relaunches. User data (SQLite, logs, calendar) lives in
    `%LOCALAPPDATA%\MT5TradingWorkstation` and is never touched.
  - UI: Settings → Updates card (check now, download progress, "Restart &
    install") + optional startup check (default on, notify-only via toast).
  - Dev mode (running from source) disables the updater explicitly.

### How to verify (your PC, Windows)

```bat
MT5TradingWorkstation.exe            :: new look everywhere; resize the window —
                                     :: the Market split and Settings column adapt
:: Settings → Connect (or tick auto-connect): status bar shows live state,
:: Market page fills with data after the first closed bar
:: Settings → Updates → Check for updates: downloads only the delta when a new
:: release exists, then "Restart & install" applies it without a manual download
```

### Acceptance

- [x] Design tokens + full QSS for both themes — tested.
- [x] Window geometry persists across restarts — implemented (QSettings).
- [x] Market page responsive via splitter; settings column centered/scrollable.
- [x] Persistent connect/disconnect + auto-connect + live gateway state — tested.
- [x] Delta update pipeline (manifest build/diff/verify/apply) — 18 unit tests,
      network fully faked; apply script content asserted.
- [x] Release workflow publishes `manifest.json` + `delta-v*.zip` + checksums.
- [x] The `QStyleHints` startup warning is filtered; all Qt output now flows
      into the observability layer.

## Code review round 1 (branch `fix/code-review-round1`)

### Scope

Full-repository review (static gates + line-by-line manual review with runtime
verification). Every finding, its severity and its fix status is tracked in
[CODE_REVIEW.md](../CODE_REVIEW.md) — the source of truth for this round.

### Highlights of what was fixed

- **Analysis correctness:** the first fetch ingested the still-forming bar as
  "closed" and froze its partial OHLC forever (stale PDH/PDL/PDC, corrupted
  indicators). Ticks are now marked before rate ingestion, with a broker-clock
  wall-clock fallback; `levels`/`volatility` moved to a single closed-only
  contract; the `evaluable()` data-quality gate is wired into the pipeline.
- **UI:** Market page timers actually start (the page used to freeze forever),
  toasts are visible (host geometry), the trend card no longer leaks ghost
  rows, the update-check button no longer bricks itself, workers are parented
  and awaited at shutdown, stats polling only runs while Settings is visible.
- **MT5 gateway:** umbrella exception guard (silent worker death), disconnect
  during reconnection, permanent auth failures stop the reconnect loop,
  shutdown race closed, `symbol_info`/`select_symbol` state contracts,
  heartbeat wired to the watchdog, single-instance guard.
- **Secrets:** masking now covers `access_token`-style keys, URL userinfo and
  `mask_dict` prefixes; exception tracebacks are masked before reaching
  logs/`app_logs`/the Supabase mirror; `--password-stdin` uses getpass.
- **Storage:** outbox failures isolated per table (one bad table no longer
  kills unrelated rows), log sink actually registered with loguru, synced
  outbox purge + `updated_at` index (migration 002), `*_json` columns no
  longer double-encoded into jsonb, identifier validation, thread-bound
  connection teardown.
- **Updater:** staging verified BEFORE install (fail-closed, keep staging),
  removed-list path traversal closed, unmanifested staging files rejected,
  release zips cleaned, previous-tag selection fixed so delta packages are
  actually built, release-please token documented (needs `RELEASE_PLEASE_TOKEN`
  secret).
- **MQL5:** `CalendarValueHistory` bool/ArraySize bug (only ONE event was
  exported), real-UTC timestamps (risk windows were off by the broker
  offset), atomic temp-file publish, proper CSV quoting.

### Quality gates

`ruff check` / `ruff format --check` / `mypy app` / `pytest` (601 tests) all
green on this branch.

### Known deferred items

See CODE_REVIEW.md §10 (code signing, cloud schema versioning, rollback on
failed apply, MT5 Common-path resolution, dead-letter tooling, dependency
lock file, SHA-pinning actions, `order_check` retcode on real hardware).
