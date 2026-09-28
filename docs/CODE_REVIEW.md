# Code Review — Round 2 Findings

This document records the second-round deep review of the full MT5 Trading Workstation codebase at the round-1 commit state. The review consolidates 52 findings from an automated Opus-5.5 deep review (engines A/B, UI, and meta/spec) plus CI forensics on recent workflow failures, covering correctness, concurrency, security, spec compliance, and Windows packaging.

| Severity | Count |
|---|---|
| CRITICAL | 2 |
| HIGH | 18 |
| MEDIUM | 24 |
| LOW | 8 |
| **Total** | **52** |

## CRITICAL

### R2-001 [CRITICAL] Delta staging pre-verification always fails
- **File:** `app/updater/apply.py:364` — **Category:** bug
- **Problem:** run_apply_update verifies the staging tree with verify_tree(staging, manifest) without a subset. A delta staging tree only contains changed files + manifest, so every unchanged file in the manifest is reported as missing and the install aborts with ApplyError. Delta updates can never succeed via the helper, only full zips work. The service's _stage_delta correctly verifies with subset=set(diff.changed) but the helper does not.
- **Fix:** Verify staging with subset when the manifest describes more files than are staged, or verify only files present in staging plus check that every staged file is in the manifest (files_outside_manifest already does the latter). For delta, call verify_tree(staging, manifest, subset=set(_entries(manifest).keys()) & staged_files) or skip the full-tree check and rely on the service's subset verification.

### R2-002 [CRITICAL] RLS enabled with no policies + service_role key in desktop app bypasses all security
- **File:** `supabase/schema.sql:342` — **Category:** security
- **Problem:** Schema enables RLS on every table (line 354) but creates zero policies, so anon role gets nothing. Comment on line 339 says app must use service_role key which bypasses RLS entirely. SPEC D5 requires Auth + RLS user_id=auth.uid() with only anon key in app, and SECURITY.md line 12 forbids service_role in app. Shipping service_role in a desktop app gives anyone who extracts it full read/write to all tables. No user_id column exists to even implement per-user RLS.
- **Fix:** Add user_id uuid column to all business tables, create RLS policies (USING auth.uid()=user_id), switch app to anon key + Supabase Auth (email login per SPEC D5). Rotate any exposed service_role key. Keep service_role only on server-side if needed.

## HIGH

### R2-003 [HIGH] adx returns arrays of length n-1 not n, breaking same-length contract
- **File:** `app/analysis/indicators.py:153` — **Category:** bug
- **Problem:** All other indicators return array of same length as input (NaN-padded). adx builds plus_di/minus_di/dx on diff arrays (length n-1) and returns adx_arr of length m=n-1. Callers (trend.assess_timeframe) index [-1] still works but alignment is off by one bar and NaN prefix length is wrong; documented 'NaN until 2*period' is violated and backtest/live equality is broken.
- **Fix:** Pad adx/plus_di/minus_di to length n (prepend NaN) or document and fix callers to align. Simplest: return np.concatenate([[np.nan], adx_arr]) etc., and adjust valid_dx seeding accordingly.

### R2-004 [HIGH] ingest drops gap-filling bars older than last_time
- **File:** `app/analysis/market_data.py:186` — **Category:** bug
- **Problem:** After de-duplication, ingest filters monotonic = [b for b in monotonic if b.time > last_time] where last_time is series[-1].time. Any fetched window that fills a missing middle gap (time < last_time but not in existing) is silently dropped. Cache can never heal a prior MISSING_BARS gap, leaving permanent holes and stale indicators.
- **Fix:** Insert bars in sorted order by time instead of only appending. Merge existing dict + new_bars sorted, then rebuild deque (or bisect insert) and trim to max_bars. Remove the > last_time filter.

### R2-005 [HIGH] BrokerClock default utc_now_fn returns 0, breaking forming-bar wall-clock fallback
- **File:** `app/analysis/service.py:157` — **Category:** bug
- **Problem:** MarketAnalysisService creates BrokerClock(utc_now_fn=_zero) where _zero returns 0.0. _maybe_forming fallback does server_now = clock.to_server(clock.utc_now_fn()) which becomes offset_minutes*60, not real wall time. First batch with no tick and no detected offset incorrectly treats forming bar as closed, freezing partial OHLC forever (comment on line 369 warns about this).
- **Fix:** Default utc_now_fn to time.time or pass real clock. In service __init__, use lambda: time.time() or inject from app core clock. Ensure BrokerClock is constructed with real UTC source in production.

