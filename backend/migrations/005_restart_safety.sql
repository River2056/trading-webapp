ALTER TABLE trading_run ADD COLUMN operational_state TEXT NOT NULL DEFAULT 'stopped'
    CHECK (operational_state IN ('running', 'stopped', 'degraded'));

CREATE TABLE market_data_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER REFERENCES trading_round(id),
    cause TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    next_retry_at TEXT,
    recovered_at TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    CHECK ((active = 1 AND recovered_at IS NULL) OR active = 0)
);

CREATE UNIQUE INDEX one_active_market_data_incident
ON market_data_incidents(active) WHERE active = 1;

CREATE TABLE worker_checkpoint (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_attempt_at TEXT,
    last_success_at TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('idle', 'advanced', 'stopped', 'degraded', 'backoff')),
    updated_at TEXT NOT NULL
);
