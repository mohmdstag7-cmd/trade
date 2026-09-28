# Architecture — MT5 Trading Workstation

> Updated at the end of every phase (SPEC A5). Decisions are recorded ADR-style:
> **decision + reason**. For the full product specification see `docs/SPEC.md`.

## Current state: Phase 5.5 (Analysis + Updater + Design System v2) — Phases 1-5.5 merged, round-2 review fixes in progress

```
┌────────────────────────────────────────────────────────────┐
│                         UI (PySide6)                        │
│  main_window ─ sidebar ─ pages ─ status_bar ─ palette       │
│       theme (tokens → QSS) ─ i18n (en/fa) ─ settings        │
├────────────────────────────────────────────────────────────┤
│                       Core layer                            │
│  event_bus ─ settings (QSettings) ─ clock (UTC/local)       │
├────────────────────────────────────────────────────────────┤
│                    Observability (Qt-free)                  │
│  logger (16 categories → JSONL) ─ context (session/trace)   │
│  masking (secrets) ─ crash handler ─ watchdog ─ ring buffer │
├────────────────────────────────────────────────────────────┤
│                  MT5 layer (Phase 3)                        │
│  gateway (1 thread + queue) ─ errors ─ credentials ─        │
│  symbols (suffix resolve) ─ diagnostics ─ demo trade test   │
├────────────────────────────────────────────────────────────┤
│                  Storage (Phase 4)                          │
│  db (WAL, per-thread conns) ─ migrations (idempotent) ─     │
│  repositories (outbox in same tx) ─ outbox worker ─         │
│  Supabase mirror ─ audit ─ backups ─ retention ─ log sink   │
├────────────────────────────────────────────────────────────┤
│  Engine (6-8) · Analysis (5) · ML (10) · Backtest (9)       │
│  Analytics (11)                                             │
└────────────────────────────────────────────────────────────┘
```

### Phase 1 module map

| Path | Responsibility |
|------|----------------|
| `app/main.py` | Composition root: CLI (`--version`, `--self-check`, `--debug`), wires QApplication + managers + MainWindow |
| `app/__version__.py` | Version single source of truth (bumped by release-please) |
| `app/core/event_bus.py` | Typed Qt-signal pub/sub (`theme_changed`, `language_changed`, `navigate_requested`) |
| `app/core/settings.py` | `UiSettings` — validated, persisted UI state (theme, language, sidebar) via QSettings INI |
| `app/core/clock.py` | UTC now + local display formatting |
| `app/observability/logger.py` | Structured per-category JSONL logging, rotation/retention/size-cap, runtime levels, debug window, ring buffer |
| `app/ui/theme/tokens.py` | Design tokens: `DARK` / `LIGHT` palettes (SPEC F1) |
| `app/ui/theme/qss.py` | Full stylesheet generated from tokens (objectName/property driven) |
| `app/ui/theme/manager.py` | `ThemeManager`: apply/persist/toggle, emits `theme_changed` |
| `app/ui/i18n/strings.py` | EN + FA string tables (key parity enforced by tests) |
| `app/ui/i18n/translator.py` | `Translator`: lookup + formatting + fallback + RTL direction |
| `app/ui/pages/base.py` | `PAGES` registry (14 pages, groups, build phase), `EmptyStatePage` |
| `app/ui/pages/settings.py` | Working settings page: theme + language + about |
| `app/ui/widgets/sidebar.py` | Grouped, collapsible, persisted sidebar |
| `app/ui/widgets/status_bar.py` | Connection dot, mode badge, clock, toggles, kill-switch slot |
| `app/ui/widgets/command_palette.py` | Ctrl+K command palette (filter + rank + keyboard) |
| `app/observability/context.py` | Session id + trace ids on ContextVars; `trace()` context manager |
| `app/observability/masking.py` | Secret redaction for messages, extras, dicts, crash reports |
| `app/observability/crash_handler.py` | sys/threading/Qt hooks → `crash_reports/*.json` + friendly dialog |
| `app/observability/watchdog.py` | Heartbeat registry, freeze detection, notify/restart callbacks |
| `app/observability/paths.py` | Platform data dirs for logs and crash reports |
| `app/ui/pages/logs.py` | Basic Logs page: level/category/search filters over the ring |
| `app/ui/main_window.py` | Composes everything; page switching; language/theme reactions |
| `installer/app.spec` | PyInstaller one-folder build incl. MetaTrader5 hidden import |
| `installer/innosetup.iss` | Per-user installer (no admin; SPEC I2) |