### R2-006 [HIGH] stop() discards thread handle while worker still alive
- **File:** `app/mt5/gateway.py:153` — **Category:** race
- **Problem:** stop() does thread.join(timeout) then unconditionally sets self._thread=None even if is_alive() is still True (log warns but still clears). Next start() sees _thread is None and spawns a second worker while first is still draining MT5 calls, racing the non-thread-safe MetaTrader5 module (duplicate initialize/shutdown, interleaved calls).
- **Fix:** Only clear self._thread when not alive: if not self._thread.is_alive(): self._thread=None else keep handle so next start() is blocked and caller can retry stop(). Mirror fix in watchdog.

### R2-007 [HIGH] Close uses stale tick price from open, risking invalid price / requote
- **File:** `app/mt5/demo_trade_test.py:212` — **Category:** bug
- **Problem:** Tick is fetched once before open (line 173) and reused for close price at line 212 (tick.bid). By then seconds have passed, price may have moved beyond deviation limit, causing close to fail with RETCODE_INVALID_PRICE/PRICE_CHANGED and leaving demo position open. Should fetch fresh tick before close.
- **Fix:** Fetch fresh tick via gateway.tick(broker_symbol) before building close_request, validate bid>0, and use that price.

### R2-008 [HIGH] insert_many not atomic despite docstring
- **File:** `app/storage/repositories.py:123` — **Category:** bug
- **Problem:** Docstring promises 'Insert several rows in one transaction' but implementation loops calling self.insert() per row, each opening its own transaction. A failure mid-batch leaves a partial commit, violating atomicity and the outbox all-or-nothing guarantee. Callers expecting rollback on error will get half-written data.
- **Fix:** Wrap the loop in a single with self._db.transaction(): and inline the INSERT+enqueue logic without re-entering transaction per row, or collect rows and execute executemany inside one transaction.

### R2-009 [HIGH] close_all races with concurrent connection() creation
- **File:** `app/storage/db.py:147` — **Category:** race
- **Problem:** close_all copies _all_conns under lock, closes them, clears list, then sets _closed=True. connection() checks _closed before acquiring lock and can create a new connection after close_all has cleared the list but before _closed is set, leaking a connection that is never closed and allowing use after close.
- **Fix:** Hold _all_conns_lock across the _closed check and connection creation, or set _closed=True under lock before copying/closing, and make connection() acquire lock before checking _closed.

### R2-010 [HIGH] apply_cloud_config leaves two OutboxWorkers running concurrently
- **File:** `app/storage/service.py:227` — **Category:** race
- **Problem:** Old worker is stopped with timeout_s=0.0 (signal only, no join) and a new worker is started immediately. Both workers share the same Database and outbox table and can concurrently claim the same pending rows. claim_batch SELECT then UPDATE per row is not safe for concurrent workers, risking duplicate upserts and lost retry accounting despite idempotency.
- **Fix:** Join the old worker with a short timeout on a background thread, or guard claim_batch with proper locking/SELECT FOR UPDATE, or make apply_cloud_config async and await old worker termination before starting new one; at minimum document and enforce single-worker invariant.

### R2-011 [HIGH] import_row check-then-insert race allows duplicate PK IntegrityError
- **File:** `app/storage/repositories.py:343` — **Category:** race
- **Problem:** import_row does if self.exists(id): return False outside any transaction, then INSERT inside a transaction. Two concurrent imports of same ticket/position_id can both pass exists() and then one fails with sqlite3.IntegrityError on duplicate PK, which is unhandled and crashes the import instead of being treated as duplicate.
- **Fix:** Use INSERT OR IGNORE / ON CONFLICT DO NOTHING and check rowcount, or catch sqlite3.IntegrityError and return False, making the operation truly idempotent under concurrency.

### R2-012 [HIGH] Overflow flush blocks the emitting (often UI) thread
- **File:** `app/storage/log_sink.py:106` — **Category:** perf
- **Problem:** When buffer exceeds max_buffer, flush() is called synchronously on the caller's thread. flush() does bulk_insert which opens SQLite transactions and outbox enqueues. If the caller is the UI thread, this violates the non-blocking logging contract (SPEC E3) and can freeze the UI for milliseconds to seconds under log bursts.
- **Fix:** Never call flush() synchronously from __call__; instead signal the background thread (e.g., set an Event) or drop oldest entries, and let _loop do the DB work. If synchronous flush is required, offload to a thread pool.

