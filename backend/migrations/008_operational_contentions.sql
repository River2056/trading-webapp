ALTER TABLE market_data_incidents ADD COLUMN incident_kind TEXT NOT NULL DEFAULT 'market_data'
    CHECK (incident_kind IN ('market_data', 'database_lock'));
