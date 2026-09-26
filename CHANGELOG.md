# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Versions are managed automatically by [release-please](https://github.com/googleapis/release-please)
from Conventional Commits.

## [0.3.0](https://github.com/mohmdstag7-cmd/trade/compare/v0.2.1...v0.3.0) (2026-09-26)


### Features

* **mt5:** single-threaded MT5 gateway with diagnostics and demo trade test ([b6942e4](https://github.com/mohmdstag7-cmd/trade/commit/b6942e47f9214516a04180d1cc1cd1a46a946619))

## [0.2.1](https://github.com/mohmdstag7-cmd/trade/compare/v0.2.0...v0.2.1) (2026-09-26)


### Bug Fixes

* **build:** bundle numpy for MetaTrader5 and repair build.yml branch filter ([3ecb7e3](https://github.com/mohmdstag7-cmd/trade/commit/3ecb7e346aaecca1d93c94cba45eb03693177f3c))

## [0.2.0](https://github.com/mohmdstag7-cmd/trade/compare/v0.1.0...v0.2.0) (2026-09-26)


### Features

* **core:** add composition root, event bus, settings, clock and logging bootstrap ([5729b3e](https://github.com/mohmdstag7-cmd/trade/commit/5729b3e2ddd509d4ced72e230e2c5754199e858e))
* **observability:** add correlation context and secret masking ([a2d5339](https://github.com/mohmdstag7-cmd/trade/commit/a2d53391b7e27e3cf53dbfb4aa271ac9dbdf4564))
* **observability:** crash handler with masked reports and heartbeat watchdog ([86ac265](https://github.com/mohmdstag7-cmd/trade/commit/86ac265e611c0c4be94346403b617132a626ace9))
* **observability:** structured per-category logging with rotation and debug window ([88af83b](https://github.com/mohmdstag7-cmd/trade/commit/88af83ba39ee231c5b29c5515036d5b1842b8304))
* **ui:** add design tokens, generated QSS, theme manager and en/fa i18n ([2fe60c1](https://github.com/mohmdstag7-cmd/trade/commit/2fe60c18dba39c73f24fdea68229f5a957d7ac90))
* **ui:** add main window, grouped sidebar, status bar, 14 pages and command palette ([41d3c10](https://github.com/mohmdstag7-cmd/trade/commit/41d3c10bc7c50b4bcaed31b66e71a520cc43095d))
* **ui:** basic Logs page and observability wiring in the app shell ([89178ed](https://github.com/mohmdstag7-cmd/trade/commit/89178ed77efc964cf38302b02a93fb2428a4cc3f))


### Documentation

* add SPEC, architecture, progress, changelog and agent instructions ([bb06ec3](https://github.com/mohmdstag7-cmd/trade/commit/bb06ec34e4fe376cf255e0c3895b94766d1d561b))
* record Phase 2 observability in PROGRESS and ARCHITECTURE ([1e0d858](https://github.com/mohmdstag7-cmd/trade/commit/1e0d858002417f643a4fd108fad02d5d16568019))

## [0.1.0] - 2026-09-26

### Added

- Phase 1 — Foundation: repository tooling (ruff, mypy, pytest, pre-commit), full
  specification in `docs/SPEC.md`, architecture and progress docs, CI/CD workflows
  (ci, build, release-please, release, CodeQL), GitHub templates and Dependabot,
  design-token based theme system (dark/light) with generated QSS, i18n
  infrastructure (English + Persian), main window with grouped collapsible sidebar,
  14 pages, status bar, and a Ctrl+K command palette.