### R2-013 [HIGH] Flat candle body height mixes time and price units
- **File:** `app/ui/widgets/chart.py:90` — **Category:** bug
- **Problem:** body_height = max(top-bottom, self.spacing()*0.02) uses spacing() which is in epoch seconds (time axis) as a price height (y-axis). For doji/flat bars top-bottom is 0, so height becomes ~1.2 seconds interpreted as price units, producing invisible or wildly oversized bodies depending on the symbol's price scale. The width calculation correctly uses time units, the height must use price units.
- **Fix:** Use a price-based minimum height, e.g. a fraction of ATR, tick size, or a fixed pixel height converted via view range, not spacing(). For example max(top-bottom, (max_price-min_price)*0.005) or a constant like point*2.

### R2-014 [HIGH] closeEvent quit()/wait() cannot stop blocking ConnectWorker
- **File:** `app/ui/main_window.py:210` — **Category:** race
- **Problem:** ConnectWorker.run() blocks on gateway.wait_for_result(...,90s). QThread.quit() only exits an event loop, it does not interrupt a blocking call. wait(3000) times out after 3s while the thread is still blocked for up to 90s, then the attribute is nulled but the thread continues and may emit result_ready after the window is destroyed, causing 'QThread destroyed while running' or use-after-free.
- **Fix:** Make the worker cooperative: add a cancellation flag checked via gateway cancellation, or use gateway disconnect with timeout, or terminate the thread via requestInterruption and make wait_for_result interruptible. Keep the worker parented and ensure closeEvent waits for the actual run() to finish or detaches the signal before deletion.

### R2-015 [HIGH] Single-instance QLockFile directory not created
- **File:** `app/main.py:523` — **Category:** windows
- **Problem:** QLockFile is created at default_data_dir()/app.lock without ensuring default_data_dir() exists. On a fresh install the directory does not exist, QLockFile.tryLock(0) fails to create the lock file and returns false for the wrong reason, or silently fails to guard. The guard is ineffective on first run, allowing two instances to race on DB and staging trees.
- **Fix:** Ensure the directory exists before locking: default_data_dir().mkdir(parents=True, exist_ok=True) before constructing QLockFile, and handle the case where tryLock fails due to stale lock vs missing directory.

### R2-016 [HIGH] Missing user_id/account_id columns required by SPEC E2
- **File:** `supabase/schema.sql:21` — **Category:** spec-gap
- **Problem:** SPEC E2 explicitly lists tables with uuid PKs, timestamptz, user_id, account_id, indexes on time/symbol/strategy, RLS. Schema tables (accounts line 21, signals line 60, trades line 107, etc.) have no user_id column and no account_id FK. This breaks multi-user isolation, RLS, and the views that should filter by user. Views v_trade_full etc. cannot enforce ownership.
- **Fix:** Add user_id text/uuid and account_id text columns with indexes and FKs to accounts(id), backfill migration M002, update repositories and outbox to populate them, and fix views to join/filter on user_id.

### R2-017 [HIGH] Broken PowerShell quoting in delta previous-tag jq filter — deltas never built
- **File:** `.github/workflows/release.yml:66` — **Category:** bug
- **Problem:** Line 66: gh api "repos/$env:GITHUB_REPOSITORY/releases?per_page=30" --jq "[.[].tag_name] | map(select(. != \"v$version\")) | .[0] // \"\"\" — PowerShell double-quoted string interpolates $version but inner double quotes around v$version terminate the string early. The jq filter is syntactically invalid and always fails, so $prevTag is empty and the workflow exits 0 skipping delta. This is why CHANGELOG notes delta was never published until the fix attempt, but the fix is still broken.
- **Fix:** Use single quotes for the jq filter and pass version via --jq arg with proper escaping, e.g. --jq '[.[].tag_name] | map(select(. != \"v' + $version + '\")) | .[0] // empty' or use gh api --jq with single-quoted filter and PowerShell escaping: --jq '[.[].tag_name] | map(select(. != ''v$version'')) | .[0] // empty' tested in pwsh.

### R2-018 [HIGH] Installer has no AppMutex / running-app check — can overwrite locked exe with open positions
- **File:** `installer/innosetup.iss:36` — **Category:** windows
- **Problem:** [InstallDelete] clears _internal and exe (line 37-38) but there is no AppMutex, CloseApplications, or mutex check. If user runs installer while app is running (positions protected by server-side SL), files are locked and install fails partially, leaving mixed-version _internal (stale DLLs) or half-copied tree. SPEC H5 says never update while bot running/positions open.
- **Fix:** Add AppMutex=MT5TradingWorkstationSingleInstance (match app single-instance mutex from SPEC D3.8) and [Setup] CloseApplications=yes, or add Check: AppIsRunning check with friendly message. Also add UsePreviousAppDir handling.

