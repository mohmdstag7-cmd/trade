# Architecture — MT5 Trading Workstation

> Updated at the end of every phase (SPEC A5). Decisions are recorded ADR-style:
> **decision + reason**. For the full product specification see `docs/SPEC.md`.

## Current state: Phase 1 (Foundation)

```
┌────────────────────────────────────────────────────────────┐
│                         UI (PySide6)                        │
│  main_window ─ sidebar ─ pages ─ status_bar ─ palette       │
│       theme (tokens → QSS) ─ i18n (en/fa) ─ settings        │
├────────────────────────────────────────────────────────────┤
│                       Core layer                            │
│  event_bus ─ settings (QSettings) ─ clock (UTC/local)       │
├────────────────────────────────────────────────────────────┤
│                    Observability                            │
│  logger bootstrap (loguru) — full E3 in Phase 2             │
├────────────────────────────────────────────────────────────┤
│  MT5 gateway (Phase 3) · Storage (Phase 4) · Engine (6-8)   │
│  Analysis (5) · ML (10) · Backtest (9) · Analytics (11)     │
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
| `app/observability/logger.py` | loguru console bootstrap; expands into E3 categories in Phase 2 |
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

## Planned layer additions (per phase)

- Phase 2: `observability/` categories, trace ids, masking, crash handler,
  watchdog, Logs page.
- Phase 3: `mt5/` gateway + connection + symbols; FakeMT5 in `tests/fakes/`.
- Phase 4: `storage/` sqlite + migrations + outbox + supabase.
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
