-- MT5 Trading Workstation — Supabase mirror schema (SPEC E2)
-- Version: 1 (matches app storage migration M001)
--
-- HOW TO APPLY
--   Supabase Dashboard → SQL Editor → paste this whole file → Run.
--   Re-running is safe (idempotent, guarded by IF NOT EXISTS / DO blocks).
--
-- DESIGN
--   - id TEXT PRIMARY KEY: the app generates UUIDv4 idempotency keys, so
--     offline-then-synced upserts never duplicate rows (SPEC G3-4).
--   - created_at/…: UTC timestamps (timestamptz).
--   - Indexes on time / symbol / strategy columns.
--   - RLS is ENABLED on every table. A desktop app signs no user in, so the
--     anon role gets NO access: use the service_role key in the app, or
--     create a dedicated user + policies (see supabase/README.md).

-- ---------------------------------------------------------------------------
-- Business tables
-- ---------------------------------------------------------------------------

create table if not exists accounts (
  id           text primary key,
  broker       text not null default '',
  server       text not null default '',
  login        bigint,
  type         text not null default 'demo',
  currency     text not null default '',
  leverage     bigint,
  margin_mode  text not null default '',
  created_at   timestamptz not null default now(),
  updated_at   timestamptz
);

create table if not exists sessions (
  id            text primary key,
  app_version   text not null default '',
  started_at    timestamptz not null,
  ended_at      timestamptz,
  mode          text not null default 'analysis',
  profile       text not null default '',
  settings_json jsonb not null default '{}',
  created_at    timestamptz not null default now()
);

create table if not exists strategy_configs (
  id               text primary key,
  strategy         text not null,
  version          text not null default '1',
  params_json      jsonb not null default '{}',
  params_hash      text not null default '',
  created_by       text not null default 'user',
  parent_config_id text,
  notes            text not null default '',
  is_active        boolean not null default false,
  created_at       timestamptz not null default now()
);
create index if not exists idx_strategy_configs_strategy
  on strategy_configs (strategy, created_at);

create table if not exists signals (
  id                 text primary key,
  bar_time           timestamptz,
  symbol             text,
  tf                 text,
  strategy           text,
  strategy_version   text,
  config_id          text,
  direction          text,
  order_type         text,
  entry              double precision,
  sl                 double precision,
  tp                 double precision,
  rr                 double precision,
  spread             double precision,
  atr                double precision,
  win_probability    double precision,
  prob_ci_low        double precision,
  prob_ci_high       double precision,
  probability_source text,
  expected_value     double precision,
  model_version      text,
  features_json      jsonb,
  shap_top_json      jsonb,
  reason             text,
  state              text not null default 'new',
  decision           text,
  reject_reason      text,
  trace_id           text,
  created_at         timestamptz not null default now()
);
create index if not exists idx_signals_bar_time on signals (bar_time);
create index if not exists idx_signals_symbol   on signals (symbol, created_at);
create index if not exists idx_signals_strategy on signals (strategy, created_at);
create index if not exists idx_signals_state    on signals (state);
create index if not exists idx_signals_trace    on signals (trace_id);

create table if not exists decision_traces (
  id             text primary key,
  signal_id      text not null,
  steps_json     jsonb not null default '[]',
  final_decision text not null default '',
  created_at     timestamptz not null default now()
);
create index if not exists idx_decision_traces_signal
  on decision_traces (signal_id, created_at);

create table if not exists trades (
  id                    text primary key,
  signal_id             text,
  mode                  text not null default 'paper',
  source                text not null default 'bot',
  ticket                bigint,
  position_id           bigint,
  magic                 bigint,
  symbol                text,
  direction             text,
  volume                double precision,
  requested_price       double precision,
  open_price            double precision,
  slippage              double precision,
  open_time             timestamptz,
  sl                    double precision,
  tp                    double precision,
  risk_money            double precision,
  close_time            timestamptz,
  close_price           double precision,
  profit                double precision,
  commission            double precision,
  swap                  double precision,
  net_profit            double precision,
  r_multiple            double precision,
  outcome               text,
  exit_reason           text,
  duration_sec          double precision,
  mfe_r                 double precision,
  mae_r                 double precision,
  predicted_probability double precision,
  session_label         text,
  comment               text,
  created_at            timestamptz not null default now()
);
create index if not exists idx_trades_symbol    on trades (symbol, open_time);
create index if not exists idx_trades_open_time on trades (open_time);
create index if not exists idx_trades_close     on trades (close_time);
create index if not exists idx_trades_signal    on trades (signal_id);