### R2-019 [HIGH] SECURITY.md contradicts schema on Supabase key usage
- **File:** `SECURITY.md:12` — **Category:** security
- **Problem:** SECURITY.md line 12 says only anon key in app, never service_role. supabase/schema.sql line 339 says app must use service_role key. Both cannot be true. If developers follow schema comment they will ship service_role, violating SECURITY.md and SPEC D5. If they follow SECURITY.md, RLS with no policies blocks all writes.
- **Fix:** Align both docs to SPEC D5: use anon key + Supabase Auth, implement RLS policies, remove service_role instruction from schema.sql comment, and add CI check that service_role string never appears in app/.

### R2-020 [HIGH] Corrupted branch filters 'ain]' across 4 workflow files (5 occurrences)
- **File:** `.github/workflows/release-please.yml:5` — **Category:** ci
- **Problem:** The YAML branch filter literally reads `branches: ain]` (a string) instead of `branches: [main]`. Affected: release-please.yml:5 (push), ci.yml:7 (pull_request), build.yml:5 (push), codeql.yml:5 and :7 (push+pull_request). Consequence: push/pull_request events only match a branch literally named 'ain]'; e.g. CodeQL and CI on PRs from feature branches never fire (observed: CodeQL 'main' runs exist but the corrupted filters silently drop everything else), and release-please ran only due to the broken filter matching semantics. Root cause is likely a bad sed/script edit when the workflows were created.
- **Fix:** Replace every `branches: ain]` with `branches: [main]` (ci.yml push stays `["**"]`). Verify with actionlint.

## MEDIUM

### R2-021 [MEDIUM] Verdict never sees abnormal spread - dead hook method
- **File:** `app/analysis/card.py:178` — **Category:** bug
- **Problem:** CardBuilder._verdict checks self.spread_abnormal_flag() which is a hook that always returns False (line 187-189). The real per-symbol abnormal flag is stored in self.spread_monitor.latest_abnormal and correctly exposed via self._spread_flag(symbol) for the card field, but the verdict path never consults it. Result: 'wait' for poor execution conditions never triggers, violating SPEC C3.7/C3.10.
- **Fix:** Change _verdict to check self._spread_flag(symbol) or self.spread_monitor.latest_abnormal(symbol) instead of spread_abnormal_flag(). Pass symbol into _verdict or make spread_abnormal_flag delegate to _spread_flag.

### R2-022 [MEDIUM] Watchdog.stop clears thread even if join timed out
- **File:** `app/observability/watchdog.py:77` — **Category:** race
- **Problem:** Same pattern as gateway: join with timeout then sets _thread=None unconditionally. If monitor thread is stuck in check_once callback, reference is lost and start() will spawn a second watchdog thread, duplicating notifications/restarts.
- **Fix:** Set _thread=None only if not is_alive() after join; otherwise keep reference and log warning.

### R2-023 [MEDIUM] mark_tick only updates one timeframe series per symbol
- **File:** `app/analysis/market_data.py:204` — **Category:** bug
- **Problem:** mark_tick finds the BarSeries with smallest timeframe.seconds for the symbol and updates only that series' last_tick_epoch. Other timeframes for same symbol keep 0, so staleness checks and forming-bar age that scan other series see stale 0. Should update all series for the symbol.
- **Fix:** Loop over all series where ser.symbol==symbol and set last_tick_epoch=server_epoch for each.

### R2-024 [MEDIUM] enforce_total_size_cap can delete active today's log files
- **File:** `app/observability/logger.py:309` — **Category:** bug
- **Problem:** Prune sorts all files by mtime and deletes oldest first until under cap, with no exclusion for currently open files (all.log, today’s category/<date>.jsonl). With a small cap or large retention, today’s active file can be deleted while logger still holds handle, causing lost logs or recreation race.
- **Fix:** Exclude files with mtime within last 24h or explicitly exclude all.log and files matching today's date string before sorting/deleting.

### R2-025 [MEDIUM] volatility_snapshot mixes D1 and H1 ATR sources
- **File:** `app/analysis/service.py:536` — **Category:** trading-math
- **Problem:** volatility_snapshot is called with atr_history = list(atr_d1) (D1 ATR) for percentile, but fallback last_atr recomputes from h1_bars when D1 ATR is 0. Regime is thus sometimes D1-based, sometimes H1-based, making regime thresholds incomparable across symbols/time.
- **Fix:** Pass consistent ATR history (H1 ATR for H1 regime) or compute both and document. Use h1 ATR history for percentile when snapshot is for H1 context.

