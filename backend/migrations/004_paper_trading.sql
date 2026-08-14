CREATE TABLE trading_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES trading_round(id),
    symbol TEXT NOT NULL,
    interval_key TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    source_timestamp TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'hold')),
    outcome TEXT NOT NULL CHECK (outcome IN ('filled', 'rejected', 'observed')),
    reason TEXT NOT NULL,
    market_evidence_json TEXT NOT NULL,
    UNIQUE (round_id, symbol, interval_key),
    UNIQUE (round_id, symbol, signal_id),
    UNIQUE (signal_id)
);

CREATE TABLE paper_positions (
    round_id INTEGER NOT NULL REFERENCES trading_round(id),
    symbol TEXT NOT NULL,
    quantity TEXT NOT NULL CHECK (CAST(quantity AS REAL) > 0),
    entry_price_ntd TEXT NOT NULL CHECK (CAST(entry_price_ntd AS REAL) > 0),
    entry_cost_ntd TEXT NOT NULL CHECK (CAST(entry_cost_ntd AS REAL) >= 0),
    opened_at TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    entry_signal_id TEXT NOT NULL,
    FOREIGN KEY (round_id, symbol, entry_signal_id)
        REFERENCES trading_signals(round_id, symbol, signal_id),
    PRIMARY KEY (round_id, symbol)
);

CREATE TABLE paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES trading_round(id),
    signal_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity TEXT NOT NULL CHECK (CAST(quantity AS REAL) > 0),
    market_price_ntd TEXT NOT NULL CHECK (CAST(market_price_ntd AS REAL) > 0),
    fill_price_ntd TEXT NOT NULL CHECK (CAST(fill_price_ntd AS REAL) > 0),
    notional_ntd TEXT NOT NULL CHECK (CAST(notional_ntd AS REAL) > 0),
    fee_ntd TEXT NOT NULL CHECK (CAST(fee_ntd AS REAL) >= 0),
    slippage_ntd TEXT NOT NULL CHECK (CAST(slippage_ntd AS REAL) >= 0),
    executed_at TEXT NOT NULL,
    source_timestamp TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    reason TEXT NOT NULL,
    realized_pnl_ntd TEXT NOT NULL,
    FOREIGN KEY (round_id, symbol, signal_id)
        REFERENCES trading_signals(round_id, symbol, signal_id)
);

CREATE TABLE portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES trading_round(id),
    interval_key TEXT NOT NULL,
    valued_at TEXT NOT NULL,
    cash_ntd TEXT NOT NULL,
    position_value_ntd TEXT NOT NULL,
    realized_pnl_ntd TEXT NOT NULL,
    unrealized_pnl_ntd TEXT NOT NULL,
    costs_ntd TEXT NOT NULL,
    available_capital_ntd TEXT NOT NULL,
    total_equity_ntd TEXT NOT NULL,
    UNIQUE (round_id, interval_key)
);
