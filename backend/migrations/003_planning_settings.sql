ALTER TABLE run_settings ADD COLUMN candle_interval TEXT NOT NULL DEFAULT '1h';
ALTER TABLE run_settings ADD COLUMN backtest_lookback_candles INTEGER NOT NULL DEFAULT 80
    CHECK (backtest_lookback_candles BETWEEN 30 AND 1000);
ALTER TABLE run_settings ADD COLUMN minimum_liquidity_ntd TEXT NOT NULL DEFAULT '1000000'
    CHECK (CAST(minimum_liquidity_ntd AS REAL) >= 0);
ALTER TABLE run_settings ADD COLUMN minimum_net_return_pct TEXT NOT NULL DEFAULT '0';
ALTER TABLE run_settings ADD COLUMN minimum_entry_count INTEGER NOT NULL DEFAULT 1
    CHECK (minimum_entry_count >= 1);
ALTER TABLE run_settings ADD COLUMN minimum_trade_count INTEGER NOT NULL DEFAULT 2
    CHECK (minimum_trade_count >= 1);
ALTER TABLE run_settings ADD COLUMN max_conversion_age_seconds INTEGER NOT NULL DEFAULT 86400
    CHECK (max_conversion_age_seconds > 0);
ALTER TABLE run_settings ADD COLUMN max_candle_age_seconds INTEGER NOT NULL DEFAULT 7200
    CHECK (max_candle_age_seconds > 0);
