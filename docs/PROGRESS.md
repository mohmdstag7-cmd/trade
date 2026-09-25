# Progress — MT5 Trading Workstation

> To continue work in a new chat, say:
> **"Read docs/SPEC.md and docs/PROGRESS.md and continue."**

## Phase status (SPEC G3)

| # | Phase | Status | Branch / PR |
|---|-------|--------|-------------|
| 1 | Foundation | **in review** | `phase/01-foundation` |
| 2 | Observability | not started | — |
| 3 | MT5 connection (real) | not started | — |
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

## Phase 1 — Foundation (current)

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
- [ ] CI green on the PR (checked when the PR runs).
- [ ] Build artifact downloads and runs `--self-check` (build workflow).

### Known issues / limitations

- The packaged exe shows a console window alongside the GUI (needed for
  `--self-check` output); a windowed build ships in the Release phase.
- Sidebar buttons are text-only; icons arrive with the Phase 14 polish.
- Persian UI is functional (RTL direction + strings) but full RTL polish is
  Phase 14 per SPEC.
- Single-instance guard and `SetThreadExecutionState` arrive with the engine
  phases (SPEC D3.8) — nothing to protect yet in Phase 1.

### Next steps

1. Merge Phase 1 PR after CI is green and the artifact self-check passes.
2. Phase 2 — Observability: logger categories, trace ids, masking, crash
   handler, watchdog, basic Logs page (SPEC E3, G3-2).