### Data flow (Phase 1)

User action → widget → manager (`ThemeManager` / `Translator`) → QSettings
persist + Qt signal → listening widgets refresh (QSS re-applied / texts
retranslated / layout direction switched).

## Decisions (ADR)

### ADR-0001 — Layered, event-driven architecture
**Decision:** UI, core, observability, and (later) domain/mt5/engine layers are
strictly separated; components communicate via signals, not direct references.
**Reason:** The engine must be testable without a screen, and the UI testable
without MT5 (FakeMT5 in CI). Direct coupling would make the three-runtime rule
(backtest/paper/live) impossible.

### ADR-0002 — MT5 access via one gateway thread
**Decision:** All `MetaTrader5` imports/calls will be owned by a single
`MT5Gateway` thread with a command queue; everything else receives results via
futures/Qt signals.
**Reason:** The MetaTrader5 package is not thread-safe (SPEC D3.1). Enforced
from Phase 3.

### ADR-0003 — Own indicators, no TA libraries
**Decision:** pandas/numpy vectorized, unit-tested indicators owned by this
repo.
**Reason:** Unmaintained TA packages are a correctness and security risk for a
trading system; reference-value tests pin correctness.

### ADR-0004 — SQLite (WAL) is the source of truth; Supabase is a mirror
**Decision:** Local-first storage with an outbox pattern for Supabase sync.
**Reason:** Trading must keep working offline and during free-tier pauses
(SPEC E1); no data loss; upserts keep idempotency.

### ADR-0005 — Design tokens → generated QSS
**Decision:** One tokens file per theme; the stylesheet is generated from it.
**Reason:** Theme changes become data changes; the dark/light pair and future
polish (Phase 14) stay consistent (SPEC F1).

### ADR-0006 — GitHub-first delivery with FakeMT5 in CI
**Decision:** CI (windows-latest) runs lint/type/tests with FakeMT5; the real
MT5 terminal only exists on the maintainer's PC. Every PR produces a
downloadable PyInstaller artifact with a `--self-check` gate.
**Reason:** GitHub runners cannot run an MT5 terminal (SPEC I1). The artifact
loop lets the maintainer verify real-connection behavior per PR.

### ADR-0007 — Version single source: `app/__version__.py`
**Decision:** release-please bumps `pyproject.toml` and `app/__version__.py`;
a test fails if they drift.
**Reason:** The UI, sessions, and releases must report one version (SPEC H3.7).

### ADR-0008 — QSettings for UI state (Phase 1)
**Decision:** `UiSettings` wraps QSettings (INI). The pydantic-settings stack
arrives with engine/config phases.
**Reason:** Boring, reliable, zero extra dependencies now; the wrapper keeps
the public surface stable.

### ADR-0009 — Lazy per-category sinks + in-memory ring (Phase 2)
**Decision:** One loguru sink per category writing
`logs/<category>/<date>.jsonl` (created lazily on first use), a readable
`all.log`, and a bounded in-memory ring buffer that feeds crash reports and
the Logs page. Every record is shaped by one global patcher that stamps
session/trace ids (ContextVar-based) and applies the masking filter before
anything reaches disk.
**Reason:** Idle categories cost nothing; levels can change at runtime
because filters are consulted at emit time; the ring gives crash reports
and the UI instant, lock-cheap access without touching files that
background threads are writing; the patcher makes secrecy-by-default
impossible to bypass by forgetting a filter (SPEC E3, C-security).

