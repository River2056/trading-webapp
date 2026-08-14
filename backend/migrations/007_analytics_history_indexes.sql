CREATE INDEX idx_paper_trades_history ON paper_trades(executed_at DESC, id DESC);
CREATE INDEX idx_paper_trades_symbol_history
ON paper_trades(symbol, executed_at DESC, id DESC);
CREATE INDEX idx_paper_trades_side_history
ON paper_trades(side, executed_at DESC, id DESC);
CREATE INDEX idx_trading_round_history ON trading_round(started_at DESC, id DESC);
CREATE INDEX idx_trading_round_status_history
ON trading_round(status, started_at DESC, id DESC);
CREATE INDEX idx_trading_round_cycle_history
ON trading_round(cycle_id, started_at DESC, id DESC);
CREATE INDEX idx_cycles_history ON cycles(started_at DESC, id DESC);
CREATE INDEX idx_cycles_status_history ON cycles(status, started_at DESC, id DESC);
CREATE INDEX idx_snapshots_chart
ON portfolio_snapshots(valued_at ASC, id ASC, round_id);
CREATE INDEX idx_round_retrospectives_chart
ON round_retrospectives(created_at ASC, round_id ASC);

CREATE TRIGGER immutable_portfolio_snapshot_update
BEFORE UPDATE ON portfolio_snapshots
BEGIN SELECT RAISE(ABORT, 'portfolio snapshot is immutable'); END;
CREATE TRIGGER immutable_portfolio_snapshot_delete
BEFORE DELETE ON portfolio_snapshots
BEGIN SELECT RAISE(ABORT, 'portfolio snapshot is immutable'); END;
