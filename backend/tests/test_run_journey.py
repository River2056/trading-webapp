import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.tests.test_round_planning import FixtureMarketData


def test_operator_can_configure_start_stop_and_observe_persisted_run(tmp_path: Path) -> None:
    database = tmp_path / "paper-trading.sqlite3"

    app = create_app(
        database_path=database,
        market_data=FixtureMarketData(),
        clock=lambda: datetime(2026, 1, 8, 12, tzinfo=UTC),
    )
    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
        assert signup.status_code == 201
        assert signup.cookies.get("paper_session")

        settings = client.put(
            "/api/settings",
            json={
                "starting_capital_ntd": "6500.00",
                "round_duration_days": 3,
                "strategy_cadence_seconds": 120,
                "max_position_allocation_pct": "15.00",
                "max_concurrent_positions": 3,
                "stop_loss_pct": "4.00",
                "take_profit_pct": "8.00",
                "daily_loss_limit_pct": "3.00",
                "fee_pct": "0.10",
                "slippage_pct": "0.10",
                "candle_interval": "1h",
                "backtest_lookback_candles": 80,
                "minimum_liquidity_ntd": "1000000",
                "minimum_net_return_pct": "0",
                "minimum_entry_count": 1,
                "minimum_trade_count": 2,
                "max_conversion_age_seconds": 86400,
                "max_candle_age_seconds": 7200,
            },
        )
        assert settings.status_code == 200

        started = client.post("/api/run/start")
        assert started.status_code == 200
        assert started.json()["desired_state"] == "running"
        assert len(started.json()["selections"]) == 5
        started_again = client.post("/api/run/start")
        assert started_again.status_code == 200
        assert started_again.json()["round_id"] == started.json()["round_id"]
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM trading_round WHERE status='active'"
            ).fetchone() == (1,)

        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        assert dashboard.json() == {
            "product": "Paper Trading Only",
            "desired_state": "running",
            "configured_capital_ntd": "6500.00",
            "current_capital_ntd": "6500.00",
            "engine_health": "healthy",
            "health": "healthy",
            "health_detail": None,
            "operational_state": "running",
            "run_status": "running",
            "round_status": "active",
            "current_round": {
                "id": started.json()["round_id"],
                "status": "active",
                "started_at": "2026-01-08T12:00:00Z",
                "ended_at": None,
                "ending_equity_ntd": None,
            },
            "completed_round_count": 0,
            "cycle_count": 1,
            "current_cycle": {
                "id": 1,
                "status": "active",
                "started_at": dashboard.json()["current_cycle"]["started_at"],
                "starting_capital_ntd": "6500",
                "completed_round_count": 0,
            },
            "bankruptcy": None,
            "latest_bankruptcy": None,
            "days_since_bankruptcy": None,
            "market_data_incident": None,
            "planning_failure": None,
        }

        assert client.post("/api/run/stop").json()["desired_state"] == "stopped"

    with TestClient(
        create_app(database_path=database, market_data=FixtureMarketData())
    ) as restarted:
        assert (
            restarted.post(
                "/api/auth/login", json={"password": "correct horse battery staple"}
            ).status_code
            == 200
        )
        dashboard = restarted.get("/api/dashboard")
        assert dashboard.json()["desired_state"] == "stopped"
        assert dashboard.json()["current_capital_ntd"] == "6500.00"


def test_start_reports_planning_failure_and_leaves_run_stopped(tmp_path: Path) -> None:
    database = tmp_path / "failed.sqlite3"
    app = create_app(
        database_path=database,
        market_data=FixtureMarketData(count=4),
        clock=lambda: datetime(2026, 1, 8, 12, tzinfo=UTC),
    )
    with TestClient(app) as client:
        client.post("/api/auth/signup", json={"password": "correct horse battery staple"})

        response = client.post("/api/run/start")

        assert response.status_code == 503
        assert "five markets with qualifying strategies" in response.json()["detail"]
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["desired_state"] == "stopped"
        assert dashboard["health"] == "degraded"
        assert "five markets" in dashboard["health_detail"]

        app.state.engine.market_data = FixtureMarketData()
        assert client.post("/api/run/start").status_code == 200
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["health"] == "healthy"
        assert dashboard["health_detail"] is None