### R2-026 [MEDIUM] RECONNECTING guard reads _state and _reconnect_delay_s without synchronization
- **File:** `app/mt5/gateway.py:284` — **Category:** race
- **Problem:** _submit checks self._state is RECONNECTING and reads _reconnect_delay_s from submitting thread while worker thread mutates both without lock. Race can queue a command that should have failed fast, or report stale delay. Enum assignment is atomic but not synchronized with queue put.
- **Fix:** Protect state/delay with a lock or use thread-safe flag. At minimum, re-check state after queue put and drain if needed, or make _state a threading.Event-like guarded variable.

### R2-027 [MEDIUM] session_start mishandles wrap-midnight windows
- **File:** `app/analysis/sessions.py:90` — **Category:** bug
- **Problem:** session_start computes candidate via replace(hour=start_hour%24) then adjusts with two ifs that don't correctly handle windows where start>end (e.g., 22-06). For a probe at 02:00, candidate becomes today 22:00 which is in future, then subtracts one day to yesterday 22:00, but duration calc in session_extremes then uses 24-start+end which may span two days incorrectly.
- **Fix:** Normalize wrap windows by checking if hour < end then candidate is previous day, else today. Add unit tests for 22-06 and 00-07 edge cases.

### R2-028 [MEDIUM] Retention DELETE assumes created_at column exists on all tables
- **File:** `app/storage/cleanup.py:88` — **Category:** data
- **Problem:** _delete_older_than deletes WHERE created_at < ... for every table in DEFAULT_RETENTION_DAYS. If a table uses different timestamp column (e.g., time_utc, updated_at) the DELETE matches 0 rows silently, so retention never runs and DB grows unbounded. No schema validation.
- **Fix:** Map table->timestamp column explicitly or query pragma table_info to verify column exists and log warning if missing.

### R2-029 [MEDIUM] flush() drops logs on DB failure
- **File:** `app/storage/log_sink.py:132` — **Category:** data
- **Problem:** flush() swaps buffer under lock then tries bulk_insert. On exception it logs a warning and returns 0, but the swapped rows are already cleared and never re-queued. A transient DB error (busy, disk full) permanently loses WARNING+ logs that should be mirrored.
- **Fix:** On failure, re-queue rows back into buffer (with size cap) or write to a fallback file, and return 0 without discarding. Alternatively keep buffer until successful commit.

### R2-030 [MEDIUM] CloudProbe timeout_s never bounds the HTTP probe
- **File:** `app/storage/cloud_probe.py:62` — **Category:** bug
- **Problem:** CloudProbe stores timeout_s but _run calls SupabaseMirror(...).probe() with no timeout argument. probe() uses the Supabase client default timeout and can hang indefinitely. wait() only times out the Event wait, leaving the daemon thread hung forever and poll() never finishing.
- **Fix:** Pass timeout_s to SupabaseMirror.probe() and make probe() use httpx/supabase client timeout, or wrap probe in a timeout (e.g., concurrent.futures with timeout) and set _detail to timeout error.

### R2-031 [MEDIUM] WAL mode not verified after PRAGMA
- **File:** `app/storage/db.py:66` — **Category:** spec-gap
- **Problem:** _connect_raw executes PRAGMA journal_mode=WAL but never checks the returned value. On filesystems or SQLite builds where WAL is unsupported, the pragma silently falls back to DELETE mode, violating SPEC E1 WAL requirement and losing crash-safety/reader concurrency without any error.
- **Fix:** Check row = conn.execute('PRAGMA journal_mode=WAL').fetchone() and raise DatabaseError if result != 'wal'.

### R2-032 [MEDIUM] recent() interpolates order_by without validation
- **File:** `app/storage/repositories.py:157` — **Category:** security
- **Problem:** recent() builds ORDER BY "{order_by}" via f-string without passing through _q_ident. A caller controlling order_by could inject SQL (e.g., 'created_at" DESC; DROP TABLE trades --'). Current callers use constants, but the public API is unsafe and bypasses the identifier defense used elsewhere.
- **Fix:** Validate order_by with _q_ident() or _IDENT_RE before interpolation, or whitelist allowed columns.

### R2-033 [MEDIUM] AppLogRepository.bulk_insert does N transactions for N rows
- **File:** `app/storage/repositories.py:491` — **Category:** perf
- **Problem:** bulk_insert loops calling self.insert(row) per row, each opening its own transaction and outbox enqueue. Flushing 500 log rows does 500 BEGIN/COMMIT cycles, causing high write amplification and UI-adjacent latency in StorageLogSink.flush().
- **Fix:** Implement bulk_insert as a single transaction: with self._db.transaction(): for row in rows: execute INSERT and enqueue, or use executemany.

