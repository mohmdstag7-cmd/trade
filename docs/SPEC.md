# MT5 Trading Workstation — Specification (v7)

> This file is the **source of truth** for the project (SPEC A1). It is assembled from the maintainer's original prompt. Re-read the relevant sections at the start of every phase.

# Prompt: MT5 Trading Workstation (Windows) — v7 (full review + GitHub workflow + real MT5 on local PC)

ROLE

You are a professional product team working as one:

- Head of Trading / Quant (15+ years FX, gold, indices; risk management; systematic strategy validation)
- Principal Software Architect (Python, Windows desktop, MetaTrader 5 integration, reliable real-time systems)
- ML Engineer (financial time-series, calibration, leakage-free validation)
- Product Designer (minimal, premium desktop UI/UX)
- QA / Reliability Engineer (testing, failure modes, observability)

Build "MT5 Trading Workstation": a production-quality Windows desktop app that connects to the user's MetaTrader 5 account, analyzes markets, estimates the win probability of trade setups, executes trades under strict risk control, logs everything, and stores the history in Supabase for AI-driven analysis and improvement.

## PART A — HOW YOU MUST WORK

1. Save this specification as docs/SPEC.md in the repo in phase 1. Re-read the relevant sections at the start of every phase. The spec is the source of truth.
2. Work in phases (Part G). After each phase: (a) a summary of what was built, (b) exact commands to run and test it, (c) the phase acceptance checklist with ✓/✗, then STOP and wait until I write next phase.
3. Complete, runnable code only — no placeholders, no pass, no TODO, no pseudo-code, no "similar to above". If a file is too long for one reply, split it across messages and say so.
4. I am not a professional developer: give exact install/run commands and explain manual steps (MT5, Supabase, Telegram) in simple numbered steps.
5. Maintain and update at the end of every phase:
   - docs/ARCHITECTURE.md — modules, data flow, key decisions (ADR style: decision + reason).
   - docs/PROGRESS.md — completed phases, the current phase, known issues, next steps, so work can continue in a new chat by saying: "Read docs/SPEC.md and docs/PROGRESS.md and continue."
   - CHANGELOG.md.
6. If any requirement is technically wrong, unsafe, or impossible with the MT5 Python API, say so and propose a better alternative before implementing. Never silently skip a requirement.
7. Prefer simple, boring, reliable solutions. Trading software must be predictable. No premature optimization, no unnecessary frameworks.
8. Never claim something works unless there is a test or a clear manual verification step for it.

## PART B — PRODUCT

### B1. Goals

1. Connect to the user's own MT5 account (any broker, demo or real) through the local MT5 terminal.
2. Analyze markets (multi-timeframe trend, structure, key levels, volatility, sessions, news) and the account (performance, behavior, risk).
3. Generate strategy signals with a calibrated win probability, expected value (EV), and a plain-language explanation.
4. Execute trades (Paper / Semi-auto / Auto) under non-bypassable risk rules.
5. Log everything so every decision and problem can be reconstructed; store the history in Supabase.
6. Close the loop: export → AI analysis → versioned config suggestion → backtest → paper → live.
7. A minimal, premium, professional UI/UX.

### B2. Non-goals / honesty rules

- The app does not guarantee profit. The win probability is a statistical estimate and must always be labeled that way, with its sample size and uncertainty.
- No martingale, grid, averaging down, or risk increase after losses — ever.
- AI (LLM) output is advisory only; it never places trades or changes live configs directly.
- MT4 is not supported (show a clear message).

### B3. Operating modes (always visible in the status bar)

| Mode | Orders | Purpose |
|------|--------|---------|
| Analysis-only | none | analysis, journaling; automatic with the investor (read-only) password |
| Paper | simulated on live prices | testing & data collection — default on first launch |
| Semi-auto | real, after the user's approval | recommended first live mode |
| Auto | real, automatic | only after passing the Go-Live gate (C9) |

### B4. User defaults (all editable; also saved as profiles)

- Symbols: EURUSD, GBPUSD, XAUUSD (auto-map broker suffixes such as EURUSD.m, XAUUSDm)
- Entry timeframe M15; context timeframes H1, H4, D1
- Risk per trade 0.5% · max total open risk 1.5% · max daily loss 2% · max total drawdown 8% · max open trades 3 · max trades per day 6
- Min win probability 60% AND min EV +0.10R
- Margin level floor 300%
- Built-in profiles: Conservative (0.25%), Normal (0.5%), Prop-firm (prop daily-loss / max-DD rules, trailing DD option)
- UI language English + Persian (RTL, Vazirmatn font)

## PART C — FUNCTIONAL SPECIFICATION

### C1. MT5 account connection

1. Explain in the onboarding and README: the official MetaTrader5 Python package talks to the MT5 terminal installed on the same Windows PC; it does not connect to the broker directly. For 24/7 running use a Windows VPS near the broker server.
2. Inputs: login, password, server, terminal path (auto-detect from common folders/registry + Browse). Credentials only in Windows Credential Manager (keyring).
3. Test connection checklist with ✓/✗ and a plain-language fix for each item: terminal found → terminal running/launched → login OK (map last_error() to friendly messages: wrong password, wrong server, no connection…) → account info loaded → Algo Trading enabled (terminal_info().trade_allowed) → account trading allowed (account_info().trade_allowed, trade_expert) → symbols available → live quotes → history available.
4. Detect and show: broker, server, login, name, DEMO / REAL / CONTEST badge, currency, leverage, hedging vs netting (adapt position logic for netting), stop-out level and mode, terminal build.
5. Investor password → automatically Analysis-only mode.
6. Account profiles: several accounts saved; switching safely stops the engine. Running several accounts at the same time = one portable MT5 terminal + one app instance per account (--profile NAME); document this.
7. History: on first connect import the full deal/order history (including past manual trades) for analytics. Check the MT5 "Max bars in chart" setting and warn if history is too short for training/backtesting.
8. Heartbeat (terminal connected + fresh ticks); auto-reconnect with exponential backoff; relaunch the terminal if closed; urgent alert if disconnected while positions are open.

