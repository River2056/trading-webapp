import json
import os
from datetime import UTC, datetime, timedelta

from backend.app.main import create_app
from backend.tests.test_round_planning import FixtureMarketData

NOW = datetime(2026, 1, 8, 12, tzinfo=UTC)
app = create_app(
    database_path=os.environ.get("TRADING_DATABASE_PATH", "data/playwright.sqlite3"),
    market_data=FixtureMarketData(),
    clock=lambda: NOW,
    start_worker=False,
)


@app.post("/__e2e__/seed-analytics")
def seed_analytics() -> dict[str, int]:
    """Deterministic browser fixture; this module is never imported by production."""
    database = app.state.database
    with database.connect() as connection, connection:
        round_row = connection.execute(
            "SELECT id FROM trading_round WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if round_row is None:
            raise RuntimeError("start the run before seeding analytics")
        round_id = int(round_row["id"])
        connection.execute("DELETE FROM portfolio_snapshots WHERE round_id=?", (round_id,))
        connection.execute("DELETE FROM paper_trades WHERE round_id=?", (round_id,))
        connection.execute("DELETE FROM trading_signals WHERE round_id=?", (round_id,))
        for index in range(12):
            signal_id = f"e2e-signal-{index}"
            side = "sell" if index % 2 else "buy"
            pnl = "125" if index == 0 else "-75" if index == 1 else "0"
            at = (NOW - timedelta(minutes=index)).isoformat()
            evidence = json.dumps({"rsi": 20 + index, "fixture": True})
            connection.execute(
                "INSERT INTO trading_signals(round_id, symbol, interval_key, signal_id, "
                "evaluated_at, source_timestamp, strategy_version, action, outcome, reason, "
                "market_evidence_json) VALUES(?, ?, ?, ?, ?, ?, 'rsi-v1', ?, 'filled', ?, ?)",
                (round_id, "BTCUSDT", f"e2e-{index}", signal_id, at, at, side,
                 f"fixture audit {index}", evidence),
            )
            connection.execute(
                "INSERT INTO paper_trades(round_id, signal_id, symbol, side, quantity, "
                "market_price_ntd, fill_price_ntd, notional_ntd, fee_ntd, slippage_ntd, "
                "executed_at, source_timestamp, strategy_version, reason, realized_pnl_ntd) "
                "VALUES(?, ?, 'BTCUSDT', ?, '0.01', '100000', '100100', '1001', '1', '1', "
                "?, ?, 'rsi-v1', ?, ?)",
                (round_id, signal_id, side, at, at, f"fixture audit {index}", pnl),
            )
        connection.execute(
            "INSERT INTO portfolio_snapshots(round_id, interval_key, valued_at, cash_ntd, "
            "position_value_ntd, realized_pnl_ntd, unrealized_pnl_ntd, costs_ntd, "
            "available_capital_ntd, total_equity_ntd) VALUES(?, 'e2e', ?, '5900', '350', "
            "'200', '50', '12', '5550', '6250')",
            (round_id, NOW.isoformat()),
        )
        connection.execute(
            "UPDATE trading_run SET operational_state='degraded' WHERE id=1"
        )
        connection.execute(
            "INSERT INTO market_data_incidents(round_id, cause, occurred_at, retry_count, "
            "next_retry_at, active) VALUES(?, 'deterministic stale market fixture', ?, 2, ?, 1)",
            (round_id, NOW.isoformat(), (NOW + timedelta(minutes=1)).isoformat()),
        )
    return {"trades": 12}


@app.post("/__e2e__/seed-negative-analytics")
def seed_negative_analytics() -> dict[str, str]:
    """Append a genuinely negative persisted valuation for browser acceptance."""
    database = app.state.database
    with database.connect() as connection, connection:
        round_row = connection.execute(
            "SELECT id FROM trading_round WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if round_row is None:
            raise RuntimeError("start the run before seeding analytics")
        connection.execute(
            "INSERT INTO portfolio_snapshots(round_id, interval_key, valued_at, cash_ntd, "
            "position_value_ntd, realized_pnl_ntd, unrealized_pnl_ntd, costs_ntd, "
            "available_capital_ntd, total_equity_ntd) VALUES(?, 'e2e-negative', ?, '4700', "
            "'100', '-150', '-50', '20', '4650', '4800')",
            (round_row["id"], (NOW + timedelta(minutes=1)).isoformat()),
        )
    return {"total_equity_ntd": "4800"}