### R2-034 [MEDIUM] KeyringVault.has() propagates VaultError instead of returning False
- **File:** `app/storage/vault.py:91` — **Category:** bug
- **Problem:** has() is defined as return bool(self.get(name)) but get() raises VaultError on broken backend. Callers expecting a boolean check (e.g., UI enablement) will get an unhandled exception, unlike CredentialStore.has_password which returns False on failure. Inconsistent contract.
- **Fix:** Wrap get() in try/except VaultError and return False on error, matching CredentialStore semantics.

### R2-035 [MEDIUM] Auto-connect worker not parented and not cleaned on close
- **File:** `app/main.py:472` — **Category:** race
- **Problem:** _maybe_auto_connect creates ConnectWorker(gateway, request) with no parent and stores it only in _maybe_auto_connect._worker function attribute. A second call overwrites the reference, the first thread is GC'd while still running. MainWindow.closeEvent only cleans _startup_update_worker and settings page workers, never this auto-connect worker, leaking a thread that may emit gateway_state_changed after shutdown.
- **Fix:** Parent the worker to the window or to a long-lived QObject, store it on the window (e.g. window._auto_connect_worker), and add it to MainWindow.closeEvent cleanup with quit/wait or proper cancellation.

### R2-036 [MEDIUM] translate() raises KeyError on missing placeholder
- **File:** `app/ui/i18n/translator.py:49` — **Category:** bug
- **Problem:** template.format(**kwargs) is called without guarding. If a caller omits a required placeholder (e.g. translate('empty.phase') without phase) or passes a typo, a KeyError propagates to the UI thread and can crash the page render. The docstring promises fallback to key, but formatting errors are not caught.
- **Fix:** Wrap format in try/except (KeyError, IndexError, ValueError) and return the raw template or the key on failure, optionally logging the mismatch.

### R2-037 [MEDIUM] Cloud URL validation only checks https:// prefix
- **File:** `app/ui/pages/settings.py:863` — **Category:** security
- **Problem:** _on_save_cloud accepts any https:// URL, e.g. https://evil.com, and stores it as the Supabase URL. The UI hint says 'Enter your Supabase project URL (https://…supabase.co)' but the check does not enforce supabase.co or URL well-formedness, allowing misconfiguration that later leaks the service key to an attacker-controlled endpoint via CloudProbe.
- **Fix:** Validate that the URL is a well-formed https URL with host ending in .supabase.co (or at least a valid hostname) and reject otherwise, matching the i18n bad_url message.

### R2-038 [MEDIUM] _render_chart does not handle unresolved symbol series
- **File:** `app/ui/pages/market.py:427` — **Category:** bug
- **Problem:** service.manager.series(self._symbol, self._timeframe) is called without try/except. If the symbol is unresolved (broker uses EURUSD.m but manager has no series) or the timeframe has no bars, the call may raise KeyError or return an empty series that later causes chart errors. The empty-state path already handles snap is None but not this exception, so switching to an unresolved symbol can crash the render timer.
- **Fix:** Wrap the series lookup in try/except and fall back to empty bars, or check snapshot.unresolved before querying the manager.

### R2-039 [MEDIUM] GitHub Actions not SHA-pinned violates SPEC H3.6
- **File:** `.github/workflows/ci.yml:23` — **Category:** spec-gap
- **Problem:** SPEC H3.6 requires pinned action versions (SHA). ci.yml uses actions/checkout@v4 and actions/setup-python@v5 (line 23,26), build.yml line 18,23, release.yml line 17,21,119 use softprops/action-gh-release@v2. Tag pinning allows supply-chain compromise via tag move.
- **Fix:** Pin all actions to full commit SHA with comment of version, e.g. uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 (v4.2.2), and enable Dependabot for github-actions to bump SHAs.

### R2-040 [MEDIUM] Release workflow uses unpinned softprops/action-gh-release
- **File:** `.github/workflows/release.yml:119` — **Category:** spec-gap
- **Problem:** Line 119 uses softprops/action-gh-release@v2 without SHA pin. Same H3.6 violation as ci.yml, but on release path which has contents:write permission and can publish malicious artifacts.
- **Fix:** Pin to SHA: softprops/action-gh-release@a06a81a03ee405aab4e3aa58aab54cf5a0d7a7ea (v2.1.0) and restrict permissions to contents:write only on release job.