### C2. Symbols & market data

1. Symbol mapping and symbol_select; read and cache: digits, point, tick size/value, contract size, volume min/max/step, stops level, freeze level, filling modes, trade mode (full / close-only / disabled), sessions, margin currency, profit currency.
2. Fetch only closed bars needed (copy_rates_from_pos with a bounded count), cache per symbol/timeframe, append incrementally.
3. Data sanity checks (log each case): missing bars, zero-volume bars, spikes > N×ATR, stale ticks, weekend gaps, broker time jumps. Bad data → skip evaluation for that bar.
4. Time: MT5 bar times are broker server time. Detect the broker UTC offset (and its DST changes) from tick time vs UTC, store everything in UTC, display in local time. Trading day for daily limits = broker day (configurable).

### C3. Market analysis (works in all modes, including Analysis-only)

1. Multi-timeframe trend matrix (M5…D1): direction and strength per TF (EMA structure, slope, ADX) + an overall bias score −100…+100 with reasons.
2. Market structure: confirmed swing highs/lows (a swing is confirmed only after N bars — no look-ahead), HH/HL/LH/LL, BOS / CHoCH, trend vs range classification.
3. Key levels: S/R from swing clustering, previous day/week high/low/close, Asia/London/NY session highs/lows, round numbers; distance to each level in ATR.
4. Volatility: ATR and its 100-day percentile, average daily range (ADR) and % used today, regime (low/normal/high).
5. Sessions: current session, per-symbol session statistics, session clock.
6. Correlation matrix (rolling) and a currency strength meter.
7. Spread monitor: the current spread vs the typical spread for this hour; warn when abnormal.
8. Candle patterns (engulfing, pin bar, inside bar) as information and optional ML features — never as standalone signals.
9. Economic calendar: the Python API cannot read the MT5 calendar. Provide (a) manual entry + CSV import and (b) a small MQL5 service/EA (CalendarExporter.mq5, full source) that exports CalendarValueHistory to a CSV the app reads every few minutes. Show upcoming high-impact events per currency with countdowns.
10. Analysis card per symbol in plain language, generated by rules, e.g. "XAUUSD — H4 uptrend, H1 pullback into support 2,318 (0.4 ATR), volatility high, London session, USD CPI in 3h → wait."
11. Opportunity scanner: on every closed bar, rank symbols by setup state ("forming" / "ready") and by probability × EV.

### C4. Strategies

1. A plugin interface:

```python
class Strategy(ABC):
    name: str
    version: str  # bump when logic changes
    params_model: type[BaseModel]  # pydantic → auto-generated settings form
    required_history: dict[Timeframe, int]

    def generate_signal(self, ctx: MarketContext) -> Signal | None: ...
```

2. MarketContext = closed bars of the entry TF + only fully closed higher-TF bars, symbol info, spread, session, analysis outputs.
3. Signal = uuid, symbol, TF, direction, order type (market/limit/stop), entry, SL, TP, R:R, reason text, strategy name/version, params hash, config id, features snapshot, bar time.
4. Two example strategies with exact rules (clearly labeled as examples, not proven profitable):
   - **Trend pullback**: H1 EMA50 > EMA200 (long) / < (short) and ADX(H1) > 20; on M15 the price pulls back to the EMA20 zone and RSI(14) crosses back above 50 (long) / below 50 (short); SL = beyond the last confirmed swing or 1.5×ATR (the larger, capped at 3×ATR); TP = 2R; no entry if a key level is within 1R in the trade direction.
   - **London breakout**: the Asia range (00:00–07:00 broker-configurable); range width between 0.5 and 1.5×ATR(D1-normalized); a stop order 0.1×ATR beyond the range at the London open; SL = the opposite side of the range (capped at 1.5×ATR); TP = 1.5R; cancel if not triggered by 11:00; one trade per symbol per day.

### C5. Signal lifecycle & decision pipeline

1. State machine (persisted, every transition logged): NEW → FILTERED_OUT | RISK_REJECTED | PENDING_APPROVAL → APPROVED | USER_REJECTED | EXPIRED → SENT → FILLED | FAILED → MANAGED → CLOSED
2. Pipeline on each closed bar: context → strategy → features → probability + EV + explanation → filters → risk → execution.
3. Filters (every one records value, threshold, pass/fail in the decision trace):
   - probability ≥ threshold AND EV ≥ min EV (per-strategy thresholds allowed)
   - one signal per symbol + strategy + bar; no new entry if the same strategy already has a position on that symbol
   - cooldown N bars after a loss; pause the strategy after X consecutive losses
   - max spread (fraction of ATR and of the SL distance)
   - sessions / trading hours; avoid rollover (~23:00–01:00 server time), Friday close, Monday open
   - news blackout (±N minutes around high-impact events for the related currencies)
   - stale data / market closed / symbol close-only
4. Semi-auto approval: the approval card shows everything (chart, probability, EV, lot, risk in money). On approval, re-validate (the price moved? spread? still within the entry tolerance?) before sending; expire after a timeout.

### C6. Risk management (non-bypassable; lives in the engine, not the UI)

1. Position sizing: lot = (equity or balance, configurable × risk%) / loss-per-lot at SL. Compute the loss per lot with mt5.order_calc_profit (correct for JPY, XAU, and cross-currency accounts), subtract the expected commission, round down to the volume step, clamp to min/max. If the min lot exceeds the allowed risk → reject. Log the full calculation.
2. Pre-trade checks: stops level, freeze level, order_calc_margin, order_check, free margin after the trade, margin level floor.
3. Limits: max open trades (total / per symbol / per strategy), max total open risk, max trades per day, max daily loss (realized + floating) vs start-of-day equity, max drawdown vs the equity high-water mark, per-currency exposure (e.g. long EURUSD + long GBPUSD = 2× short USD).
4. Manual trades (magic 0) count toward exposure and daily loss (setting), but the bot never touches them.
5. Limit states (daily P/L, HWM, loss streaks, cooldowns) are persisted in SQLite and survive restarts.
6. Daily loss hit → no new entries until the next trading day. Max DD hit → full stop until the user re-enables (typed confirmation).
7. Kill switch: close all bot positions, cancel bot pending orders, stop the engine (confirmation dialog, also via hotkey and Telegram).
8. Switching to a REAL account or to Auto mode requires typed confirmation.

