from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from backend.app.main import create_app
from backend.app.market_data import Candle, MarketSummary
from backend.tests.test_round_planning import FixtureMarketData


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 8, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class JourneyMarketData(FixtureMarketData):
    """Mutable deterministic provider used only by the browser acceptance app."""

    def __init__(self, clock: MutableClock) -> None:
        super().__init__()
        self.clock = clock
        self.mode = "planning"
        self.price_multiplier = Decimal("1")
        self.summary_calls = 0

    @property
    def observed_at(self) -> datetime:  # type: ignore[override]
        return self.clock.now

    @observed_at.setter
    def observed_at(self, _value: datetime) -> None:
        pass

    def market_summaries(self) -> list[MarketSummary]:
        self.summary_calls += 1
        multiplier = (
            self.price_multiplier
            if self.mode != "crash" or self.summary_calls == 1
            else Decimal("1")
        )
        return [
            MarketSummary(
                symbol=summary.symbol,
                base_asset=summary.base_asset,
                quote_asset=summary.quote_asset,
                last_price=summary.last_price * multiplier,
                quote_volume=summary.quote_volume,
                observed_at=self.clock.now,
            )
            for summary in super().market_summaries()
        ]

    def historical_candles(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        if self.mode == "planning" or (self.mode == "crash" and self.summary_calls > 1):
            return super().historical_candles(symbol, interval, limit)
        current = next(
            summary.last_price for summary in self.market_summaries() if summary.symbol == symbol
        )
        start = self.clock.now - timedelta(hours=limit - 1)
        if self.mode == "entry":
            # A flat series followed by one uptick is a deterministic final-candle MACD entry.
            closes = [current for _ in range(limit - 1)] + [current + Decimal("1")]
        else:
            closes = [current for _ in range(limit)]
        return [
            Candle(
                start + timedelta(hours=index),
                close,
                close,
                close,
                close,
                Decimal("10000"),
            )
            for index, close in enumerate(closes)
        ]


DATABASE_PATH = Path(os.environ.get("TRADING_DATABASE_PATH", "data/playwright.sqlite3"))
clock = MutableClock()
market_data = JourneyMarketData(clock)
app = create_app(
    database_path=DATABASE_PATH,
    market_data=market_data,
    clock=clock,
    start_worker=False,
)


@app.post("/__e2e__/reset")
def reset() -> dict[str, str]:
    """Give every browser test isolated application state without production imports."""
    DATABASE_PATH.unlink(missing_ok=True)
    app.state.database.migrate()
    app.state.database.ensure_defaults()
    clock.now = datetime(2026, 1, 8, 12, tzinfo=UTC)
    market_data.mode = "planning"
    market_data.price_multiplier = Decimal("1")
    market_data.summary_calls = 0
    app.state.worker.pending_database_lock = None
    return {"status": "reset"}


@app.post("/__e2e__/worker-step")
def worker_step(mode: str = "entry", advance_days: int = 0) -> dict[str, object]:
    """Advance the real production worker/engine/lifecycle under deterministic adapters."""
    clock.now += timedelta(days=advance_days)
    market_data.mode = mode
    market_data.price_multiplier = Decimal("0.00000001") if mode == "crash" else Decimal("1")
    market_data.summary_calls = 0
    result = app.state.worker.step()
    with app.state.database.connect() as connection:
        counts = {
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            )
            for table in ("paper_trades", "trading_round", "cycles", "bankruptcies")
        }
    return {"outcome": result.outcome, **counts}


@app.post("/__e2e__/seed-analytics")
def seed_analytics() -> dict[str, int]:
    """Legacy deterministic audit fixture; direct seeding is not the complete journey seam."""
    database = app.state.database
    now = clock.now
    with database.connect() as connection, connection:
        round_row = connection.execute(
            "SELECT id FROM trading_round WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if round_row is None:
            raise RuntimeError("start the run before seeding analytics")
        round_id = int(round_row["id"])
        for index in range(12):
            signal_id = f"e2e-signal-{index}"
            side = "sell" if index % 2 else "buy"
            pnl = "125" if index == 0 else "-75" if index == 1 else "0"
            at = (now - timedelta(minutes=index)).isoformat()
            evidence = json.dumps({"rsi": 20 + index, "fixture": True})
            connection.execute(
                "INSERT INTO trading_signals(round_id, symbol, interval_key, signal_id, "
                "evaluated_at, source_timestamp, strategy_version, action, outcome, reason, "
                "market_evidence_json) VALUES(?, ?, ?, ?, ?, ?, 'rsi-v1', ?, 'filled', ?, ?)",
                (
                    round_id,
                    "BTCUSDT",
                    f"e2e-{index}",
                    signal_id,
                    at,
                    at,
                    side,
                    f"fixture audit {index}",
                    evidence,
                ),
            )
            connection.execute(
                "INSERT INTO paper_trades(round_id, signal_id, symbol, side, quantity, "
                "market_price_ntd, fill_price_ntd, notional_ntd, fee_ntd, slippage_ntd, "
                "executed_at, source_timestamp, strategy_version, reason, realized_pnl_ntd) "
                "VALUES(?, ?, 'BTCUSDT', ?, '0.01', '100000', '100100', '1001', '1', '1', "
                "?, ?, 'rsi-v1', ?, ?)",
                (round_id, signal_id, side, at, at, f"fixture audit {index}", pnl),
            )
        connection.execute("UPDATE trading_run SET operational_state='degraded' WHERE id=1")
        connection.execute(
            "INSERT INTO market_data_incidents(round_id, cause, occurred_at, retry_count, "
            "next_retry_at, active, incident_kind) VALUES(?, ?, ?, 2, ?, 1, 'database_lock')",
            (
                round_id,
                "database is locked",
                now.isoformat(),
                (now + timedelta(minutes=1)).isoformat(),
            ),
        )
    return {"trades": 12}
