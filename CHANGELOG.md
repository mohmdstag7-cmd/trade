# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Versions are managed automatically by [release-please](https://github.com/googleapis/release-please)
from Conventional Commits.

## [0.6.1](https://github.com/mohmdstag7-cmd/trade/compare/v0.6.0...v0.6.1) (2026-09-26)


### Bug Fixes

* **ci:** ASCII-only console output in release tools ([2d2b8d6](https://github.com/mohmdstag7-cmd/trade/commit/2d2b8d67b0f97b88c052ed639f07a133387e30cc))
* **ci:** ASCII-only console output in release tools (Windows cp1252 runner) ([fdf2c8d](https://github.com/mohmdstag7-cmd/trade/commit/fdf2c8d0120b4714a728bb09bf83db675a763d0c))

## [0.6.0](https://github.com/mohmdstag7-cmd/trade/compare/v0.5.0...v0.6.0) (2026-09-26)


### Features

* Phase 5.5 — UI v2 (responsive shell) + in-app delta updates + persistent MT5 connect ([30cf7f5](https://github.com/mohmdstag7-cmd/trade/commit/30cf7f5bbe77ceb67ec80f4861b3b8d147102753))
* **ui:** design system v2 — token QSS rewrite, icons, toasts, sidebar, empty states ([6557330](https://github.com/mohmdstag7-cmd/trade/commit/6557330fc6fbcd512637984162fc111659d3cd72))
* **ui:** responsive shell v2, persistent MT5 connect, updates card ([010959c](https://github.com/mohmdstag7-cmd/trade/commit/010959c0805c5d36fd148029db1c7e7efc030011))
* **updater:** manifest-verified delta updates over GitHub release assets ([e4d65c9](https://github.com/mohmdstag7-cmd/trade/commit/e4d65c9214c224be842f8e2c788bf0aa4ebf4167))

## [0.5.0](https://github.com/mohmdstag7-cmd/trade/compare/v0.4.1...v0.5.0) (2026-09-26)


### Features

* **analysis:** market structure, key levels, volatility and sessions ([488e52d](https://github.com/mohmdstag7-cmd/trade/commit/488e52d35d3cc014349cf5bb2cdaa4a20a1bdf76))
* **analysis:** timeframes, self-built indicators, broker clock and data manager ([d4f2045](https://github.com/mohmdstag7-cmd/trade/commit/d4f2045a632880eec16343d5a60da27a31a16d5e))
* **analysis:** trend matrix, correlation, spread, patterns, cards, scanner ([510ee1c](https://github.com/mohmdstag7-cmd/trade/commit/510ee1c0722ccf7de73cc9d742685eb3fcc2d110))
* **calendar:** economic event store, CSV import and MQL5 exporter EA ([3032a59](https://github.com/mohmdstag7-cmd/trade/commit/3032a59b3b14c344bc8c67f391cce58d6e442b23))
* Phase 5 — Market data & analysis (C2/C3 modules, chart, Market page, calendar) ([e5d1b62](https://github.com/mohmdstag7-cmd/trade/commit/e5d1b62975fb42e8762eb7bd0df95ccabd07ccf8))
* **ui:** market page, candlestick chart and analysis service orchestration ([b351fec](https://github.com/mohmdstag7-cmd/trade/commit/b351fec53e28630198a3918ad5b6f10d5c7d2a9c))

## [0.4.1](https://github.com/mohmdstag7-cmd/trade/compare/v0.4.0...v0.4.1) (2026-09-26)


### Documentation

* refresh stale status lines + release-please auto-version annotation ([1f0dce1](https://github.com/mohmdstag7-cmd/trade/commit/1f0dce1425e90db0dbd3dfd3cf352a6c3456d1e4))
* refresh stale status lines in README and PROGRESS ([5adbb53](https://github.com/mohmdstag7-cmd/trade/commit/5adbb533cf77d242fe89e9ee7f0c9002a148a9e8))

## [0.4.0](https://github.com/mohmdstag7-cmd/trade/compare/v0.3.0...v0.4.0) (2026-09-26)


### Features

* **cloud:** Supabase mirror schema, RLS and performance views with setup guide ([d9ed432](https://github.com/mohmdstag7-cmd/trade/commit/d9ed43203d4985011da9c2d9bebaf285ea6cf1b9))
* **storage:** audit service, daily backups, retention cleanup, WARNING+ log sink ([20f9c18](https://github.com/mohmdstag7-cmd/trade/commit/20f9c18ea12b27f87344168b58b2fed1e5a62a92))
* **storage:** idempotent trade history import from MT5 deal history ([78d2957](https://github.com/mohmdstag7-cmd/trade/commit/78d29571555b98d0d8b96432de67546dcd648366))
* **storage:** outbox worker, Supabase mirror and keyring vault ([f8cab64](https://github.com/mohmdstag7-cmd/trade/commit/f8cab643a8b656d75eaebb00d1e14a05e854dfcb))
* **storage:** SQLite core with WAL, migrations and outbox-backed repositories ([c661f1c](https://github.com/mohmdstag7-cmd/trade/commit/c661f1c7120da591aeeb00b0b18e3704db6d11bc))
* **storage:** SQLite core with WAL, migrations and outbox-backed repositories ([fbb1534](https://github.com/mohmdstag7-cmd/trade/commit/fbb153420719fa409462544d2b8b8da65437c7a2))
* **storage:** StorageService facade, --db-check CLI and storage self-check gate ([a652666](https://github.com/mohmdstag7-cmd/trade/commit/a6526663b5d195905d53cb6b77b89e5a69d2cdc6))
* **ui:** Storage & Sync settings card with Supabase probe and audit wiring ([ffaa583](https://github.com/mohmdstag7-cmd/trade/commit/ffaa583d16146e50bd43f894914d9e5dbdcceb08))


### Bug Fixes

* **storage:** close backup destination handle; Args class resolution in db-check tests ([bb35c8f](https://github.com/mohmdstag7-cmd/trade/commit/bb35c8f573997c5acbcc66ee790164e9264bcfc9))


### Documentation

* phase 4 architecture decisions, progress and cloud setup guide ([3941bfa](https://github.com/mohmdstag7-cmd/trade/commit/3941bfa23865b5197b03f1e774260cadff03b030))

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