### C7. Execution & position management

1. Buy at ask, sell at bid; SL/TP always inside the order request (server-side SL is mandatory); the correct filling mode per symbol; a deviation limit; a unique magic number per strategy; comment = short signal id.
2. Retcode handling policy table: retry only on safe codes (requote, price changed, off quotes, timeout — max N retries with fresh prices); never retry on invalid stops / no money / trade disabled / market closed. Log every request/response.
3. Record requested vs filled price → slippage; latency per request.
4. Pending orders with expiration.
5. Position management (per strategy, optional): break-even at X R (+ costs), ATR trailing, partial close at X R, time exit after N bars; respect the freeze level; track MFE/MAE live.
6. Netting accounts: the per-symbol position is shared — prevent opposite-direction signals from netting each other out (block or reduce, configurable) and document it.
7. State recovery: on startup/reconnect, load positions and orders by magic number, reconcile with the local DB, and resume management. Unknown bot positions are adopted and logged.
8. Closed trades from deal history (history_deals_get): real profit, commission, swap, close price, and the exit reason from the deal reason (SL / TP / stop-out / client / expert).

### C8. Paper broker & backtesting

1. PaperBroker implements the same Broker interface as the live broker; fills on live bid/ask + configurable slippage; SL/TP on ticks; commission; the same tables with mode='paper'.
2. BacktestBroker + a bar-by-bar engine reusing the same strategy, feature, risk, and position-management code. Entries at the next bar open; variable spread from bar data; commission; slippage; optional swap; SL+TP in the same candle → loss (or resolved with M1 data when available); weekend gaps.
3. Outputs: equity and drawdown curves, a trade list, win rate, profit factor, expectancy (R and money), avg win/loss, max DD (depth and duration), Sharpe/Sortino, longest losing streak, breakdowns by month/session/symbol/weekday.
4. Robustness: walk-forward (rolling in-sample/out-of-sample), Monte-Carlo trade reshuffling (DD distribution, risk of ruin), a parameter sensitivity heatmap (a stable plateau vs a sharp peak = overfitting warning), and a warning when there are fewer than 100 trades.
5. A test that proves backtest and live produce identical signals on identical data.
6. The MT5 Strategy Tester cannot be driven from Python; this engine replaces it — document that.

### C9. Go-Live gate (professional safeguard)

Before Auto mode can be enabled for a strategy+config on a REAL account, show a readiness checklist (thresholds configurable):

1. ≥ 100 backtest trades with positive OOS expectancy in walk-forward
2. ≥ 30 paper/demo trades with positive expectancy and slippage within the assumptions
3. the live/paper win rate within the calibration band of the predicted probability
4. no unresolved CRITICAL errors in the last 7 days, all health checks green
5. risk settings reviewed and confirmed

The user can override only with a typed confirmation, and the override is audit-logged.

### C10. Win probability (ML)

1. Labeling: simulate every historical signal forward with the same cost model as the backtest; win = TP before SL within N bars; SL+TP in the same candle = loss (or M1 resolution); timeouts labeled by the sign of R (configurable). Also store the continuous outcome (R) for EV modeling.
2. Features (only information available at signal time, ATR/%-normalized so they generalize across symbols): EMA distances/slopes, RSI, ADX, ATR percentile, Bollinger width, candle body/wick ratios, the structure state (BOS/CHoCH, trend/range), distance to key levels in ATR, % of ADR used, higher-TF alignment, currency-strength difference, minutes to the next high-impact news, spread/ATR, session, hour and weekday (cyclical), R:R, strategy id.
3. Training data: at least ~300 labeled signals per model (configurable). Below that → baseline = the strategy's historical win rate with a Wilson confidence interval, clearly labeled "baseline".
4. Validation: purged walk-forward time-series CV with an embargo — no shuffling. Report ROC-AUC, log-loss, Brier score, a calibration curve, and expectancy and profit factor per probability bucket out-of-sample. If the model does not beat the baseline OOS → warn and do not allow activation.
5. Calibration: isotonic or sigmoid on held-out folds; display "62% ± x (n = …)".
6. Explainability: top-3 SHAP factors per signal in plain words ("strong H1 uptrend +8%", "wide spread −5%").
7. Model registry: version id, training period, symbols, feature list + feature schema hash, metrics, file hash; stored locally + in Supabase. Activate / rollback from the UI. Refuse to load a model whose feature schema does not match the current code.
8. Drift monitoring: rolling live win rate vs the mean predicted probability (last N trades) and feature distribution drift (PSI); warn and suggest retraining.
9. Train in a separate process with progress reporting; the UI stays smooth.
10. The model estimates probability at entry only. Do not show a fake "live probability" for open trades.

### C11. Account & performance analytics

1. Stats: net profit, win rate, profit factor, expectancy (R and money), avg win/loss, payoff ratio, largest win/loss, streaks, Sharpe, Sortino, recovery factor, max DD (money, %, duration), time to recover.
2. Breakdowns: symbol, strategy, direction, session, hour, weekday, month, holding time, probability bucket, config version, bot vs manual.
3. Charts: equity + DD, monthly returns heatmap, P/L calendar, R distribution, MFE/MAE scatter (were SL/TP well placed? profit left on the table?), cost analysis (spread + commission + swap + slippage as % of gross profit).
4. Trader behavior analysis (manual trades): overtrading, revenge trading (a new trade within X min after a loss with a larger lot), holding losers longer than winners, trading during news, lot-size inconsistency, trading outside your best hours.
5. Risk of ruin + Monte-Carlo equity projection from the real trade distribution.
6. Comparisons: live vs paper vs backtest; config v1 vs v2; period A vs period B.
7. Filters everywhere: date range, account, symbol, strategy, mode, bot/manual. Export CSV/PNG.