### ADR-0010 — Single-threaded gateway behind futures (Phase 3)
**Decision:** One dedicated worker thread owns every MetaTrader5 call.
Public gateway methods enqueue `(operation, callable, future)` tuples and
return `concurrent.futures.Future` immediately; results are converted to
frozen dataclass snapshots on the worker before delivery. Disconnects map
onto `ConnectionLostError` which flips the machine into `RECONNECTING`
with exponential backoff; commands arriving during backoff fail fast.
**Reason:** The MT5 package is not thread-safe and blocks (SPEC C3, I-8);
futures let the CLI block deliberately while the UI polls from a QTimer,
and typed snapshots keep the broker dependency quarantined inside
`app/mt5` so domain/UI/storage layers stay portable and testable against
the stateful fake (SPEC C2).

### ADR-0011 — Credentials live only in the OS vault (Phase 3)
**Decision:** The account password is stored exclusively in Windows
Credential Manager through `keyring` (service `MT5TradingWorkstation`,
username = login). QSettings keeps login/server/terminal-path only;
in-memory `ConnectRequest` carries the password for one session. The CLI
accepts `--password-stdin` or the vault — never argv/env; logs show
`***`-masked login tails only.
**Reason:** Secrets must never reach code, config files, logs, exports or
crash reports (SPEC C11, I-6); masking at the source plus storage outside
the filesystem makes the leak paths structurally impossible rather than
policy-enforced.

### ADR-0012 — Transactional outbox behind a background mirror (Phase 4)
**Decision:** Every mirrored insert writes the business row AND its outbox
entry inside ONE SQLite transaction. A single `OutboxWorker` thread claims
due batches (pending → in_flight), upserts them to Supabase by the row's
UUID (`on_conflict=id`), and either acknowledges (`synced`), reschedules
with exponential backoff (5 s → 1 h, max 10 attempts → `dead`), or enters a
global 15-minute cooldown when the cloud reports auth/server trouble
(paused free-tier project). Startup re-queues leftover in-flight rows.
**Reason:** Offline-first with no data loss and duplicate-free sync is the
G3-4 acceptance; the atomic pair (row + queue entry) makes "written but
never queued" structurally impossible, and upsert-by-UUID makes replays
idempotent on the cloud side.

### ADR-0013 — Local SQLite is the source of truth, Supabase is a mirror (Phase 4)
**Decision:** WAL mode, `synchronous=NORMAL`, foreign keys on, busy-timeout
5 s, per-thread connections (threading.local). M001 creates the complete
SPEC E2 table set (uuid TEXT PKs, UTC ISO-8601 stamps, time/symbol/strategy
indexes) plus the outbox. Daily online snapshots (`Connection.backup`) keep
7 days; a retention service expires low-level rows (app_logs 30 d,
mt5_requests 14 d, account_snapshots 30 d, performance_metrics 14 d,
health_checks 30 d) and refuses to touch trades/signals/audit/journal.
**Reason:** A trading journal must survive cloud outages and laptops
turning off mid-write (WAL keeps readers/writer unblocked); snapshots
protect against disk corruption; retention keeps the DB small without ever
deleting the irreplaceable records (SPEC E1).

### ADR-0014 — Statements-split DDL migrations (Phase 4)
**Decision:** `MigrationRunner` splits each migration script with
`sqlite3.complete_statement` and executes the statements plus the
`schema_migrations` ledger row inside one BEGIN IMMEDIATE transaction.
`executescript` is never used in the runner.
**Reason:** `executescript` implicitly COMMITs any open transaction, which
would silently break the all-or-nothing guarantee; transactional DDL makes
a crash mid-migration roll the whole step back.

## Planned layer additions (per phase)

- Phase 2: `observability/` categories, trace ids, masking, crash handler,
  watchdog, Logs page.
- Phase 3: `mt5/` gateway + errors + credentials + symbols + diagnostics;
  FakeMT5 in `tests/fakes/` (done).
  Watchdog heartbeats are wired to the gateway thread; startup log gains
  MT5 build, broker and account fields.