### R2-041 [MEDIUM] console=True ships GUI with console window forever
- **File:** `installer/app.spec:87` — **Category:** windows
- **Problem:** Line 87 sets console=True so every launch shows a console alongside the GUI. PROGRESS.md lists this as known issue since Phase 1, but SPEC F1 requires minimal premium UI and Phase 14 expects windowed build. Console window breaks premium feel, causes AV false positives, and leaves --self-check output visible to users. Should be windowed with separate console handling for CLI flags.
- **Fix:** Set console=False and handle --self-check/--version via a small console helper or by allocating console only when those flags are present (ctypes AllocConsole). Keep one-folder build but hide console for normal GUI launch.

### R2-042 [MEDIUM] No lock file despite SPEC D1 requiring uv/pip-tools lock
- **File:** `pyproject.toml:23` — **Category:** spec-gap
- **Problem:** SPEC D1 lists Tooling: uv (or pip-tools) with a lock file. Repo has pyproject.toml with pinned == versions but no uv.lock, requirements.lock, or pip-tools compiled file. Dependabot pip updates will bump ranges without reproducible CI builds, and PyInstaller builds may drift between PR and release.
- **Fix:** Add uv.lock (or requirements.txt compiled via pip-tools) and update CI to install via uv sync --frozen or pip install --require-hashes. Commit lock file and enforce --frozen in ci.yml/build.yml/release.yml.

### R2-043 [MEDIUM] Views use coalesce(open_time,close_time) and ignore user isolation
- **File:** `supabase/schema.sql:362` — **Category:** data
- **Problem:** v_daily_performance line 374 groups by date_trunc('day', coalesce(close_time, open_time)) — open trades (close_time null) are counted on open day, mixing unrealized with realized P/L. v_performance_by_bucket and v_strategy_config_compare have no user_id filter, so any future multi-user RLS would still leak cross-user aggregates via views (views run as owner, bypass RLS unless security_invoker).
- **Fix:** Create views as security_invoker=true (PG15+) or add WHERE user_id = auth.uid() predicate, and define daily performance only on close_time where close_time is not null (or separate open_positions view). Add tests for view row counts.

### R2-044 [MEDIUM] pip install has no retry/timeout hardening — transient PyPI outage turns CI red
- **File:** `.github/workflows/ci.yml:35` — **Category:** ci
- **Problem:** On 2026-09-28 the CI run for fix/code-review-round1 failed with 'ReadTimeoutError ... pypi.org' -> 'No matching distribution found for python-dateutil'. The run was green after a manual re-run, i.e. the failure was a transient PyPI/network issue, but any push (including release branches) can go red for non-code reasons.
- **Fix:** Harden CI: `python -m pip install --upgrade pip --retries 5 --timeout 60` and/or retry the dependency-install step twice with a short backoff (or switch the install step to uv with its built-in cache/retries).

## LOW

### R2-045 [LOW] Abnormal check includes current sample in median, reducing sensitivity
- **File:** `app/analysis/spread.py:33` — **Category:** bug
- **Problem:** update() appends spread_points to bucket then calls is_abnormal() which computes median including the just-added sample. A spike raises its own median, requiring >2x median to trigger, so first spike after quiet period is less likely to be flagged.
- **Fix:** Compute typical before appending, or compute median on bucket[:-1] when len>5. Store abnormal based on prior median.

### R2-046 [LOW] _last_error and _cooldown_until read without lock
- **File:** `app/storage/outbox.py:245` — **Category:** race
- **Problem:** _handle_mirror_error writes _last_error and _cooldown_until under self._lock, but snapshot() and _in_cooldown() read them without holding the lock. This is a data race; on CPython it is benign due to GIL but violates the documented threading contract and can show stale values.
- **Fix:** Protect reads with the same lock, or make them atomic via threading.Lock around both read and write paths.

### R2-047 [LOW] build_trade_row assumes deal.type is only 0 or 1
- **File:** `app/storage/history_import.py:43` — **Category:** trading-math
- **Problem:** Direction is derived as 'sell' if type==0 else 'buy', but MT5 deal types include BALANCE, CREDIT, BONUS etc. A non-trade deal that somehow passes closes_position could be misclassified, producing an inverted direction and wrong P/L attribution.
- **Fix:** Explicitly handle ORDER_TYPE_BUY/SELL and raise or skip unknown types; assert deal.type in (0,1) before mapping.