### C12. Journal & reports

1. An auto-generated plain-language story per trade + entry/exit chart snapshots (data + PNG), user notes, tags, a 1–5 rating, an emotion tag for manual trades.
2. Daily report (end of trading day) and weekly report: P/L, trades, win rate, best/worst trade, rejected signals by reason, costs, errors/warnings, health issues, anomalies (high slippage, many requotes). Saved locally, in Supabase, and optionally sent to Telegram.

### C13. AI improvement loop

1. Export for AI (filters: date range, account, strategy, symbol, config, mode) produces:
   - trades_full.csv/json (one row per trade: trade + signal + features + decision trace summary),
   - report.md: summary stats, all breakdowns, calibration (predicted vs actual), MFE/MAE, cost analysis, rejected-signal counterfactuals (what would have happened, computed with the labeler), live vs backtest comparison, active configs and their JSON schema,
   - an analysis prompt at the top asking the AI to find weaknesses, overfitting, bad sessions/symbols, and calibration issues, and to return changes as JSON strictly following the params_json schema, each with a reason and the expected impact.
2. Import suggestion: paste the JSON → schema validation → diff vs the current config → save as a new config version (created_by = ai_suggestion) → automatic backtest + walk-forward vs the current config → activate in Paper only → the Go-Live gate before live.
3. Optional LLM integration ("Ask AI" buttons on the Analysis card, trade detail, Analytics): the user's own API key (keyring), OpenAI-compatible endpoint, a configurable model; sends compact structured summaries only (never credentials or account passwords); logs tokens and estimated cost; can be fully disabled.

### C14. Notifications & remote control

1. Windows toasts + tray; optional Telegram bot. Events: trade opened/closed, approval needed, limit hit, disconnect, errors, sync failing, drift, daily report.
2. Telegram commands (whitelisted chat ids + PIN): /status, /positions, /pnl, /pause, /resume, /approve <id>, /killswitch (with confirmation). Every command is audit-logged.
3. Per-event notification settings + quiet hours (urgent alerts always delivered).

## PART D — ARCHITECTURE & ENGINEERING

### D1. Tech stack

- Python 3.11 64-bit, Windows 10/11 (required by MetaTrader5)
- MT5: MetaTrader5 · UI: PySide6 (+ optional PySide6-Fluent-Widgets) · Charts: pyqtgraph (custom candlestick item)
- Data: pandas, numpy; own vectorized, unit-tested indicators (no unmaintained TA libraries)
- ML: lightgbm, scikit-learn, shap, joblib
- Storage: SQLite (WAL mode) with migrations + supabase-py
- Config: pydantic v2, pydantic-settings; secrets: keyring
- Logging: loguru · metrics: psutil · HTTP: httpx
- Packaging: PyInstaller one-folder + Inno Setup installer
- Tooling: uv (or pip-tools) with a lock file, ruff, mypy, pytest, pytest-qt, pre-commit

### D2. Architecture

Layered, event-driven, dependency-injected, with the domain independent of UI and MT5:

```
app/
  main.py                  # composition root (wires everything)
  domain/                  # pure logic, no I/O: models, enums, signal state machine, risk math, sizing, metrics
  core/                    # config, profiles, event_bus, clock (broker/UTC), single_instance, di container
  observability/           # logger, context (trace ids), decision_trace, audit, metrics, health, watchdog, crash_handler, debug_bundle
  mt5/                     # gateway.py (ONLY place importing MetaTrader5), connection, symbols, market_data, history_sync, retcodes
  brokers/                 # broker_interface.py, live_broker.py, paper_broker.py, backtest_broker.py
  engine/                  # trading_engine (orchestrator), bar_scheduler, signal_pipeline, position_manager, state_recovery, go_live_gate
  analysis/                # indicators, mtf_trend, structure, levels, volatility, sessions, correlation, currency_strength, patterns, scanner, analysis_card
  strategies/              # base, registry, trend_pullback, london_breakout
  ml/                      # features, labeler, dataset, trainer (subprocess), predictor, calibration, explain, registry, drift
  risk/                    # risk_manager, position_sizer, exposure, limits_state
  backtest/                # engine, costs, walk_forward, monte_carlo, sensitivity
  analytics/               # performance stats, breakdowns, behavior, reports, ai_export, ai_import
  calendar/                # store, csv_import, mql5/CalendarExporter.mq5
  storage/                 # sqlite_db, migrations/, repositories, outbox, supabase_sync, backup
  notify/                  # toast, telegram (+ remote commands)
  llm/                     # optional OpenAI-compatible client, prompts
  ui/                      # theme (tokens → QSS), i18n (en, fa), widgets, pages, dialogs, main_window, tray, command_palette
supabase/                  # schema.sql, views.sql, rls.sql, cleanup.sql
docs/                      # SPEC.md, ARCHITECTURE.md, PROGRESS.md, USER_GUIDE.md
tests/                     # unit, integration (FakeMT5), ui (pytest-qt)
```

### D3. Critical engineering rules

1. MT5 thread-safety: the MetaTrader5 package is not thread-safe. One dedicated MT5Gateway thread owns all MT5 calls through a command queue; everyone else gets results via futures/Qt signals. Every call has a timeout.
2. The UI thread never blocks. MT5, network, DB, ML and backtests run in workers; ML training and big backtests in a separate process.
3. Closed-bar driven: evaluation only on new closed candles per symbol/TF; ticks are used only for position management, paper fills, and the UI (throttled).
4. One logic, three runtimes: backtest, paper, and live share strategy, features, risk, costs, and position management through the Broker interface.
5. Domain purity: risk math, sizing, the state machine, and metrics are pure functions → easy to test.
6. Idempotency: client-generated UUIDs; Supabase writes are upserts.
7. Crash safety: server-side SL on every order; persisted state; recovery on start.
8. Single instance per profile; prevent Windows sleep while running (SetThreadExecutionState); warn on battery power.
9. Fake MT5 is for automated tests ONLY: a FakeMT5 simulator (scripted prices, retcodes, disconnects) lives in tests/fakes/, is used by pytest and the CI, and is never included in the release build. The shipped app always uses the real MetaTrader5 package and the real terminal on the user's PC (see Part I). There is no "demo data" or "mock" mode in the product — Paper mode also uses real live prices from the connected MT5 account.
10. Config safety: config changes while running apply only between bars, validated, versioned, and audit-logged.