create table if not exists trade_events (
  id           text primary key,
  trade_id     text not null,
  time         timestamptz not null,
  type         text not null,
  old_value    text,
  new_value    text,
  reason       text,
  payload_json jsonb,
  created_at   timestamptz not null default now()
);
create index if not exists idx_trade_events_trade on trade_events (trade_id, time);

create table if not exists mt5_requests (
  id           text primary key,
  trace_id     text,
  action       text not null,
  request_json jsonb,
  retcode      bigint,
  retcode_text text,
  result_json  jsonb,
  last_error   text,
  latency_ms   double precision,
  attempt      bigint not null default 1,
  created_at   timestamptz not null default now()
);
create index if not exists idx_mt5_requests_trace   on mt5_requests (trace_id);
create index if not exists idx_mt5_requests_created on mt5_requests (created_at);

create table if not exists account_snapshots (
  id             text primary key,
  account_id     text,
  balance        double precision,
  equity         double precision,
  margin         double precision,
  free_margin    double precision,
  margin_level   double precision,
  open_positions bigint,
  open_risk      double precision,
  daily_pnl      double precision,
  drawdown_pct   double precision,
  created_at     timestamptz not null default now()
);
create index if not exists idx_account_snapshots_created
  on account_snapshots (created_at);

create table if not exists risk_events (
  id           text primary key,
  type         text not null,
  details_json jsonb,
  created_at   timestamptz not null default now()
);
create index if not exists idx_risk_events_created on risk_events (created_at);
create index if not exists idx_risk_events_type    on risk_events (type, created_at);

create table if not exists model_versions (
  id            text primary key,
  strategy      text not null,
  features_hash text not null default '',
  train_period  text not null default '',
  symbols       text not null default '',
  metrics_json  jsonb not null default '{}',
  file_hash     text not null default '',
  is_active     boolean not null default false,
  created_at    timestamptz not null default now()
);
create index if not exists idx_model_versions_strategy
  on model_versions (strategy, created_at);

create table if not exists backtest_runs (
  id                text primary key,
  config_id         text,
  period_start      timestamptz,
  period_end        timestamptz,
  costs_json        jsonb,
  metrics_json      jsonb,
  walk_forward_json jsonb,
  monte_carlo_json  jsonb,
  created_at        timestamptz not null default now()
);
create index if not exists idx_backtest_runs_created on backtest_runs (created_at);

create table if not exists journal (
  id             text primary key,
  trade_id       text,
  narrative      text not null default '',
  snapshots_json jsonb,
  notes          text not null default '',
  tags           text not null default '',
  rating         bigint,
  emotion        text not null default '',
  created_at     timestamptz not null default now()
);
create index if not exists idx_journal_trade on journal (trade_id);

create table if not exists audit_log (
  id          text primary key,
  source      text not null default 'user',
  action      text not null,
  before_json jsonb,
  after_json  jsonb,
  created_at  timestamptz not null default now()
);
create index if not exists idx_audit_log_created on audit_log (created_at);
create index if not exists idx_audit_log_action  on audit_log (action, created_at);

create table if not exists app_logs (
  id             text primary key,
  level          text not null,
  category       text not null default '',
  module         text not null default '',
  function       text not null default '',
  message        text not null default '',
  trace_id       text,
  signal_id      text,
  trade_id       text,
  symbol         text,
  error_code     text,
  exception_type text,
  stack_trace    text,
  context_json   jsonb,
  created_at     timestamptz not null default now()
);
create index if not exists idx_app_logs_level   on app_logs (level, created_at);
create index if not exists idx_app_logs_created on app_logs (created_at);

