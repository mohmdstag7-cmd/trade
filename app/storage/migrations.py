"""Idempotent schema migrations (SPEC E1).

The runner keeps a ``schema_migrations`` ledger. Each migration is applied
at most once, inside a transaction together with its ledger row, so a
crash mid-migration can never leave a half-updated schema. Re-running is a
no-op — this is what makes startup and ``--db-check`` safely repeatable.

M001 creates the complete SPEC E2 table set (uuid text PKs, UTC ISO-8601
timestamps, indexes on time/symbol/strategy) plus the outbox queue. Later
phases extend the list with new numbered migrations; nothing here is ever
edited in place.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.observability.logger import get_logger
from app.storage.db import Database

log = get_logger("sync")


def _statements(script: str) -> list[str]:
    """Split a SQL script into complete statements.

    Uses :func:`sqlite3.complete_statement` so semicolons inside string
    literals never split a statement. ``executescript`` cannot be used here
    because it implicitly COMMITs any open transaction, which would break
    the all-or-nothing guarantee below.
    """
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines():
        buffer += line + "\n"
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    tail = buffer.strip()
    if tail:
        statements.append(tail)
    return statements


M001_INITIAL = """
CREATE TABLE IF NOT EXISTS accounts (
    id           TEXT PRIMARY KEY,
    broker       TEXT NOT NULL DEFAULT '',
    server       TEXT NOT NULL DEFAULT '',
    login        INTEGER NOT NULL DEFAULT 0,
    type         TEXT NOT NULL DEFAULT 'demo',
    currency     TEXT NOT NULL DEFAULT '',
    leverage     INTEGER NOT NULL DEFAULT 0,
    margin_mode  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    app_version   TEXT NOT NULL DEFAULT '',
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    mode          TEXT NOT NULL DEFAULT 'analysis',
    profile       TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_configs (
    id               TEXT PRIMARY KEY,
    strategy         TEXT NOT NULL,
    version          TEXT NOT NULL DEFAULT '1',
    params_json      TEXT NOT NULL DEFAULT '{}',
    params_hash      TEXT NOT NULL DEFAULT '',
    created_by       TEXT NOT NULL DEFAULT 'user',
    parent_config_id TEXT,
    notes            TEXT NOT NULL DEFAULT '',
    is_active        INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_configs_strategy
    ON strategy_configs (strategy, created_at);

CREATE TABLE IF NOT EXISTS signals (
    id                 TEXT PRIMARY KEY,
    bar_time           TEXT,
    symbol             TEXT,
    tf                 TEXT,
    strategy           TEXT,
    strategy_version   TEXT,
    config_id          TEXT,
    direction          TEXT,
    order_type         TEXT,
    entry              REAL,
    sl                 REAL,
    tp                 REAL,
    rr                 REAL,
    spread             REAL,
    atr                REAL,
    win_probability    REAL,
    prob_ci_low        REAL,
    prob_ci_high       REAL,
    probability_source TEXT,
    expected_value     REAL,
    model_version      TEXT,
    features_json      TEXT,
    shap_top_json      TEXT,
    reason             TEXT,
    state              TEXT NOT NULL DEFAULT 'new',
    decision           TEXT,
    reject_reason      TEXT,
    trace_id           TEXT,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_bar_time ON signals (bar_time);
CREATE INDEX IF NOT EXISTS idx_signals_symbol   ON signals (symbol, created_at);
CREATE INDEX IF NOT EXISTS idx_signals_strategy ON signals (strategy, created_at);
CREATE INDEX IF NOT EXISTS idx_signals_state    ON signals (state);
CREATE INDEX IF NOT EXISTS idx_signals_trace    ON signals (trace_id);

CREATE TABLE IF NOT EXISTS decision_traces (
    id             TEXT PRIMARY KEY,
    signal_id      TEXT NOT NULL,
    steps_json     TEXT NOT NULL DEFAULT '[]',
    final_decision TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_traces_signal
    ON decision_traces (signal_id, created_at);

CREATE TABLE IF NOT EXISTS trades (
    id                   TEXT PRIMARY KEY,
    signal_id            TEXT,
    mode                 TEXT NOT NULL DEFAULT 'paper',
    source               TEXT NOT NULL DEFAULT 'bot',
    ticket               INTEGER,
    position_id          INTEGER,
    magic                INTEGER,
    symbol               TEXT,
    direction            TEXT,
    volume               REAL,
    requested_price      REAL,
    open_price           REAL,
    slippage             REAL,
    open_time            TEXT,
    sl                   REAL,
    tp                   REAL,
    risk_money           REAL,
    close_time           TEXT,
    close_price          REAL,
    profit               REAL,
    commission           REAL,
    swap                 REAL,
    net_profit           REAL,
    r_multiple           REAL,
    outcome              TEXT,
    exit_reason          TEXT,
    duration_sec         REAL,
    mfe_r                REAL,
    mae_r                REAL,
    predicted_probability REAL,
    session_label        TEXT,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol    ON trades (symbol, open_time);
CREATE INDEX IF NOT EXISTS idx_trades_open_time ON trades (open_time);
CREATE INDEX IF NOT EXISTS idx_trades_close     ON trades (close_time);
CREATE INDEX IF NOT EXISTS idx_trades_signal    ON trades (signal_id);

CREATE TABLE IF NOT EXISTS trade_events (
    id           TEXT PRIMARY KEY,
    trade_id     TEXT NOT NULL,
    time         TEXT NOT NULL,
    type         TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT,
    reason       TEXT,
    payload_json TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trade_events_trade ON trade_events (trade_id, time);

CREATE TABLE IF NOT EXISTS mt5_requests (
    id            TEXT PRIMARY KEY,
    trace_id      TEXT,
    action        TEXT NOT NULL,
    request_json  TEXT,
    retcode       INTEGER,
    retcode_text  TEXT,
    result_json   TEXT,
    last_error    TEXT,
    latency_ms    REAL,
    attempt       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mt5_requests_trace    ON mt5_requests (trace_id);
CREATE INDEX IF NOT EXISTS idx_mt5_requests_created  ON mt5_requests (created_at);

CREATE TABLE IF NOT EXISTS account_snapshots (
    id             TEXT PRIMARY KEY,
    account_id     TEXT,
    balance        REAL,
    equity         REAL,
    margin         REAL,
    free_margin    REAL,
    margin_level   REAL,
    open_positions INTEGER,
    open_risk      REAL,
    daily_pnl      REAL,
    drawdown_pct   REAL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_account_snapshots_created
    ON account_snapshots (created_at);

CREATE TABLE IF NOT EXISTS risk_events (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    details_json TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_risk_events_created ON risk_events (created_at);
CREATE INDEX IF NOT EXISTS idx_risk_events_type    ON risk_events (type, created_at);

CREATE TABLE IF NOT EXISTS model_versions (
    id            TEXT PRIMARY KEY,
    strategy      TEXT NOT NULL,
    features_hash TEXT NOT NULL DEFAULT '',
    train_period  TEXT NOT NULL DEFAULT '',
    symbols       TEXT NOT NULL DEFAULT '',
    metrics_json  TEXT NOT NULL DEFAULT '{}',
    file_hash     TEXT NOT NULL DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_versions_strategy
    ON model_versions (strategy, created_at);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id                TEXT PRIMARY KEY,
    config_id         TEXT,
    period_start      TEXT,
    period_end        TEXT,
    costs_json        TEXT,
    metrics_json      TEXT,
    walk_forward_json TEXT,
    monte_carlo_json  TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_created ON backtest_runs (created_at);

CREATE TABLE IF NOT EXISTS journal (
    id             TEXT PRIMARY KEY,
    trade_id       TEXT,
    narrative      TEXT NOT NULL DEFAULT '',
    snapshots_json TEXT,
    notes          TEXT NOT NULL DEFAULT '',
    tags           TEXT NOT NULL DEFAULT '',
    rating         INTEGER,
    emotion        TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_trade ON journal (trade_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL DEFAULT 'user',
    action      TEXT NOT NULL,
    before_json TEXT,
    after_json  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log (created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_action  ON audit_log (action, created_at);

CREATE TABLE IF NOT EXISTS app_logs (
    id             TEXT PRIMARY KEY,
    level          TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT '',
    module         TEXT NOT NULL DEFAULT '',
    function       TEXT NOT NULL DEFAULT '',
    message        TEXT NOT NULL DEFAULT '',
    trace_id       TEXT,
    signal_id      TEXT,
    trade_id       TEXT,
    symbol         TEXT,
    error_code     TEXT,
    exception_type TEXT,
    stack_trace    TEXT,
    context_json   TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_logs_level   ON app_logs (level, created_at);
CREATE INDEX IF NOT EXISTS idx_app_logs_created ON app_logs (created_at);

CREATE TABLE IF NOT EXISTS health_checks (
    id          TEXT PRIMARY KEY,
    component   TEXT NOT NULL,
    status      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_health_checks_created   ON health_checks (created_at);
CREATE INDEX IF NOT EXISTS idx_health_checks_component ON health_checks (component, created_at);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    value      REAL,
    unit       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_perf_metrics_name    ON performance_metrics (name, created_at);
CREATE INDEX IF NOT EXISTS idx_perf_metrics_created ON performance_metrics (created_at);

CREATE TABLE IF NOT EXISTS daily_reports (
    id          TEXT PRIMARY KEY,
    day         TEXT NOT NULL,
    report_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_daily_reports_day ON daily_reports (day);

CREATE TABLE IF NOT EXISTS calendar_events (
    id         TEXT PRIMARY KEY,
    event_time TEXT NOT NULL,
    currency   TEXT NOT NULL DEFAULT '',
    impact     TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL DEFAULT '',
    actual     TEXT,
    forecast   TEXT,
    previous   TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calendar_events_time ON calendar_events (event_time);

CREATE TABLE IF NOT EXISTS calibration_reports (
    id              TEXT PRIMARY KEY,
    model_version   TEXT NOT NULL DEFAULT '',
    method          TEXT NOT NULL DEFAULT '',
    brier           REAL,
    ece             REAL,
    reliability_json TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calibration_reports_created
    ON calibration_reports (created_at);

CREATE TABLE IF NOT EXISTS drift_reports (
    id            TEXT PRIMARY KEY,
    model_version TEXT NOT NULL DEFAULT '',
    feature       TEXT NOT NULL DEFAULT '',
    psi           REAL,
    details_json  TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drift_reports_created ON drift_reports (created_at);

CREATE TABLE IF NOT EXISTS outbox (
    id              TEXT PRIMARY KEY,
    table_name      TEXT NOT NULL,
    row_id          TEXT NOT NULL,
    payload         TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_claim ON outbox (state, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_outbox_table ON outbox (table_name, created_at);
"""


@dataclass(frozen=True, slots=True)
class Migration:
    """One numbered, immutable schema step."""

    version: int
    name: str
    sql: str


MIGRATIONS: tuple[Migration, ...] = (Migration(version=1, name="initial_schema", sql=M001_INITIAL),)


class MigrationRunner:
    """Applies :data:`MIGRATIONS` exactly once each, transactionally."""

    def __init__(self, db: Database, migrations: tuple[Migration, ...] = MIGRATIONS) -> None:
        self._db = db
        self._migrations = sorted(migrations, key=lambda m: m.version)
        self._ensure_ledger()

    def _ensure_ledger(self) -> None:
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)"
        )

    # -- state -----------------------------------------------------------------
    def applied_versions(self) -> list[int]:
        rows = self._db.query("SELECT version FROM schema_migrations ORDER BY version")
        return [int(r["version"]) for r in rows]

    def current_version(self) -> int:
        """Latest applied version (0 on a fresh database)."""
        versions = self.applied_versions()
        return versions[-1] if versions else 0

    def pending(self) -> list[Migration]:
        applied = set(self.applied_versions())
        return [m for m in self._migrations if m.version not in applied]

    # -- execution ---------------------------------------------------------------
    def run_all(self) -> list[Migration]:
        """Apply every pending migration; return the ones applied now.

        The DDL statements and the ledger row commit atomically (transactional
        DDL), so a crash mid-migration rolls the whole step back.
        """
        applied_now: list[Migration] = []
        for migration in self.pending():
            with self._db.transaction():
                self._db.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at)"
                    " VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                    (migration.version, migration.name),
                )
                for statement in _statements(migration.sql):
                    self._db.execute(statement)
            applied_now.append(migration)
            log.info("storage: applied migration {:03d} ({})", migration.version, migration.name)
        return applied_now

    def verify(self) -> bool:
        """Quick integrity check (cheap enough to run at startup)."""
        return self._db.integrity_ok()
