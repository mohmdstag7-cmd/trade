## What was built

<!-- Short summary. Reference SPEC sections (e.g. C6.1, G3-7). -->

## Why / spec reference

<!-- Which SPEC part/phase this implements; link issues if any. -->

## How to test manually (dev machine)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy app
pytest
```

## Test on your PC (Windows + MT5)

1. Download the artifact `MT5TradingWorkstation-<PR number>` from **Checks → Build → Artifacts**.
2. Unzip and run: `MT5TradingWorkstation.exe --self-check` → expected: `SELF-CHECK OK: MetaTrader5 ...`.
3. Run `MT5TradingWorkstation.exe` and do: <!-- steps for this phase -->
   - Expected result: <!-- ... -->

## Phase acceptance checklist

<!-- Copy the phase checklist from docs/SPEC.md (G3) with ✓/✗. -->

- [ ] …

## CI / quality gates

- [ ] `ruff check .` clean
- [ ] `ruff format --check .` clean
- [ ] `mypy app` clean
- [ ] `pytest` green (coverage uploaded)

## Known limitations

<!-- What this phase intentionally does not do yet. -->

## Screenshots

<!-- Paste screenshots of new UI pages when possible. -->
