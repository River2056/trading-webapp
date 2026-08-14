from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.database import Database
from backend.app.engine import RoundPlanningError, RoundPlanningSettings, TradingEngine
from backend.app.main import create_app
from backend.app.market_data import BinanceMarketData, MarketDataError
from backend.tests.test_round_planning import FixtureMarketData


def planning_settings(**overrides: object) -> RoundPlanningSettings:
    values: dict[str, object] = {
        "candle_interval": "1h",
        "backtest_lookback_candles": 80,
        "minimum_liquidity_ntd": Decimal("1000000"),
        "fee_pct": Decimal("0.10"),
        "slippage_pct": Decimal("0.10"),
        "max_position_allocation_pct": Decimal("10"),
        "max_concurrent_positions": 3,
        "stop_loss_pct": Decimal("5"),
        "take_profit_pct": Decimal("10"),
        "daily_loss_limit_pct": Decimal("3"),
        "minimum_net_return_pct": Decimal("0"),
        "minimum_entry_count": 1,
        "minimum_trade_count": 2,
        "max_conversion_age_seconds": 3600,
    }
    values.update(overrides)
    return RoundPlanningSettings.from_mapping(values)


def test_active_round_allows_only_completion_without_plan_changes(tmp_path: Path) -> None:
    database = Database(tmp_path / "immutable.sqlite3")
    database.migrate()
    plan = TradingEngine(
        database, FixtureMarketData(), lambda: datetime(2026, 1, 8, 12, tzinfo=UTC)
    ).activate_round(planning_settings())
    with sqlite3.connect(database.path) as connection:
        for statement in (
            "UPDATE trading_round SET status='failed' WHERE id=?",
            "UPDATE trading_round SET frozen_settings_json='{}' WHERE id=?",
            "UPDATE trading_round SET status='completed', frozen_settings_json='{}' WHERE id=?",
            "DELETE FROM trading_round WHERE id=?",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, (plan.round_id,))
        connection.execute(
            "UPDATE trading_round SET status='completed' WHERE id=?", (plan.round_id,)
        )
        assert connection.execute(
            "SELECT status FROM trading_round WHERE id=?", (plan.round_id,)
        ).fetchone() == ("completed",)


def test_qualification_gates_and_candle_provenance_are_persisted(tmp_path: Path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.migrate()
    engine = TradingEngine(
        database, FixtureMarketData(), lambda: datetime(2026, 1, 8, 12, tzinfo=UTC)
    )
    with pytest.raises(RoundPlanningError):
        engine.activate_round(planning_settings(minimum_entry_count=999))
    with sqlite3.connect(database.path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT assumptions_json, qualified FROM backtest_results LIMIT 1"
        ).fetchone()
        assumptions = json.loads(row["assumptions_json"])
        assert row["qualified"] == 0
        assert assumptions["qualification"]["minimum_entry_count"] == 999
        candles = assumptions["candles"]
        assert candles["source"] == "fixture-market-data"
        assert candles["symbol"]
        assert candles["interval"] == "1h"
        assert candles["requested_count"] == candles["actual_count"] == 80
        assert candles["first_candle_at"] < candles["last_candle_at"]
        assert len(candles["sha256"]) == 64
        frozen = json.loads(
            connection.execute("SELECT frozen_settings_json FROM trading_round").fetchone()[0]
        )
        assert frozen["minimum_entry_count"] == 999


def test_conversion_records_both_fresh_legs_and_rejects_stale_legs() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)

    def transport(url: str, _params: dict[str, str]) -> object:
        if url.endswith("/ticker/price"):
            return {"symbol": "USDCUSDT", "price": "1", "closeTime": int(now.timestamp() * 1000)}
        return {
            "result": "success",
            "time_last_update_unix": int((now - timedelta(minutes=1)).timestamp()),
            "rates": {"TWD": "32"},
        }

    conversion = BinanceMarketData(
        transport=transport, clock=lambda: now, max_conversion_age=timedelta(hours=1)
    ).ntd_conversion("USDT")
    assert conversion.provenance.stablecoin.source
    assert conversion.provenance.stablecoin.observed_at == now
    assert conversion.provenance.fx.source
    assert conversion.provenance.fx.observed_at == now - timedelta(minutes=1)
    assert conversion.rate == conversion.provenance.stablecoin.rate * conversion.provenance.fx.rate

    for stale_leg in ("stablecoin", "fx"):

        def stale_transport(url: str, _params: dict[str, str], leg: str = stale_leg) -> object:
            if url.endswith("/ticker/price"):
                stamp = now - (timedelta(days=1) if leg == "stablecoin" else timedelta())
                return {"price": "1", "closeTime": int(stamp.timestamp() * 1000)}
            stamp = now - (timedelta(days=1) if leg == "fx" else timedelta())
            return {
                "result": "success",
                "time_last_update_unix": int(stamp.timestamp()),
                "rates": {"TWD": "32"},
            }

        with pytest.raises(MarketDataError, match=f"stale {stale_leg} conversion leg"):
            BinanceMarketData(
                transport=stale_transport, clock=lambda: now, max_conversion_age=timedelta(hours=1)
            ).ntd_conversion("USDT")


def test_dashboard_exposes_failure_health_and_success_clears_it(tmp_path: Path) -> None:
    fixture = FixtureMarketData(count=4)
    app = create_app(
        database_path=tmp_path / "health.sqlite3",
        market_data=fixture,
        clock=lambda: datetime(2026, 1, 8, 12, tzinfo=UTC),
    )
    with TestClient(app) as client:
        client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
        assert client.post("/api/run/start").status_code == 503
        failed = client.get("/api/dashboard").json()
        assert failed["engine_health"] == "degraded"
        assert failed["planning_failure"]["active"] is True
        assert "five markets" in failed["planning_failure"]["reason"]
        fixture.count = 6
        assert client.post("/api/run/start").status_code == 200
        recovered = client.get("/api/dashboard").json()
        assert recovered["engine_health"] == "healthy"
        assert recovered["planning_failure"] is None