- Phase 4: `storage/` db + migrations + repositories + outbox + mirror + audit + backup + cleanup + log sink + history import (done).
- Phase 5: `analysis/` + chart.
- Phase 6: `strategies/` + `engine/signal_pipeline`.
- Phase 7: `risk/`.
- Phase 8: `brokers/` + `engine/` execution + recovery.
- Phase 9: `backtest/`.
- Phase 10: `ml/`.
- Phase 11: `analytics/` + dashboard pages.
- Phase 12: AI loop + Go-Live gate.
- Phase 13: health/soak hardening.
- Phase 14: Persian/RTL polish, light theme QA, installer finalization.

### ADR-0015 — Analysis layering: pure domain, gateway-futures driver (Phase 5)

`app/analysis/` never imports MetaTrader5 or PySide6. Bars enter as
`RateBar` dataclasses; `MarketAnalysisService` drives the pipeline through
the shared gateway's futures and recomputes only on new closed bars. The
UI renders snapshots and never blocks. The forming bar is stripped at the
data-manager boundary so closed-bar evaluation (SPEC C7, I-4) holds by
construction, and `evaluable()` gates evaluation on poisoned bars.

### ADR-0016 — Calendar by CSV export, not API (Phase 5)

The MetaTrader5 Python package cannot read the MT5 economic calendar, so
the app owns a UTC event store fed by (a) manual/CSV import and (b) the
bundled `CalendarExporter.mq5` EA writing `Common\Files\...csv` every
minute; an mtime-throttled poller imports it. Merge semantics are
idempotent (time + currency + title = same event).

### ADR-0017 — In-app updates: manifest-verified deltas over release assets (Phase 5.5)

**Decision.** The updater never calls the GitHub REST API and never ships a
token. Releases carry three update-relevant assets: `manifest.json` (SHA-256 +
size of every file in the portable build, generated by CI and embedded in the
zip too), the full `MT5TradingWorkstation-portable.zip`, and — when the
previous release is available — `delta-v<old>-to-v<new>.zip` containing only
the changed files. The app fetches `releases/latest/download/manifest.json`
directly, diffs it against its local manifest, downloads the delta (full zip
as fallback), verifies every staged byte against the *new* manifest, and
installs via a generated batch script that waits for the app to exit.

**Rationale.** Plain asset URLs are anonymous, rate-limit-free and public-repo
friendly; manifest verification means a truncated/tampered delta can never
reach the installed tree; staging as a sibling folder + `robocopy /E` keeps the
swap atomic-ish on Windows without a separate updater service. User data lives
outside the app folder, so updates never touch it.

**Consequences.** Private-repo hosting would require an alternative transport
(token or mirror) — the fetcher is injectable for that. The first release
without a predecessor publishes no delta (full only). Installing requires one
app restart; auto-install without consent is deliberately not offered.

### ADR-0018 — Design system v2: token-generated QSS + optional icon layer (Phase 5.5)

**Decision.** All visual styling stays in generated QSS built from
`ThemeTokens` (now extended with semantic soft backgrounds, hover/selected,
chart grid and a type/spacing/radius scale). Icons come from qtawesome behind
`app/ui/icons.py`, which degrades silently to an icon-free UI if the dependency
or fonts misbehave. Responsive behaviour is structural (splitters, size
policies, persisted geometry, centered max-width columns) — never fixed pixel
geometry in code.

**Rationale.** QSS-from-tokens keeps dark/light parity testable; a thin icon
wrapper isolates the only runtime-artifact dependency (fonts loaded from a
wheel) so a packaging regression can never crash the app; structural
responsiveness survives arbitrary DPI/scale factors (PassThrough policy set
explicitly).

**Consequences.** New widgets should reuse tokens/objectNames rather than
inline styles; icon call sites must accept `None` (fallback). pyqtgraph's
harmless `QStyleHints` UniqueConnection warning is filtered by a Qt message
handler that otherwise routes Qt output into loguru.
