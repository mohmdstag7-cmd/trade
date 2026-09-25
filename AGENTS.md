# AGENTS.md — rules for AI agents working in this repository

Read this file first. Then read `docs/SPEC.md` (source of truth) and
`docs/PROGRESS.md` (current state) before writing any code.

## Identity

You are working on **MT5 Trading Workstation**: a production-quality Windows
desktop app (Python 3.11, PySide6) that connects to the user's local MetaTrader 5
terminal, analyzes markets, estimates win probability, executes trades under
strict risk control, and syncs history to Supabase.

## Non-negotiable rules (SPEC G4)

- Never promise profit; probabilities are estimates, always labeled with sample
  size and uncertainty.
- Paper mode by default; REAL accounts and Auto mode require typed confirmation
  and audit logging.
- Every live order carries a **server-side SL**. No martingale / grid / averaging
  down / risk escalation after losses.
- No look-ahead bias, no data leakage, no shuffled time series.
- The bot never modifies manual trades (magic 0).
- Secrets never appear in code, logs, exports, debug bundles, or LLM requests.
- The shipped app uses **only real MT5 data**; mocks live in `tests/` only.
- Respect broker differences: digits, point, tick value, contract size, symbol
  suffixes, stops/freeze levels, filling modes, hedging vs netting, server
  time/DST.

## Architecture rules (SPEC D3)

- All `MetaTrader5` imports live in `app/mt5/gateway.py` only (when it exists).
  One dedicated gateway thread owns every MT5 call through a command queue.
- The UI thread never blocks. Workers for MT5/network/DB/ML/backtests; heavy
  jobs run in separate processes.
- Evaluation is **closed-bar driven**; ticks serve position management, paper
  fills, and throttled UI updates only.
- One logic, three runtimes: backtest / paper / live share strategy, features,
  risk, costs, and position management via the `Broker` interface.
- Domain logic (risk math, sizing, state machine, metrics) is pure functions.
- Client-generated UUIDs; Supabase writes are upserts; state survives crashes.

## Workflow (SPEC H1)

- Never push to `main`. Branch per phase: `phase/<nn>-<short-name>`, PR into main.
- Conventional Commits (`feat(risk): ...`, `fix(mt5): ...`, `test(...): ...`).
- Before every push run: `ruff check .`, `ruff format --check .`, `mypy app`,
  `pytest`. Do not push red code.
- Update `docs/PROGRESS.md` and `CHANGELOG.md` in every PR; PRs use
  `.github/pull_request_template.md` and include the phase acceptance checklist.
- Stop after each phase and wait for the maintainer (SPEC A2).

## Commands

```bat
pip install -e ".[dev]"
ruff check .            :: lint
ruff format .           :: format
ruff format --check .   :: CI format gate
mypy app                :: types
pytest                  :: tests (+coverage, offscreen Qt)
python -m app           :: run the app
python -m app --version :: print version
python -m app --self-check :: verify MetaTrader5 import (exit 0/1)
```

Build: `pyinstaller installer/app.spec --noconfirm` →
`dist\MT5TradingWorkstation\MT5TradingWorkstation.exe`.

## Phase map

See `docs/SPEC.md` Part G3 for the 14 phases and their acceptance checklists.
Work phase by phase; the SPEC is the source of truth for what comes next.