### D4. Performance budgets

- Idle CPU < 3%, RAM < 500 MB after 7 days of running (bounded caches, no leaks).
- Bar evaluation for 10 symbols < 1 s; UI updates throttled to ≤ 4/s; charts smooth with 10k+ bars (downsampling, render the visible range only).
- App cold start < 5 s.

### D5. Security

- Secrets only in the keyring; masked in logs, exports, and debug bundles (a redaction filter + tests).
- Supabase: Auth (email login) + RLS user_id = auth.uid() on every table; only the anon key in the app; never the service_role key.
- LLM calls send no credentials and no account password; the account login number is optional (setting).
- A signed / checksummed build; document antivirus false positives for PyInstaller.

## PART E — DATA & OBSERVABILITY

### E1. Storage design

- Local SQLite = source of truth (WAL, migrations, daily backup, keep 7). Outbox pattern → a background upsert to Supabase in batches with exponential backoff; a sync status indicator; no data loss offline; handles paused Supabase free-tier projects.
- Supabase receives business data + WARNING+ logs; DEBUG/TRACE and high-frequency metrics stay local. A cleanup job aggregates/deletes old low-level rows (never trades/signals).

### E2. Tables (supabase/schema.sql — uuid PKs, timestamptz, user_id, account_id, indexes on time/symbol/strategy, RLS)

- accounts — broker, server, login, type, currency, leverage, margin_mode
- sessions — app_version, started/ended, mode, profile, settings_json
- strategy_configs — strategy, version, params_json, params_hash, created_by (user/ai_suggestion), parent_config_id, notes, is_active
- signals — bar_time, symbol, tf, strategy + version, config_id, direction, order_type, entry/sl/tp, rr, spread, atr, win_probability, prob_ci_low/high, probability_source (model/baseline), expected_value, model_version, features_json, shap_top_json, reason, state, decision, reject_reason, trace_id
- decision_traces — signal_id, steps_json (name, value, threshold, pass, ms), final_decision
- trades — signal_id, mode, source (bot/manual), ticket, position_id, magic, symbol, direction, volume, requested/open price, slippage, open_time, sl/tp initial, risk_money, close_time/price, profit, commission, swap, net_profit, r_multiple, outcome, exit_reason, duration_sec, mfe_r, mae_r, predicted_probability, session_label
- trade_events — trade_id, time, type (open/modify_sl/modify_tp/partial_close/close/error/retry), old/new values, reason, payload_json
- mt5_requests — trace_id, action, request_json, retcode, retcode_text, result_json, last_error, latency_ms, attempt
- account_snapshots — balance, equity, margin, free_margin, margin_level, open_positions, open_risk, daily_pnl, drawdown_pct (every 1–5 min)
- risk_events — type (daily_limit/dd_limit/kill_switch/exposure_block/margin_block/cooldown/strategy_paused), details_json
- model_versions — strategy, features + schema hash, train_period, symbols, metrics_json, file_hash, is_active
- backtest_runs — config_id, period, costs_json, metrics_json, walk_forward_json, monte_carlo_json
- journal — trade_id, narrative, snapshots, notes, tags, rating, emotion
- audit_log — source (user/system/ai_suggestion/telegram), action, before_json, after_json
- app_logs — level, category, module, function, message, trace_id, signal_id, trade_id, symbol, error_code, exception_type, stack_trace, context_json
- health_checks, performance_metrics, daily_reports, calendar_events
- Views: v_trade_full, v_daily_performance, v_performance_by_bucket, v_strategy_config_compare.

### E3. Logging & debugging (every part of the app)

1. Structured JSON logs via loguru, non-blocking (enqueue=True). Every record: UTC time, level, category, module, function, line, thread, session_id, trace_id, and when relevant signal_id / trade_id / ticket / symbol / strategy.
2. Categories (own file + own level, changeable at runtime): app, mt5, market_data, analysis, strategy, ml, risk, execution, position, sync, backtest, ui, notify, llm, audit, perf.
3. Files logs/<category>/<date>.jsonl + a readable logs/all.log; rotation, compression, retention (30 days), and a total size cap.
4. Debug mode toggle: all categories DEBUG for X minutes, then auto-revert.
5. Trace IDs follow a signal from the bar evaluation to the close and to the sync; the UI shows the full timeline of one trace.
6. Decision trace per signal as a readable checklist (e.g. probability 0.63 ≥ 0.60 ✓ | spread 1.8 ≤ 2.5 ✓ | news USD CPI in 12 min ✗ → REJECTED).
7. Trade lifecycle logging: pre-trade (sizing breakdown, order_check, margin, bid/ask/spread) → every MT5 request/response → every SL/TP change with the reason → partial closes → close with the deal data, slippage, costs.
8. Crash handler: sys.excepthook, threading.excepthook, the Qt message handler → crash_reports/crash_<time>.json (stack, last 200 log lines, state, versions, OS) + a friendly dialog.
9. Watchdog: heartbeats from each worker; freeze > N s → CRITICAL, notify, restart the worker.
10. Health checks (each minute): MT5 connected, Algo Trading on, quotes fresh, broker offset stable, Supabase reachable, sync queue size, disk space, log size, internet latency, PC clock drift.
11. Performance metrics: CPU, RAM, bar-processing latency, MT5 call latency p50/p95, queue sizes; a WARNING above budgets (D4).
12. Startup log: versions (app, Python, OS, MT5 build), broker, account type, profile, configs (no secrets), model version, enabled strategies.
13. Audit log: every user action and setting change (before → after), start/stop, kill switch, approvals, activations, Telegram commands, Go-Live overrides.
14. Debug bundle button: a zip of recent logs, crash reports, masked settings, health, versions, the last N decision traces + README_DEBUG.md with an AI prompt: "Find the root cause of this problem."