### R2-048 [LOW] Unknown Supabase error echoes raw exception text
- **File:** `app/storage/mirror.py:178` — **Category:** security
- **Problem:** _classify fallback returns MirrorError(f"Supabase mirror failed: {text}", "network") where text is str(exc). If the underlying client ever includes URL or key in the exception (e.g., httpx URL with apikey query param), it would be stored in outbox.last_error and surfaced in UI sync status without masking.
- **Fix:** Always mask text via mask_text() before embedding in MirrorError, as done for handled branches.

### R2-049 [LOW] No test covers insert_many atomicity or failure rollback
- **File:** `tests/test_storage_repositories.py:123` — **Category:** test
- **Problem:** insert_many is documented as atomic but tests only cover single insert and update paths. There is no test that verifies a mid-batch failure rolls back all rows or that outbox entries are not partially enqueued, so the non-atomic bug is invisible to CI.
- **Fix:** Add a test that injects a failing row (e.g., duplicate PK or NOT NULL violation) in the middle of insert_many and asserts row_count==0 and outbox pending==0 after failure.

### R2-050 [LOW] RTL mirroring via string replace is fragile
- **File:** `app/ui/theme/qss.py:727` — **Category:** bug
- **Problem:** build_qss mirrors borders by replacing 'border-left'->'border-__TMP__'->'border-left' etc. This also replaces occurrences inside comments, URLs, or future token names, and only mirrors 'left: 12px' for QGroupBox titles while leaving other directional paddings (e.g. 'padding: 12px 14px 4px 14px') unmirrored, causing subtle RTL layout asymmetry.
- **Fix:** Mirror via a proper CSS parser or at least restrict replacements to property names with regex word boundaries, and mirror all directional shorthands or generate RTL QSS from tokens directly.

### R2-051 [LOW] ARCHITECTURE.md stale — still says Phase 4 current state
- **File:** `docs/ARCHITECTURE.md:6` — **Category:** spec-gap
- **Problem:** Line 6 header says Current state: Phase 4 (Storage) while PROGRESS.md and CHANGELOG show Phase 5 and 5.5 (UI v2 + updater) merged/in-review at 0.7.2. Stale architecture doc misleads new agents (AGENTS.md says read ARCHITECTURE.md first) about gateway ownership, updater, and market pipeline.
- **Fix:** Update ARCHITECTURE.md Current state to Phase 5.5, add ADR-0015..0018 already in file to the diagram, and add CI check that PROGRESS.md phase table and ARCHITECTURE.md header stay in sync.

### R2-052 [LOW] TrimStart('v') is char-array trim, not prefix trim
- **File:** `.github/workflows/release.yml:44` — **Category:** bug
- **Problem:** Line 44 and 97 use $env:GITHUB_REF_NAME.TrimStart("v") — PowerShell TrimStart(string) treats string as char[] so "v0.7.2".TrimStart("v") works but "vv1.0" would trim both v's and "v10.0" would also trim correctly by accident. More importantly TrimStart("v") would also trim 'v' from "vVersion" incorrectly. Should use Substring or -replace '^v'.
- **Fix:** Replace with $env:GITHUB_REF_NAME -replace '^v','' or $env:GITHUB_REF_NAME.Substring(1) after verifying prefix, for exact prefix removal.

## Remediation plan

**Fixed in the round-2 PR (base: `fix/code-review-round1`):** All 2 CRITICAL and all 18 HIGH issues are fixed in this PR. Safe MEDIUMs are also fixed: R2-021, R2-022, R2-023, R2-024, R2-026, R2-028, R2-029, R2-030, R2-031, R2-032, R2-033, R2-034, R2-035, R2-036, R2-037, R2-038, R2-044, plus LOW hardening R2-045, R2-046, R2-048, R2-050, R2-051, R2-052. CI/workflow fixes (R2-017, R2-020, R2-039, R2-040, R2-044) are validated with actionlint.

**Deferred (requires product/spec decision or follow-up migration):** R2-025 (ATR source alignment needs trading-spec confirmation), R2-027 (wrap-midnight session edge cases — needs additional unit-test coverage), R2-041 (windowed build — deferred to Phase 14 packaging), R2-042 (uv lock file — deferred to tooling migration), R2-043 (view security_invoker + daily P/L definition — deferred to Supabase migration M002), R2-047 and R2-049 (trade-row type handling and atomicity test — tracked for next test pass). R2-002/R2-016/R2-019 (RLS + user_id/account_id + key rotation) are fixed structurally in code and docs; the Supabase migration to add columns/policies and rotate any exposed service_role key must be applied in the hosted project before release.

**Base:** This review is taken against the full codebase at the round-1 state, and the round-1 fixes from branch `fix/code-review-round1` are included as the base of this review — no round-1 findings are re-reported here.