create table if not exists health_checks (
  id         text primary key,
  component  text not null,
  status     text not null,
  detail     text not null default '',
  created_at timestamptz not null default now()
);
create index if not exists idx_health_checks_created   on health_checks (created_at);
create index if not exists idx_health_checks_component on health_checks (component, created_at);

create table if not exists performance_metrics (
  id         text primary key,
  name       text not null,
  value      double precision,
  unit       text not null default '',
  created_at timestamptz not null default now()
);
create index if not exists idx_perf_metrics_name    on performance_metrics (name, created_at);
create index if not exists idx_perf_metrics_created on performance_metrics (created_at);

create table if not exists daily_reports (
  id          text primary key,
  day         date not null,
  report_json jsonb not null default '{}',
  created_at  timestamptz not null default now()
);
create index if not exists idx_daily_reports_day on daily_reports (day);

create table if not exists calendar_events (
  id         text primary key,
  event_time timestamptz not null,
  currency   text not null default '',
  impact     text not null default '',
  title      text not null default '',
  actual     text,
  forecast   text,
  previous   text,
  created_at timestamptz not null default now()
);
create index if not exists idx_calendar_events_time on calendar_events (event_time);

create table if not exists calibration_reports (
  id               text primary key,
  model_version    text not null default '',
  method           text not null default '',
  brier            double precision,
  ece              double precision,
  reliability_json jsonb,
  created_at       timestamptz not null default now()
);
create index if not exists idx_calibration_reports_created
  on calibration_reports (created_at);

create table if not exists drift_reports (
  id            text primary key,
  model_version text not null default '',
  feature       text not null default '',
  psi           double precision,
  details_json  jsonb,
  created_at    timestamptz not null default now()
);
create index if not exists idx_drift_reports_created on drift_reports (created_at);

-- ---------------------------------------------------------------------------
-- Row Level Security: tables exist for the mirror but the anon role (which a
-- desktop app effectively is, when not signing users in) gets nothing.
-- The app must use the service_role key, stored in Windows Credential Manager.
-- ---------------------------------------------------------------------------

do $$
declare
  t text;
begin
  foreach t in array array[
    'accounts','sessions','strategy_configs','signals','decision_traces',
    'trades','trade_events','mt5_requests','account_snapshots','risk_events',
    'model_versions','backtest_runs','journal','audit_log','app_logs',
    'health_checks','performance_metrics','daily_reports','calendar_events',
    'calibration_reports','drift_reports'
  ]
  loop
    execute format('alter table %I enable row level security', t);
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- Views (SPEC E2)
-- ---------------------------------------------------------------------------

create or replace view v_trade_full as
select
  t.*,
  s.strategy            as signal_strategy,
  s.win_probability     as signal_probability,
  s.expected_value      as signal_expected_value,
  s.trace_id            as signal_trace_id
from trades t
left join signals s on s.id = t.signal_id;

create or replace view v_daily_performance as
select
  date_trunc('day', coalesce(t.close_time, t.open_time)) as day,
  count(*)                                                as trades,
  count(*) filter (where t.outcome = 'win')               as wins,
  count(*) filter (where t.outcome = 'loss')              as losses,
  coalesce(sum(t.net_profit), 0)                          as net_profit,
  coalesce(avg(t.r_multiple), 0)                          as avg_r
from trades t
group by 1
order by 1;

create or replace view v_performance_by_bucket as
select
  coalesce(t.session_label, 'unlabeled') as bucket,
  count(*)                               as trades,
  count(*) filter (where t.outcome = 'win') as wins,
  round(
    count(*) filter (where t.outcome = 'win')::numeric
    / nullif(count(*), 0), 4
  )                                      as win_rate,
  coalesce(sum(t.net_profit), 0)         as net_profit
from trades t
group by 1
order by 1;

create or replace view v_strategy_config_compare as
select
  c.strategy,
  c.version,
  c.id                    as config_id,
  c.params_json,
  c.is_active,
  count(t.id)             as trades,
  count(t.id) filter (where t.outcome = 'win') as wins,
  coalesce(sum(t.net_profit), 0)               as net_profit
from strategy_configs c
left join signals s on s.config_id = c.id
left join trades t  on t.signal_id = s.id
group by c.id, c.strategy, c.version, c.params_json, c.is_active;