## PART F — UI/UX

### F1. Design system

- Calm, minimal, premium (Linear / Apple / TradingView level). Whitespace, a strong hierarchy, every key number readable within 1 second.
- Design tokens in one file → the QSS is generated. Dark (default): bg #0B0D12, surface #12151C, card #171B24, border #232836, text #E6E8EE, secondary #8A91A5, accent #5B8CFF, profit #22C55E, loss #EF4444, warning #F59E0B. A matching light theme.
- Inter / Segoe UI Variable (Vazirmatn for Persian); tabular numbers; max 4 type sizes; 8px grid; 12px radius; 1px subtle borders; 150–200 ms transitions; skeleton loaders.
- Accessibility: profit/loss never by color only (+/− and icons), WCAG AA contrast, full keyboard navigation, High-DPI, RTL.
- A reusable widget library: KPI card, probability ring, badge, toggle, data table (virtualized), drawer, toast, empty/error states, confirm dialog with typed confirmation.

### F2. Layout

1. A collapsible sidebar grouped: Trade (Dashboard, Market, Signals, Positions & Trades) · Analyze (Analytics, Journal, Backtest, Model, AI Lab) · System (Strategies, Risk, Logs, Health, Settings).
2. Status bar (always visible): connection dot, DEMO/REAL badge, mode badge, balance, equity, today P/L, DD bar, open risk, bot state (Running / Paused by limit / Stopped / Disconnected), sync state, session clock, next news, the Kill switch.
3. Command palette (Ctrl+K); shortcuts for start/stop, the kill switch (with confirm), page switching.
4. Onboarding wizard: MT5 connect → Supabase connect (with instructions) → symbols & risk profile → Paper mode start → a short product tour.

### F3. Pages

1. Dashboard — KPIs, equity/DD, open positions (live R, SL/TP distance), latest signals, risk-limit usage bars, a market bias strip, a Go-Live readiness status.
2. Market — watchlist + analysis cards, MTF matrix, interactive chart (TF switch, zoom/pan, crosshair, overlays, auto levels/structure, session shading, signal/trade markers), correlation, currency strength, calendar, scanner, "Ask AI".
3. Signals — a live feed including rejected signals with reasons, probability ring + CI, EV, SHAP factors, decision trace, approval cards.
4. Positions & Trades — open positions (manual close/modify with confirm) + history with filters; a detail drawer with a chart, the reasoning, features, events timeline, and the journal.
5. Analytics — C11.
6. Journal — C12 + a P/L calendar + reports.
7. Backtest — form, results, walk-forward, Monte-Carlo, sensitivity heatmap, compare.
8. Model — train, OOS metrics, calibration, buckets, importance, versions, drift.
9. AI Lab — export, import suggestion, diff, auto-backtest results, config comparison.
10. Strategies — cards: on/off, mode, auto-generated params form, symbols, config versions, stats, Go-Live checklist.
11. Risk — limits, usage, currency exposure, events, profiles.
12. Logs — category tabs, live tail, filters (level, category, symbol, strategy, time, regex), JSON detail, full trace view, change level, debug mode, export, open folder.
13. Health — checks, performance metrics, worker status, debug bundle.
14. Settings — accounts, Supabase, LLM, notifications/Telegram, logging, theme, language, startup, profiles, backup/import/export.

- Minimize to tray; optional start with Windows; on exit with open positions show: "Positions remain protected by server-side SL."

## PART G — QUALITY, DELIVERY & PHASES

### G1. Testing

1. Unit: indicators (vs reference values), sizing (EURUSD, USDJPY, XAUUSD, EUR-account conversion, min-lot rejection), risk limits, the state machine, the labeler (same-candle rule), structure/swing (no look-ahead), metrics (vs hand-calculated), retcode policy, secrets masking, trace propagation.
2. Integration with FakeMT5: full lifecycle signal → fill → break-even → close; disconnect/reconnect; state recovery after a crash; outbox offline → online without duplicates.
3. Backtest-vs-live signal equality test.
4. UI smoke tests with pytest-qt.
5. CI-ready pytest run; target ≥ 80% coverage on domain/, risk/, engine/.

### G2. Documentation

README.md (simple language): install Python 3.11 64-bit, create a venv, install, MT5 setup (Algo Trading, max bars), Supabase setup step by step (project, run SQL, auth), Telegram bot setup, first run, training a model, the Go-Live process, building the .exe/installer, troubleshooting (common MT5 errors and fixes). Plus docs/USER_GUIDE.md for daily use.

### G3. Phases (each ends with its acceptance checklist; STOP after each)

