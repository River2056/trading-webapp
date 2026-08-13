PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL CHECK (applied_at GLOB '????-??-??T??:??:??*Z')
);

CREATE TABLE IF NOT EXISTS operator_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z')
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    operator_id INTEGER NOT NULL REFERENCES operator_account(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    expires_at TEXT NOT NULL CHECK (expires_at GLOB '????-??-??T??:??:??*Z')
);

CREATE TABLE IF NOT EXISTS run_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    starting_capital_ntd TEXT NOT NULL CHECK (CAST(starting_capital_ntd AS REAL) > 0),
    round_duration_days INTEGER NOT NULL CHECK (round_duration_days > 0),
    strategy_cadence_seconds INTEGER NOT NULL CHECK (strategy_cadence_seconds > 0),
    max_position_allocation_pct TEXT NOT NULL
        CHECK (CAST(max_position_allocation_pct AS REAL) > 0
            AND CAST(max_position_allocation_pct AS REAL) <= 100),
    max_concurrent_positions INTEGER NOT NULL CHECK (max_concurrent_positions BETWEEN 1 AND 5),
    stop_loss_pct TEXT NOT NULL
        CHECK (CAST(stop_loss_pct AS REAL) > 0 AND CAST(stop_loss_pct AS REAL) < 100),
    take_profit_pct TEXT NOT NULL
        CHECK (CAST(take_profit_pct AS REAL) > 0 AND CAST(take_profit_pct AS REAL) <= 1000),
    daily_loss_limit_pct TEXT NOT NULL
        CHECK (CAST(daily_loss_limit_pct AS REAL) > 0
            AND CAST(daily_loss_limit_pct AS REAL) < 100),
    fee_pct TEXT NOT NULL CHECK (CAST(fee_pct AS REAL) >= 0 AND CAST(fee_pct AS REAL) < 10),
    slippage_pct TEXT NOT NULL
        CHECK (CAST(slippage_pct AS REAL) >= 0 AND CAST(slippage_pct AS REAL) < 10),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    CHECK (CAST(max_position_allocation_pct AS REAL) * max_concurrent_positions <= 100)
);

CREATE TABLE IF NOT EXISTS trading_run (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    desired_state TEXT NOT NULL CHECK (desired_state IN ('running', 'stopped')),
    current_capital_ntd TEXT NOT NULL CHECK (CAST(current_capital_ntd AS REAL) >= 0),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z')
);
