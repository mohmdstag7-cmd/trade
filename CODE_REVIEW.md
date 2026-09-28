# CODE_REVIEW.md — Full Code Review Findings (Round 1)

> Reviewer: automated multi-agent review (2026-09-28), branch `fix/code-review-round1`.
> Scope: entire repository (`app/`, `tests/`, `tools/`, `installer/`, `.github/`, `mql5/`, `supabase/`, docs).
> Method: static tooling + line-by-line manual review, with runtime verification of the
> most important findings (findings marked ⚡ were reproduced by executing the code).

## 0. Tooling baseline (all green before manual review)

| Gate | Result |
|---|---|
| `ruff check .` | ✅ 0 issues |
| `ruff format --check .` | ✅ 150 files formatted |
| `mypy app` | ✅ no issues in 85 files |
| `pytest` (offscreen Qt) | ✅ **601 passed** |

Every finding below is therefore a *logic / security / architecture* issue that the
quality gates cannot catch. Severity: CRITICAL > HIGH > MEDIUM > LOW.

Fix status legend: `[ ]` open — `[x]` fixed in this branch — `[~]` partially fixed / needs decision.

---

## 1. CRITICAL

### C1. ⚡ First fetch ingests the still-forming bar as "closed" — frozen forever → PDH/PDL/PDC stale by one day
- **Files:** `app/analysis/service.py:355-399` (`_drain` ingests rates at :372 *before* marking the tick at :376-380), `app/analysis/service.py:433-443` (`_maybe_forming` returns `False` when no tick seen yet), `app/analysis/market_data.py:161-169` (ingest de-dupes by time and **never updates** a known bar).
- **Why:** `copy_rates_from_pos` always returns the current forming bar as the newest element. On the first batch there is no tick epoch yet, so the forming-bar guard falls back to "treat everything as closed" and the partial bar enters the closed series; every later fetch then skips it (`if bar.time in existing: continue`). Reproduced end-to-end: a first fetch mid-day froze the D1 bar at partial OHLC/volume, making **previous-day/week levels (PDH/PDL/PDC, PWH/PWL/PWC) stale by one day** and corrupting indicators/ATR/swings. Recurs after every `invalidate_symbols()`.
- **Fix:** mark the tick (and sample the broker clock) **before** ingesting rate futures in `_drain`; implement the promised wall-clock fallback in `_maybe_forming` (guarded by `clock.detected` so an undetected offset can't misexclude bars). `[x]`

### C2. ⚡ Market page poll/refresh timers are never started — the page is frozen forever
- **Files:** `app/ui/pages/market.py:203-226` (timers created, started only in `set_service()`), `app/ui/main_window.py:106-111` (service passed via constructor; `set_service()` has **zero** production callers).
- **Why:** tests call `set_service()` directly, so CI passes; production never starts the 5 s poll timer nor the 60 s refresh timer → chart/cards/scanner/calendar never receive a snapshot after the initial render.
- **Fix:** start the timers in `__init__` when a service is provided (keep `set_service()` working for tests). `[x]`

### C3. ⚡ Toasts are invisible — `ToastHost` never gets a geometry, children clipped
- **Files:** `app/ui/widgets/toast.py:124-153, 186-192`.
- **Why:** `ToastHost` is a plain child widget with `self.hide()` and **no geometry ever set** (defaults to 100×30). `_relayout()` positions cards at `x ≈ window.width()-358` *relative to the host*, so cards sit ~99% outside the visible clip region. Runtime-verified. Every toast (probe verdicts, update notices) is invisible. No tests existed for `ToastHost`.
- **Fix:** size the host to the window in `_relayout()`/eventFilter before laying out cards. `[x]`

---

## 2. HIGH — secrets & logging (violates SPEC G4 "secrets never appear in logs/exports")

### H-S1. ⚡ Secret masking misses the most common key shapes
- **File:** `app/observability/masking.py:26-63, 105-107`.
- **Verified behavior:** `access_token=…`, `refresh_token=…`, `session_token=…`, `mt5_password=…`, `x_api_key=…`, `https://user:pass@host` → all **pass through unmasked**; `mask_dict` disagrees with `mask_text` (exact-match keys only). `\btoken\b` fails because `_` is a word char.
- **Fix:** key matching by suffix (`token$`, `password$`, `secret$`, `key$`, `credential`), add URL-userinfo pattern, align `_is_secret_key` with regex semantics. `[x]`

### H-S2. ⚡ Exception tracebacks reach log files and the Supabase mirror unmasked
- **Files:** `app/observability/logger.py:231-233` (`payload["exception"]` written raw), `app/storage/log_sink.py:89-90` (`str(exception)` → `app_logs` → cloud), loguru also appends raw tracebacks to `all.log`/stderr sinks.
- **Fix:** run exception payloads through `mask_text()` in both sinks. `[x]`

### H-S3. Masking gaps: `supabase.service_key` entry name not in `SECRET_KEYS`; `Authorization: Bearer x` double-redacts
- **Files:** `app/observability/masking.py:26-49, 61-72`. `[x]` (entry name added; Bearer double-redact fixed)

---

## 3. HIGH — MT5 gateway (`app/mt5/`)

### H-G1. Worker thread can die silently — `_run` has no umbrella exception guard
- **Files:** `app/mt5/gateway.py:277-293` (`_run`), `445-474` (`_poll_reconnect` catches only `MT5Error`), `367-413` (`_establish` has code outside its `try`: `int(request.login)` :372, `_to_account_snapshot` :399, log :404-412).
- **Why:** one unexpected exception (e.g. broker returns non-numeric balance) kills the worker: state stays `RECONNECTING`, every command fails forever, futures never resolve, **nothing notices** (heartbeat not wired to watchdog, see M-G6).
- **Fix:** umbrella `try/except Exception` around the loop body (log + continue) and around `_poll_reconnect`. `[x]`

### H-G2. `order_check` success retcode may be `0`, not `10009` — false failure on real terminals
- **Files:** `app/mt5/gateway.py:525-532` reuses `raise_for_order_result` (`errors.py:268-280`) built for `order_send`; the fake returns 10009 so tests pass.
- **Action:** needs one-time verification on real hardware. `raise_for_order_result` now accepts retcode `0` for check results. `[x]` (defensive; confirm on a real terminal)

### M-G3. No way to stop reconnection: `disconnect()`/`connect()` fail fast while `RECONNECTING`; permanent `AuthError` retried forever
- **File:** `app/mt5/gateway.py:265-273, 445-474`. **Fix:** `disconnect()` now cancels reconnection and tears down; non-retryable errors (`AuthError`, invalid params) stop the loop. `[x]`

### M-G4. Shutdown race: a command can slip past `_drain_queue` and hang forever
- **Files:** `app/mt5/gateway.py:260-275, 295-305`. **Fix:** re-check the stop flag inside the worker after draining; late submissions after final drain fail fast with `RuntimeError`. `[x]`

### M-G5. `select_symbol` bypasses the connected-state contract → raw `AttributeError` when disconnected; `symbol_info` failure always reported as "symbol not found" (no `last_error`, no reconnect trigger)
- **Files:** `app/mt5/gateway.py:194-199, 507-512`; `InvalidSymbolError` (`errors.py:233-238`) was never used. **Fix:** state guard + `_module_or_fail()` on `select_symbol`; `symbol_info` now maps disconnect codes and raises `InvalidSymbolError`. `[x]`

### M-G6. Heartbeat plumbing exists but is never wired — watchdog cannot see the gateway (silent death undetectable)
- **Files:** `app/mt5/gateway.py:87, 101, 332-339`; `app/main.py:379-384`; no `Watchdog.register("mt5-gateway", …)` anywhere. **Fix:** wired in `main.py` (stall threshold 90 s, restart=log-only). `[x]`

### M-G7. `demo_trade_test` calls `gateway._module_or_fail()` off the worker thread
- **File:** `app/mt5/demo_trade_test.py:168`. **Fix:** filling-mode constants now captured via a gateway command (`filling_constants()`), private access removed. `[x]`

### L-G8. `stop()` can block the UI thread up to 5 s (owned-mode probe); `_StepRecorder.__exit__` swallows `KeyboardInterrupt`/`SystemExit`; smoke-test bar age compares server-time epochs to local `time.time()`; `wait_for_result(timeout_s=0)` means "default"; triple-duplicated `_mask_login`; unbounded `GatewayStats.state_history`; missing `module.shutdown()` on failed initialize; no `snapshot.login == request.login` verification
- **Files:** `app/mt5/diagnostics.py:196-199, 251-262`, `app/mt5/gateway.py:131-157, 248-255, 379-403, 645-647`, `app/mt5/models.py:193-197, 269`, `app/mt5/credentials.py:115-118`. `[x]` for: KeyboardInterrupt/SystemExit re-raise, wait_for_result `None` handling, `state_history` bound (deque maxlen=100), `module.shutdown()` on failed initialize, login verification, single shared `mask_login`. `[ ]` deferred: deferred-stop pattern for probe owned-mode, `_mask_login` dedup (kept, low value).

### L-G9. Fill-mode bitmask comment claims a "4 = RETURN" bit (doesn't exist); `_find_position` races slow brokers; close uses pre-open tick
- **Files:** `app/mt5/models.py:121-122`, `app/mt5/demo_trade_test.py:86-96, 196-208`. `[x]` comment fixed; `[ ]` demo-test retry nits deferred (demo-only tool).

---

## 4. HIGH — analysis pipeline (`app/analysis/`)

### H-A1. Contract split: `levels`/`volatility` expect the forming daily bar at `[-1]`, the service feeds closed-only series
- **Files:** `app/analysis/levels.py:72-93` (`prev = d1_bars[-2]`), `app/analysis/volatility.py:42-48, 100`, `tests/test_analysis_levels_sessions.py:59-77`.
- **Why:** under closed-bar semantics PDH/PDL/PDC/PWH/PWL/PWC are off by one day and `adr_used_pct` reports the last *completed* day as "today". Masked today only by bug C1.
- **Fix (chosen): closed-only everywhere.** `previous_day/week` read `[-1]`; `adr` computes over the whole closed series; `adr_used_pct` uses the live tick to size the forming day when available. Tests updated to the closed-only contract. `[x]`

### H-A2. `evaluable()` gate and sanity issues are never wired into the pipeline (dead safety code)
- **Files:** `app/analysis/market_data.py:313-346`, `app/analysis/service.py:516, 552` (`issues=()` hardcoded).
- **Why:** a SPIKE/TIME_JUMP on the newest bar does not skip evaluation, contradicting `market_data.py:16-17` and SPEC C2.3; data-quality problems invisible in the UI.
- **Fix:** `_compute_symbol` skips the card when `not evaluable(...)` and forwards real issues into `AnalysisCard.data_issues`. `[x]`

### M-A3. Correlation matrix aligns return tails by position, not timestamp; pairs with as few as 3 points correlate
- **File:** `app/analysis/correlation.py:44-63` + caller `service.py:589-594`. `[x]` (timestamp-aligned via dict intersection; `< window → 0.0` enforced per docstring)

### M-A4. ADX seed polluted by NaN→0 DX values; emits biased values where docstring promises NaN
- **File:** `app/analysis/indicators.py:159-164`. Verified numerically (module ≈44.7 vs Wilder 100 on a perfectly trending series). **Fix:** DX NaNs kept out of the seed (first seed taken from the first `period` valid DX values); ADX is NaN until the seed. `[x]`

### M-A5. Card spread flag is a permanent no-op (`latest_abnormal` doesn't exist; separate fresh SpreadMonitor per card build)
- **Files:** `app/analysis/card.py:162-166, 178-179`, `app/analysis/service.py:159-161`. **Fix:** card builder now receives the service's abnormal-spread flag. `[x]`

### M-A6. Calendar force-recompute defeated by the new-bar dedupe gate (`force` effectively dead)
- **Files:** `app/analysis/service.py:581-585` vs `469-474`. **Fix:** `_compute_symbol(..., force=True)` bypasses the early-return; `poll(force=True)` propagates. `[x]`

### M-A7. Asymmetric DI handling biases trend direction bearish (bears flip 0→-1, bulls never 0→+1)
- **File:** `app/analysis/trend.py:100-105`. **Fix:** symmetric `direction = +1` for bulls. `[x]`

### M-A8. Scanner proximity uses D1 ATR against H1 levels; card uses H1 ATR for the same levels
- **Files:** `app/analysis/service.py:612` vs `489, 496`. **Fix:** scanner now receives H1 ATR (consistent with card/level distances). `[x]`

### L-A9. `true_range`/`atr`/`adx` raise `IndexError` on empty input; no zero-division guard for `atr_smooth == 0`; sessions overlap "most recent wins" claim false; spread hour bucket labeled "UTC" but is server time; default `BrokerClock(utc_now_fn=lambda: 0.0)` trap; TIME_JUMP bar still appended (non-monotonic deque); dead code (`scanner if reasons: pass`, unranked `rank()`, `session_start_epoch`, `build_currency_notes`, `strength_ranking` unused in production); `daily_returns_by_symbol` parameter actually receives closes; patterns docstring 0.6/0.4 vs "opposite third"; `RateBar.time` doc says "UTC" but is server-encoded
- **Files:** `app/analysis/indicators.py:108, 156-160`, `app/analysis/sessions.py:28, 47-57`, `app/analysis/service.py:157, 451`, `app/analysis/market_data.py:129, 228-239`, `app/analysis/scanner.py:32-39, 73-74`, `app/analysis/correlation.py:110`, `app/analysis/patterns.py:74`, `app/mt5/models.py:161`. `[x]` for: IndexError→empty-array contract, zero-division guard, session overlap most-recent-wins, TIME_JUMP bar excluded from series (drop non-monotonic update), `RateBar.time` doc corrected, `closes_by_symbol` rename. `[ ]` deferred (dead-code removal sweep — needs a product decision on intended consumers).

---

## 5. HIGH — storage & sync (`app/storage/`)

### H-ST1. ⚡ One failing mirror table poisons unrelated tables — shared retry budget per batch
- **Files:** `app/storage/outbox.py:144-172` (whole batch fails together), `209-240` (attempts bumped for ALL claimed rows).
- **⚡ Verified by simulation:** with a sink failing only `mt5_requests`, healthy `trades` rows went `dead` after 3 flush cycles despite succeeding upserts.
- **Fix:** per-table upsert isolation; only entries of the failed table bump attempts/reschedule. `[x]`

### H-ST2. `StorageLogSink` is never registered with loguru — `app_logs` stays empty in production
- **Files:** `app/storage/service.py:136-139` (starts only the flush thread), `app/storage/log_sink.py` (`__call__` is loguru's entry point, never wired). **Fix:** registered in `StorageService.open()` (`level="WARNING"`), removed in `close()`. `[x]`

### M-ST3. `Database.close_all()` cannot close other threads' connections — silently ⚡
- **File:** `app/storage/db.py:130-151`. **Fix:** workers now close their own thread connection on stop (`close_thread_connection()` in outbox/log-sink/cleanup stop paths); `close_all` no longer clears the registry. `[x]`

### M-ST4. Outbox grows unbounded — synced entries never purged; no index on `updated_at`
- **Files:** `app/storage/repositories.py:194-204, 227-231`, `app/storage/cleanup.py:19-25`. **Fix:** retention now purges `synced` entries older than 7 days; `outbox(updated_at)` index added in migration 4. `[x]`

### M-ST5. Stale mirror overwrites: no version guard on upserts; cloud tables lack `updated_at`
- **Files:** `app/storage/outbox.py:140-172`, `supabase/schema.sql`. `[~]` **Deferred — needs schema decision** (add `updated_at`/`rev` columns + guarded upsert or Postgres trigger; touching the cloud schema affects existing deployments).

### M-ST6. Deletion is never propagated; `delete()` can resurrect deleted rows in the cloud
- **File:** `app/storage/repositories.py:148-149`. **Fix (minimal):** `delete()` now cancels pending outbox entries for the row (prevents resurrection). `[ ]` Full tombstone/propagation design deferred (needs product decision).

### M-ST7. Unclassified mirror errors embed raw exception text into `outbox.last_error`, UI, logs — no `mask_text`
- **Files:** `app/storage/mirror.py:150`, `app/storage/outbox.py:163-165`. **Fix:** fallback messages masked; persisted status keeps a fixed classified message. `[x]`

### M-ST8. `*_json` columns are double-encoded into Supabase `jsonb`
- **Files:** `app/storage/repositories.py:334-341, 362-364, 395-409, 424-430`, `supabase/schema.sql` (`jsonb` columns). **Fix:** mirror payload parses `*_json` string fields back into objects before upsert. `[x]`

### M-ST9. History import deterministic id omits the account — cross-account ticket collision silently drops trades
- **Files:** `app/storage/repositories.py:303-327`, `app/storage/history_import.py:77-105`. `[~]` **Deferred — needs migration** for existing rows (include `login` in `deterministic_id`). Documented; do together with the next schema migration.

### M-ST10. ⚡ UI thread runs `PRAGMA integrity_check` + 5×`COUNT(*)` every 2 s; "Save"/"Remove key" joins the outbox worker up to 5 s on the UI thread
- **Files:** `app/ui/pages/settings.py:307-312, 802-839, 856-875`, `app/storage/service.py:194-228`, `app/storage/db.py:183-186`. **Fix:** stats timer runs only while the page is visible and uses cheap counts; `integrity_check` moved to a daily background job in the cleanup scheduler; `apply_cloud_config` stop-join reduced to a non-blocking stop for the UI path. `[x]`

### L-ST11. Identifier interpolation into SQL safe only by convention; failed `BEGIN` masked by bogus `ROLLBACK`; `flush_once` ignores cooldown; worker-swap race in `apply_cloud_config`; cloud probe reports success with anon key; SPEC↔implementation conflict on anon vs service_role key; masking misses vault entry name (fixed via H-S3); history import: no NaN/inf guard, one bad deal aborts import; sessions mirror only on clean exit; `insert_many` docstring lies (per-row transactions); failed log flush drops batch
- **Files:** `app/storage/repositories.py:96-99, 107-111, 176-192, 437-464`, `app/storage/db.py:114-127, 166`, `app/storage/outbox.py:134-172, 257-268, 122-127`, `app/storage/mirror.py:107-117, 160-171`, `app/storage/history_import.py:40-105`, `app/storage/log_sink.py:115-125`, `docs/SPEC.md:290`. `[x]` for: identifier validation (`_validate_identifier`), BEGIN/ROLLBACK fix, cooldown inside `flush_once`, NaN/inf guard + per-deal try in import, session start mirrored. `[ ]` deferred: anon-key/RLS redesign (product decision), atomic `insert_many` transaction, probe write-verification, dead-letter requeue tooling (L12 below).

### L-ST12. Dead-letter rows are a black hole (no list/requeue tooling)
- **File:** `app/storage/outbox.py:204-221`. `[ ]` deferred (needs CLI/UI decision).

### L-ST13. Clock assumptions: client wall clock ms-truncated; `ORDER BY created_at` without id tiebreaker
- **Files:** `app/storage/repositories.py:60-64, 176-192`. `[x]` id tiebreaker added.

---

## 6. HIGH — UI (`app/ui/`)

### H-U1. ⚡ `PRAGMA integrity_check` on the UI thread every 2 s — see M-ST10 (fixed there). `[x]`

### H-U2. "Check for updates" button permanently bricked after any check that doesn't stage
- **Files:** `app/ui/pages/settings.py:370-418` (`_update_worker` cleared only on staging). **Fix:** cleared in `_on_update_check_done` on both early-return paths. `[x]`

### H-U3. `_clear_layout` leaks sub-layouts → ghost/duplicated rows in the Trend card (verified at runtime)
- **Files:** `app/ui/pages/market.py:58-64, 308-322`. **Fix:** recursive sub-layout + spacer-item deletion. `[x]`

### H-U4. UI thread joins the outbox worker up to 5 s on Save — see M-ST10. `[x]`

### H-U5. Parentless `QThread` workers can abort the app at shutdown ("QThread destroyed while running")
- **Files:** `app/ui/pages/settings.py:375-379, 462-464, 596-600`; `app/ui/workers.py:20`; `app/updater/worker.py:28, 77`; `app/ui/main_window.py:196-199`.
- **Fix:** workers parented to the page; `MainWindow.closeEvent` stops/awaits known workers (connect/update/elevate) before quit; finished workers `deleteLater`. `[x]`

### M-U6. `ElevateWorker.started` shadows `QThread.started`
- **File:** `app/updater/worker.py:87`. **Fix:** renamed to `start_result`. `[x]`

### M-U7. RTL: physical `border-left/right` in QSS don't mirror
- **File:** `app/ui/theme/qss.py:112-115, 149-172, 280-287, 547-552`. `[x]` (RTL-aware border sides generated when the translator direction is RTL).

### M-U8. Logs page: full `setPlainText` every second resets scroll/selection
- **File:** `app/ui/pages/logs.py:115-134`. **Fix:** scroll position preserved (stick-to-bottom when already at bottom); rebuild skipped while page hidden. `[x]`

### M-U9. i18n: raw English enum values interpolated into FA sentences; logs toolbar labels never retranslated
- **Files:** `app/ui/pages/market.py:303, 318`, `app/analysis/trend.py:41-46`, `app/analysis/card.py:114-137`, `app/ui/pages/logs.py:77-80, 169-183`. **Fix:** trend/structure/session labels translated at render time; logs filter labels retranslated. `[x]`

### M-U10. Crash-handler Qt hook overwritten 20 lines later — Qt fatals no longer produce crash reports; `crash.title/body` strings unused
- **Files:** `app/main.py:490-493, 510`, `app/observability/crash_handler.py:94-121, 171-192`. **Fix:** single merged Qt message handler (noise-drop + fatal→crash report); crash dialog wired with translated strings. `[x]`

### M-U11. `translate().format()` errors escape into slots invisibly (PySide6 prints & swallows)
- **File:** `app/ui/i18n/translator.py:49`. **Fix:** format failures fall back to the raw template and log a warning. `[x]`

### M-U12. Chart `autoRange()` on every refresh resets user pan/zoom
- **File:** `app/ui/widgets/chart.py:161-162`. **Fix:** auto-range only on symbol/timeframe change or first fill; manual interaction tracked via `sigRangeChangedManually`. `[x]`

### M-U13. UI-thread `worker.wait()` in the connect-result slot
- **File:** `app/ui/pages/settings.py:602-607`. **Fix:** `finished` → `deleteLater`, no `wait()`. `[x]`

### M-U14. A11y: universal `* { outline: none; }` removes keyboard focus indication
- **File:** `app/ui/theme/qss.py:33-35`. **Fix:** universal rule removed; per-control `:focus` styles. `[x]`

### M-U15. Dead EventBus signals (`theme_changed`, `language_changed`, `market_analysis_changed` never emitted); PySide6 queues plain-function slots to the *sender's* thread (implementation detail worth a comment)
- **File:** `app/core/event_bus.py:19-23, 37`. `[x]` comment added; `[ ]` bus-vs-direct-connection redesign deferred (needs product decision).

### L-U16. Sidebar collapse animations accumulate; finished QThread objects leaked (`_maybe_auto_connect._worker`, `_startup_update_worker`); toast leaveEvent restarts full timer + opacity effect left installed; RTL chevron swap; command palette not modal / `command_executed` never connected / docstring says "fuzzy" but is substring; app display name not updated on language change; status-bar cursor inconsistency; FA typo "پیش‌رویی نیست"; negative `minutes_until` renders "in -5 min"; `_confirm_helper_started` quit-race; watchlist hardcoded in 3 places; empty.settings.desc copy drift
- **Files:** `app/ui/sidebar.py:135, 178, 195-202, 215-226`, `app/main.py:442, 518, 602`, `app/ui/widgets/toast.py:117-121, 137`, `app/ui/command_palette.py:36-45, 101-103`, `app/ui/widgets/status_bar.py:74`, `app/ui/i18n/strings.py:366, 377`, `app/ui/pages/market.py:36, 100, 369-374`, `app/ui/pages/settings.py:446-452`, `app/ui/main_window.py:255-259`. `[x]` for: animation group stop/delete, worker cleanup + deleteLater, toast timer restart short + effect cleanup, RTL chevrons, palette close-on-focus-loss, display name retranslate, cursor consistency, FA typo, `minutes_until` clamp. `[ ]` deferred: watchlist config setting (Phase 6+), palette modal behavior (UX decision), copy drift text.

---

## 7. HIGH — updater & observability (`app/updater/`, `app/observability/`)

### H-UP1. Apply flow verifies the staging tree **after** installing it; failed verification still reports success and deletes staging
- **Files:** `app/updater/apply.py:324-334`, `app/updater/service.py:270-272`.
- **Fix:** staging is verified **wholesale against the new manifest before** `install_tree`; mismatch aborts before touching `app_dir` and keeps staging for retry; post-install mismatch is fatal (no "apply OK"). `[x]`

### H-UP2. Arbitrary file deletion via the removed-list (no containment check) + delta zip can smuggle unmanifested files + version regex only anchors prefix
- **Files:** `app/updater/apply.py:207-221, 238-246`, `app/updater/service.py:258-272`, `app/updater/version.py:14-27`.
- **Fix:** removed-list entries validated (relative, no `..`, contained in `app_dir`); staging files outside the manifest rejected before apply; version string fully matched. `[x]`

### M-UP3. No cryptographic signature — trust anchor is "GitHub repo + TLS"; `__delta__.json` outside the manifest
- **Files:** `app/updater/github.py`, `app/updater/manifest.py`, `installer/app.spec:55`, `docs/SPEC.md:292`. `[ ]` **Deferred — needs signing infrastructure** (minisign/ed25519 pubkey pinned in the exe, or Authenticode). This changes the release process; needs maintainer buy-in.

### M-UP4. No rollback; partial apply leaves a mixed tree that still gets relaunched
- **File:** `app/updater/apply.py:193-204, 346-353`. `[ ]` deferred (`.old`-based restore design; medium-size feature).

### M-UP5. Watchdog runs `notify()`/`restart()` while holding a non-reentrant lock (deadlock risk; head-of-line blocking)
- **File:** `app/observability/watchdog.py:118-142`. **Fix:** snapshot under lock, callbacks outside. `[x]`

### M-UP6. `--password-stdin` echoes the password (no `getpass`)
- **File:** `app/main.py:194-197`. **Fix:** `getpass.getpass()`. `[x]`

### M-UP7. Proxy URL (possibly with credentials) logged; masking gap for URL userinfo covered by H-S1
- **File:** `app/updater/github.py:135-137`. **Fix:** userinfo stripped before logging. `[x]`

### M-UP8. Downloaded release zips never cleaned up (~165 MB each)
- **File:** `app/updater/service.py:148, 252, 283, 364-377`. **Fix:** zip unlinked after successful staging; stale zips swept by `cleanup_stale_artifacts`. `[x]`

### M-UP9. `--update-pid` → `taskkill /F /T` on an unvalidated, reusable PID
- **File:** `app/updater/apply.py:109-148, 317-322`. `[ ]` deferred (needs process-identity design; documented).

### M-UP10. No single-instance guard — two instances can duplicate connections/pollers/update swaps
- **File:** `app/main.py:472-583`. **Fix:** `QLockFile` single-instance guard in `run_gui` (clear message + exit code 2). `[x]`

### M-UP11. Unhandled Qt-slot exceptions may bypass all hooks on PySide6 6.11.2; crash-report path nits (OSError fallback unwrapped, argv unmasked, hooks not chained, no thread-crash rate limit)
- **Files:** `app/observability/crash_handler.py:54-79, 134-137, 156-168`. `[x]` for: safe fallback write, argv masked, previous hooks chained, thread-report rate limit. `[ ]` slot-exception safety net needs a targeted test on 6.11.2 (deferred — behavior changed across 6.x).

### L-UP12. `_retry` treats HTTP status errors as retryable (404 retried 3×, then silent 165 MB full-download fallback); Range resume without `If-Range`; no zip-bomb size cap; `relaunch()` picks alphabetically-first exe; log size-cap thread can die on stat race; `_stage_full` flatten can abort a good download; shutdown-order fragility (audit raise skips log flush); `sweep_old_files` deletes any `*.old`; `Authorization: Bearer` double-redact (fixed via H-S3)
- **Files:** `app/updater/github.py:153-175, 195-208, 225-252`, `app/updater/service.py:258-259, 285-295, 364-377`, `app/updater/apply.py:151-162, 251-258`, `app/observability/logger.py:316, 329-331`, `app/main.py:577-583`. `[x]` for: 404 not retried + fallback logged, zip-bomb cap (400 MB), relaunch prefers the known exe name, size-cap stat race fixed, `_stage_full` flatten tolerant of empty dirs, shutdown audit in try/finally. `[ ]` deferred: `If-Range`/ETag resume (robustness only), `sweep_old_files` scoping doc.

### L-UP13. Calendar: naive timestamps assumed UTC; poller merges (union) so dropped events accumulate forever; `_last_mtime` set before read (torn read never retried); naive `now_utc` raises TypeError; `save()` non-atomic
- **Files:** `app/calendar/events.py:55-65, 157, 185, 209`, `app/calendar/importer.py:49-66`. **Fix:** exporter CSV requires `Z`-suffixed UTC (mismatched rows rejected + warned), poller uses `replace_from_csv` semantics per EA contract decision below, mtime set after successful read, `save()` atomic (temp+rename), naive datetimes normalized. `[x]` (see also CI-H5 for the timezone root cause)

### H-UP14. Calendar events are never persisted — `EventStore.save()` is dead code (every restart loses imported events)
- **Files:** `app/calendar/events.py:137-157` (never called), `app/main.py:392-402`. **Fix:** store saved after every successful poll/merge (atomic). `[x]`

---

## 8. HIGH — CI / release pipeline / MQL5 / installer

### H-CI1. release-please uses `GITHUB_TOKEN` → its tag push will NOT trigger `release.yml` (release assets never built)
- **File:** `.github/workflows/release-please.yml:16-20`.
- **Fix (repo-side):** workflow doc + comments added. **Maintainer action required:** create a PAT (or GitHub App token) with `contents: write` + `pull-requests: write` and store it as the `RELEASE_PLEASE_TOKEN` repository secret; the workflow now uses `token: ${{ secrets.RELEASE_PLEASE_TOKEN || secrets.GITHUB_TOKEN }}`. `[~]` (needs the secret to be created by the maintainer)

### H-CI2. Delta package is effectively never built — `gh release view` returns the *current* release, so the guard always skips
- **File:** `.github/workflows/release.yml:60-70`. **Fix:** previous tag now picked via `gh api repos/.../releases?per_page=30` excluding the current tag. `[x]`

### H-CI3. MQL5: `CalendarValueHistory` returns `bool`, used as an int count → exactly ONE event exported
- **File:** `mql5/CalendarExporter.mq5:87-96`. **Fix:** `bool ok = CalendarValueHistory(...)` + `int total = ArraySize(values)`. `[x]`

### H-CI4. Calendar data path mismatch: EA writes `Common\Files\mt5_workstation_calendar.csv`, app polls `%LOCALAPPDATA%\MT5TradingWorkstation\calendar\exporter.csv`
- **Files:** `mql5/CalendarExporter.mq5:9, 23, 74`, `app/main.py:392-395`. `[~]` **Partially fixed:** the EA now also writes a copy into the terminal's own `MQL5\Files` (non-common) and the docs updated; **full fix needs a decision:** resolve the MT5 Common path from the app (config setting) — deferred to the next phase since it touches Settings UI + installer.

### H-CI5. Timezone: exporter writes broker-server time labeled `Z` (UTC); importer believes it → `risk_window()` off by the broker UTC offset (+2/+3 h typical)
- **Files:** `mql5/CalendarExporter.mq5:47-57`, `app/calendar/events.py:55-65`, `app/analysis/service.py:559-579`.
- **Fix (two-sided):** EA converts to real UTC via `TimeTradeServer() - TimeGMT()` before writing; importer rejects naive timestamps and warns (L-UP13). `[x]`

### H-CI6. No code signing (updater chain unsigned end-to-end; `checksums.txt` generated but unconsumed)
- **Files:** `installer/app.spec:43-57`, `installer/innosetup.iss`, `.github/workflows/release.yml:90-126`, `docs/SPEC.md:292`. `[ ]` **Deferred — needs a certificate purchase + CI secret setup** (maintainer decision; also fix M-UP3 with manifest signing).

### M-CI7. Inno Setup upgrades leave stale PyInstaller files (no `[InstallDelete]`)
- **File:** `installer/innosetup.iss:32-33`. **Fix:** `[InstallDelete]` clears `{app}\_internal` + top-level stale DLLs before install. `[x]`

### M-CI8. PyInstaller exe has no version-info resource / icon; `console=True` ships a permanent console window
- **File:** `installer/app.spec:43-57`, `docs/PROGRESS.md:69-70`. **Fix:** `VSVersionInfo` resource generated from `app/__version__.py` (version, company, product name). `[x]` Icon + windowed-build strategy `[ ]` deferred (needs icon asset + helper-exe decision).

### M-CI9. Actions tag-pinned not SHA-pinned; `choco install innosetup` floats; no concurrency/timeout in workflows; CI doesn't run on non-main branch pushes
- **Files:** `.github/workflows/*.yml`, SPEC H3-1/H3-6. **Fix:** `concurrency` groups + `timeout-minutes` added; CI triggers on all branch pushes; innosetup version pinned. `[x]` SHA-pinning `[ ]` deferred (Dependabot keeps tags fresh; SHA pinning needs a one-time mechanical pass).

### M-CI10. MQL5: truncate-then-write race (header-only file on transient failure) + exclusive share flags + `g_lastWrite` set before success + CSV quoting only wraps `title`
- **File:** `mql5/CalendarExporter.mq5:74-93, 113-114, 131`. **Fix:** query calendar before opening, write to temp + `FileMove`, share flags, `g_lastWrite` on success only, proper CSV quoting. `[x]`

### L-CI11. Non-deterministic release bits (`generated_at`, zip mtimes); no lock file (SPEC D1); uninstall leaves updater debris; tag↔`__version__` not cross-checked before ISCC
- **Files:** `tools/build_manifest.py:35`, `tools/build_delta.py:54-57`, SPEC D1/H5, `installer/innosetup.iss:8-10`. `[ ]` deferred (lock file = maintainer tooling decision; deterministic builds = nice-to-have).

### L-CI12. Version sync consistent ✅ (pyproject 0.7.2 = `__version__.py` = manifest = CHANGELOG; enforced by `tests/test_version.py`)

---

## 9. Intentionally-good patterns (do NOT "fix" these)

- Gateway single-thread discipline (futures + worker-only module access); `stop()` keeps the draining thread reference.
- Secrets: keyring-only storage, masked logins, `ConnectRequest.masked()`, CLI never takes passwords via argv, crash reports mask stacks.
- Transactional outbox + idempotent upserts + client UUIDs; migration ledger; retry taxonomy with backoff and cooldown.
- Analysis: causal smoothers, confirmed-swing activation (no look-ahead), closed-bar event detection, spike check uses pre-existing history only.
- UI: heavy work off the UI thread in hot paths (ConnectWorker/probe/QTimer-poll), correct loop-variable capture, translator fallback chain, pyqtgraph cached `QPicture`.
- Updater: HTTPS-only URLs, Python 3.11 `zipfile` extraction sanitization, staged manifest always overwritten from the remote, staging kept for retry.
- Workflows: no `pull_request_target`, least-privilege permissions, no event-data interpolation into `run:`, Windows-correct shells.

## 10. Deferred items requiring maintainer decisions

1. **M-ST5 / M-ST9** — cloud schema changes (`updated_at` version guard; account-scoped trade ids) — coordinate with existing deployments.
2. **M-UP3 / H-CI6** — code signing + manifest signing (certificate purchase, new release process).
3. **M-UP4** — rollback-on-failed-apply (medium feature).
4. **H-CI4** — MT5 Common-path resolution / config setting for the calendar CSV source.
5. **L-ST12** — dead-letter requeue tooling (CLI/UI).
6. **L-CI11** — dependency lock file (uv/pip-tools).
7. **M-CI9** — SHA-pin all actions (mechanical pass).
8. **H-G2** — confirm `order_check` retcode `0` on a real terminal.
9. **Watchlist as a setting**, command-palette modal UX, anon-key/RLS redesign.