1. **Foundation**: repo, tooling, docs/SPEC.md, AGENTS.md, all GitHub files and workflows from Part H (CI, build, release-please, release, CodeQL, templates, Dependabot), design tokens, the main window, sidebar, empty pages, command palette. ✓ app launches, theme switches, lint/tests pass, the CI is green, and the build artifact downloads and runs.
2. **Observability**: logger, categories, trace ids, masking, crash handler, watchdog, basic Logs page. ✓ a forced exception produces a crash report; secrets are masked.
3. **MT5 connection (real)**: gateway thread on the real MetaTrader5 package, FakeMT5 in tests/fakes/ for CI only, test-connection checklist, account profiles, investor mode, symbols, status bar, request logging, the Connection Diagnostics tool and the mt5_smoke_test script (Part I). ✓ I run the downloaded build on my PC and it connects to my real MT5 demo account; all checklist items are shown with real values (broker, login, balance, live bid/ask).
4. **Storage**: SQLite + migrations, outbox, Supabase schema/views/RLS/auth, sync status, audit log, history import. ✓ offline writes sync later without duplicates.
5. **Market data & analysis**: data + sanity checks, broker time, all C3 modules, the chart, the Market page, calendar + MQL5 exporter. ✓ analysis cards for 3 symbols update on closed bars.
6. **Strategies & signals**: interface, 2 strategies, the pipeline, state machine, decision traces, scanner, Signals page (signal-only). ✓ every signal has a full decision trace.
7. **Risk**: sizing, limits, exposure, margin, persisted state, Risk page, profiles. ✓ sizing tests pass for all symbol types; limits survive a restart.
8. **Execution**: Paper broker, live broker, retcode policy, position manager, recovery, deal sync, semi-auto approval, kill switch, the mt5_trade_test script (Part I4). ✓ on my real MT5 demo account: a tiny test trade opens with SL/TP, appears in the MT5 terminal with the correct magic number, is modified, closed, and the closed deal (profit, commission, swap) is synced back; crash-recovery test passes.
9. **Backtesting**: engine, costs, metrics, walk-forward, Monte-Carlo, sensitivity, the equality test, Backtest page. ✓ backtest = live signals on the same data.
10. **ML**: labeler, features, trainer subprocess, calibration, SHAP, registry, drift, Model page, pipeline integration. ✓ the OOS report + baseline comparison; activation is blocked if it is worse.
11. **Analytics & journal**: Dashboard, Positions & Trades, Analytics, Journal, daily/weekly reports, notifications, Telegram. ✓ numbers match hand-checked examples.
12. **AI loop & Go-Live**: export, import, diff, auto-backtest, config compare, the Go-Live gate, optional LLM. ✓ an AI JSON suggestion goes through the whole flow to Paper.
13. **Reliability**: Health page, metrics, debug bundle, full Logs page, a 24-hour soak test on demo with a report. ✓ memory/CPU within budgets.
14. **Release**: Persian/RTL, light theme, polish, accessibility, full tests, README/USER_GUIDE, PyInstaller + Inno Setup. ✓ a clean install on a fresh Windows machine works.

### G4. Hard constraints (never violate)

- Never promise profit; always label probabilities as estimates with their uncertainty.
- Paper by default; REAL, Auto mode, and Go-Live overrides need typed confirmation and are audit-logged.
- Every live order has a server-side SL. No martingale / grid / averaging down / risk increase after a loss.
- No look-ahead bias, no data leakage, no shuffled time series.
- The bot never modifies manual trades.
- Secrets never in code, logs, exports, bundles, or LLM requests.
- The shipped app uses only real MT5 data from the user's terminal; mocks/fakes exist only in tests/.
- Handle broker differences: digits, point, tick value, contract size, suffixes, stops/freeze levels, filling modes, hedging/netting, server time/DST.

## PART H — GITHUB WORKFLOW (you work directly in my GitHub repository)

### H1. Git rules for you (the AI agent)

1. Never push directly to main. For every phase create a branch phase/<nn>-<short-name> (e.g. phase/03-mt5-connection) and open a Pull Request into main.
2. Small, logical commits using Conventional Commits: feat(risk): add currency exposure limit, fix(mt5): handle requote retry, test(...), docs(...), refactor(...), chore(...), ci(...). Breaking changes: feat!: + a BREAKING CHANGE: footer.
3. Before every push, run locally/in the sandbox: ruff check, ruff format --check, mypy, pytest. Do not push red code.
4. The PR description must contain: what was built, how to test it manually, the phase acceptance checklist (✓/✗), known limitations, screenshots of new UI pages when possible.
5. If CI fails, read the logs, fix, and push again to the same branch. Do not disable tests or lower the checks to make CI green.
6. Never commit secrets, .env, credentials, account numbers, model binaries > 50 MB, logs, or local databases (add a proper .gitignore).
7. Update docs/PROGRESS.md and CHANGELOG.md in every PR.

### H2. Repository files to create in Phase 1

1. AGENTS.md (and identical copies/symlinks as CLAUDE.md and .github/copilot-instructions.md): short rules for any AI agent working in this repo — read docs/SPEC.md + docs/PROGRESS.md first, the architecture rules (D3), the hard constraints (G4), the commands for lint/test/build, the commit and PR conventions (H1).
2. .gitignore, .editorconfig, pyproject.toml, the lock file, .pre-commit-config.yaml.
3. .github/pull_request_template.md (with the checklist sections), .github/ISSUE_TEMPLATE/ (bug report with "attach debug bundle", feature request, phase task).
4. .github/dependabot.yml (pip + GitHub Actions, weekly).
5. SECURITY.md (how secrets are handled), LICENSE (ask me which; default: proprietary / all rights reserved).

### H3. CI/CD with GitHub Actions (all on windows-latest, Python 3.11 x64)

1. ci.yml — on every push and PR: install with cache → ruff → mypy → pytest with coverage (using FakeMT5; the real MT5 terminal is NOT available in CI) → upload the coverage report. Required to pass before merge.
2. build.yml — on PR to main and on manual trigger: build with PyInstaller (one-folder) → run the built exe with --self-check (it must import MetaTrader5 successfully) → zip → upload as a workflow artifact so I can download and test the build of every PR.
3. release-please.yml — uses release-please on main: reads the Conventional Commits, automatically opens/updates a "Release PR" that bumps the version (SemVer: fix → patch, feat → minor, breaking → major), updates CHANGELOG.md and the version in pyproject.toml + app/__version__.py. When I merge the Release PR, it creates the git tag vX.Y.Z and a GitHub Release.
4. release.yml — on tag v*: run the tests, build the PyInstaller app + the Inno Setup installer (choco install innosetup on the runner), compute SHA-256 checksums, and attach MT5TradingWorkstation-Setup-X.Y.Z.exe, the portable zip, and checksums.txt to the GitHub Release. The release notes come from the changelog.
5. codeql.yml — security scanning for Python (weekly + on PR).
6. Use pinned action versions, the least permissions needed per workflow, and caching for pip/uv.
7. The app version shown in the UI and saved in every session comes from app/__version__.py (single source of truth, bumped only by release-please).

