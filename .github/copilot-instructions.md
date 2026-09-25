# Copilot instructions

This file is an identical copy of `AGENTS.md` (kept in sync manually).
Read `AGENTS.md` for the full working rules of this repository.

Key points:

- Read `docs/SPEC.md` + `docs/PROGRESS.md` before any code change.
- Hard constraints: SPEC G4. Architecture rules: SPEC D3.
- Commands: `ruff check .`, `ruff format --check .`, `mypy app`, `pytest`,
  `python -m app`, `pyinstaller installer/app.spec --noconfirm`.
- Branch per phase (`phase/<nn>-<name>`), Conventional Commits, PR into `main`,
  never push to `main` directly, stop after each phase.
