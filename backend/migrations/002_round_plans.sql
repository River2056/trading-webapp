CREATE TABLE trading_round (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL CHECK (status IN ('planning', 'active', 'completed', 'failed')),
    started_at TEXT NOT NULL,
    frozen_settings_json TEXT NOT NULL
);

CREATE TABLE market_rankings (
    round_id INTEGER NOT NULL REFERENCES trading_round(id),
    symbol TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    last_price TEXT NOT NULL,
    quote_volume TEXT NOT NULL,
    conversion_path TEXT,
    conversion_rate TEXT,
    conversion_observed_at TEXT,
    conversion_provenance_json TEXT,
    liquidity_ntd TEXT,
    score TEXT,
    rank INTEGER,
    selected INTEGER NOT NULL CHECK (selected IN (0, 1)),
    exclusion_reason TEXT,
    PRIMARY KEY (round_id, symbol)
);

CREATE TABLE backtest_results (
    round_id INTEGER NOT NULL REFERENCES trading_round(id),
    symbol TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    assumptions_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    qualified INTEGER NOT NULL CHECK (qualified IN (0, 1)),
    score TEXT NOT NULL,
    PRIMARY KEY (round_id, symbol, strategy_version)
);

CREATE TABLE round_selections (
    round_id INTEGER NOT NULL REFERENCES trading_round(id),
    symbol TEXT NOT NULL,
    selection_rank INTEGER NOT NULL,
    strategy_version TEXT NOT NULL,
    strategy_config_json TEXT NOT NULL,
    backtest_provenance_json TEXT NOT NULL,
    PRIMARY KEY (round_id, symbol),
    UNIQUE (round_id, selection_rank)
);

CREATE TABLE planning_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES trading_round(id),
    occurred_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TRIGGER immutable_active_round_update
BEFORE UPDATE ON trading_round WHEN OLD.status IN ('active', 'completed')
AND NOT (NEW.status='completed' AND NEW.frozen_settings_json=OLD.frozen_settings_json
         AND NEW.started_at=OLD.started_at AND NEW.id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'active round is immutable'); END;

CREATE TRIGGER immutable_completed_round_update
BEFORE UPDATE ON trading_round WHEN OLD.status = 'completed'
BEGIN SELECT RAISE(ABORT, 'completed round is immutable'); END;

CREATE TRIGGER immutable_active_round_delete
BEFORE DELETE ON trading_round WHEN OLD.status IN ('active', 'completed')
BEGIN SELECT RAISE(ABORT, 'active round is immutable'); END;

CREATE TRIGGER immutable_active_rankings_insert
BEFORE INSERT ON market_rankings
WHEN (SELECT status FROM trading_round WHERE id = NEW.round_id) IN ('active', 'completed')
BEGIN SELECT RAISE(ABORT, 'active round ranking is immutable'); END;
CREATE TRIGGER immutable_active_rankings_update
BEFORE UPDATE ON market_rankings
WHEN (SELECT status FROM trading_round WHERE id = OLD.round_id) IN ('active', 'completed')
BEGIN SELECT RAISE(ABORT, 'active round ranking is immutable'); END;
CREATE TRIGGER immutable_active_rankings_delete
BEFORE DELETE ON market_rankings
WHEN (SELECT status FROM trading_round WHERE id = OLD.round_id) IN ('active', 'completed')
BEGIN SELECT RAISE(ABORT, 'active round ranking is immutable'); END;

CREATE TRIGGER immutable_active_backtests_insert
BEFORE INSERT ON backtest_results
WHEN (SELECT status FROM trading_round WHERE id = NEW.round_id) IN ('active', 'completed')
BEGIN SELECT RAISE(ABORT, 'active round backtest is immutable'); END;
CREATE TRIGGER immutable_active_backtests_update
BEFORE UPDATE ON backtest_results
WHEN (SELECT status FROM trading_round WHERE id = OLD.round_id) IN ('active', 'completed')
BEGIN SELECT RAISE(ABORT, 'active round backtest is immutable'); END;
CREATE TRIGGER immutable_active_backtests_delete
BEFORE DELETE ON backtest_results
WHEN (SELECT status FROM trading_round WHERE id = OLD.round_id) IN ('active', 'completed')
BEGIN SELECT RAISE(ABORT, 'active round backtest is immutable'); END;

CREATE TRIGGER immutable_active_selections_insert
BEFORE INSERT ON round_selections
WHEN (SELECT status FROM trading_round WHERE id = NEW.round_id) IN ('active', 'completed')
BEGIN SELECT RAISE(ABORT, 'active round selection is immutable'); END;
CREATE TRIGGER immutable_active_selections_update
BEFORE UPDATE ON round_selections
WHEN (SELECT status FROM trading_round WHERE id = OLD.round_id) IN ('active', 'completed')
BEGIN SELECT RAISE(ABORT, 'active round selection is immutable'); END;
CREATE TRIGGER immutable_active_selections_delete
BEFORE DELETE ON round_selections
WHEN (SELECT status FROM trading_round WHERE id = OLD.round_id) IN ('active', 'completed')
BEGIN SELECT RAISE(ABORT, 'active round selection is immutable'); END;