### H4. Branch protection (explain to me how to enable it, step by step)

1. main: require PRs, require the ci status check to pass, no force-push, no deletion. I review and merge every phase PR myself — you never merge to main.

### H5. Optional: auto-update in the app

1. On startup (setting: on/off) check the latest GitHub Release via the GitHub API, compare it with the local version, and show "Update available vX.Y.Z — see what's new" with a download button. Verify the SHA-256 checksum before running the installer. Never update automatically while the bot is running or positions are open.

## PART I — REAL MT5 CONNECTION ON MY PC (build on GitHub → download → run locally)

### I1. The setup (design for exactly this)

1. The code is written, tested (with FakeMT5), and built on GitHub Actions (the cloud has no MT5 terminal).
2. I download the build/installer from the PR artifact or the GitHub Release and run it on my own Windows PC, where my MT5 terminal is installed and logged in to my broker account.
3. On my PC the app must connect to the real MT5 terminal and my real account (demo first, then real). Everything the app shows (prices, balance, positions, history) must be real data from my MT5 — never mock, sample, or generated data.

### I2. Packaging requirements for a real MT5 connection

1. The MetaTrader5 package (and its numpy dependency) must be bundled correctly in the PyInstaller build (add hidden imports / collect data if needed) — verify in the CI that the built exe can import MetaTrader5 and print mt5.__version__ (a --self-check command-line flag that exits with code 0/1).
2. Build for 64-bit Windows only (MT5 is 64-bit). The installer must not require admin rights (install per user) so it can talk to a terminal running as the same user.
3. The app and the MT5 terminal must run as the same Windows user and the same privilege level (both normal, or both "Run as administrator") — detect a mismatch and explain it, since a mismatch is a common cause of initialize() failures.
4. A production build must fail at startup with a clear error (not fall back to fake data) if the MetaTrader5 package cannot be loaded.

### I3. First-run connection wizard (real account)

1. Find the terminal: auto-detect terminal64.exe (Program Files, %APPDATA%\MetaQuotes\Terminal\*, the registry, running processes), and list all found terminals with their broker name so I can pick the right one (many people have several brokers installed). "Browse…" as fallback.
2. Account details: login, password, server — with a server dropdown filled from the servers known to that terminal when possible, plus free text.
3. Connect via mt5.initialize(path, login, password, server, timeout=60000); if the terminal is closed, let initialize launch it and wait.
4. Live checklist with real values: terminal build, connected to the broker (terminal_info().connected), the account (login, name, broker, server, DEMO/REAL), balance/equity/currency/leverage, Algo Trading ON (if OFF: a screenshot-style hint "press the Algo Trading button in the MT5 toolbar" + a "Re-check" button), account trading permission, and the live bid/ask of 3 symbols updating every second (proof that the data is real).
5. Save the profile (the password in Windows Credential Manager). Next start: auto-connect.
6. Friendly messages for common real-world errors: wrong password / investor password (→ Analysis-only), wrong server, the terminal is not logged in, "IPC timeout" / "IPC initialize failed" (terminal not ready, or a different user/privilege level), the terminal is the 32-bit or MT4 version, no internet, the broker's server is down, the symbol is not in Market Watch, AutoTrading disabled by the server, a trade context busy.

### I4. Real-connection diagnostics tools (shipped with the app)

1. Connection Diagnostics page/button: runs all checks from I3 and additionally: ping/latency to the broker (terminal_info().ping_last), history availability per symbol/TF (bars count), the symbol specs table, the broker time offset, and permission flags. "Copy report" → a text report (no password) I can paste to the AI when something fails.
2. MT5TradingWorkstation.exe --mt5-smoke-test: connects with the saved profile, prints the account info, 3 symbol ticks, the last 10 bars of EURUSD M15, the last 10 deals — read-only, no orders. Exit code 0/1. Used to verify every new build in 30 seconds.
3. MT5TradingWorkstation.exe --mt5-trade-test --symbol EURUSD: refuses to run on a REAL account. On demo: opens the minimum lot with SL/TP, modifies the SL, closes it, reads the deal from the history, and prints a step-by-step ✓/✗ report with all retcodes. Proves the full real order path works.
4. Both tests write their results to the logs (mt5 + execution categories) and can be included in the debug bundle.

### I5. Running on my PC day-to-day

1. Start order: MT5 terminal (logged in, Algo Trading ON) → the app (auto-connect). Optional "start MT5 automatically" and "start the app with Windows".
2. If MT5 is closed, restarted, or loses the broker connection, the app shows it immediately, pauses new entries, keeps monitoring, and reconnects automatically; open positions stay protected by server-side SL/TP.
3. Document in the README: keep the PC awake / power settings, Windows updates schedule, the VPS option for 24/7, and "do not run two instances on the same account".

### I6. My testing loop for each phase PR

The PR description must include a "Test on your PC" section with exact steps, e.g.:

1. Download the artifact MT5TradingWorkstation-<branch>.zip from the PR → Checks → Artifacts.
2. Unzip and run MT5TradingWorkstation.exe --self-check, then --mt5-smoke-test.
3. Open the app and do X, Y, Z; expected result: ….
4. If something fails: Health → "Create debug bundle" and attach the zip (or paste the Diagnostics report) as a comment on the PR.

When I report a problem with a debug bundle, reproduce it with FakeMT5 in a test first, then fix it, so it never comes back.

### Start now

1. Confirm your understanding in 10 bullet points.
2. List any risks, contradictions, or improvements you see in this spec.
3. Show the final folder tree and pyproject.toml with pinned dependencies.
4. Implement Phase 1 completely on the branch phase/01-foundation, commit with Conventional Commits, push, open the PR into main, make sure the CI is green, then stop with the Phase 1 acceptance checklist and the PR link.
