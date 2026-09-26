# MT5 Trading Workstation

A production-quality Windows desktop application that connects to your **MetaTrader 5** account, analyzes markets, estimates the win probability of trade setups, executes trades under strict risk control, logs everything, and stores history in Supabase for AI-driven analysis.

> **Status:** Phases 1–5 complete (foundation, observability, MT5 connection, storage, market data & analysis) **plus the Phase 5.5 UI overhaul** (design system v2, responsive shell, persistent MT5 connect, in-app delta updates). Next: Strategies & signals. See [docs/PROGRESS.md](docs/PROGRESS.md).

## What it is (and is not)

- Connects to **your own** MT5 terminal installed on the same Windows PC (any broker, demo or real) through the official `MetaTrader5` Python package. It does **not** connect to the broker directly.
- Operating modes: **Analysis-only**, **Paper** (default on first launch), **Semi-auto**, **Auto** (only after the Go-Live gate).
- The win probability is a **statistical estimate**, always shown with its sample size and uncertainty. The app does **not** guarantee profit.
- No martingale, grid, averaging down, or risk increase after losses — ever.

## Development quickstart

Requires **Python 3.11 64-bit**.

```bat
:: 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate

:: 2. Install the package with dev tools
pip install -e ".[dev]"

:: 3. Run quality gates
ruff check .
ruff format --check .
mypy app
pytest

:: 4. Launch the app
python -m app
```

## Cloud mirror (optional)

Everything is stored locally first. To mirror to Supabase:

1. Create a free project at [supabase.com](https://supabase.com).
2. Run [`supabase/schema.sql`](supabase/schema.sql) in its SQL Editor.
3. In the app: **Settings → Storage & Sync** → paste the Project URL and the
   service_role key → **Save** → **Test**. The bilingual guide with details:
   [supabase/README.md](supabase/README.md).

## Build the Windows executable

```bat
pip install -e ".[dev]"
pyinstaller installer/app.spec --noconfirm
dist\MT5TradingWorkstation\MT5TradingWorkstation.exe --self-check
```

## Documentation

- [docs/SPEC.md](docs/SPEC.md) — full product specification (source of truth)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — modules, data flow, decisions
- [docs/PROGRESS.md](docs/PROGRESS.md) — phase status and next steps
- [CHANGELOG.md](CHANGELOG.md)

## License

Proprietary — all rights reserved. See [LICENSE](LICENSE).
