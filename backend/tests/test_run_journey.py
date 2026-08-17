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
        projected = dashboard.json()
        assert projected["product"] == "Paper Trading Only"
        assert projected["desired_state"] == "running"
        assert projected["configured_capital_ntd"] == "6500.00"
        assert projected["current_capital_ntd"] == "6500.00"
        assert projected["available_capital_ntd"] == "6500.00"
        assert projected["total_profit_ntd"] == "0.00"
        assert projected["profit_direction"] == "neutral"
        assert projected["engine_health"] == "healthy"
        assert projected["operational_state"] == "running"
        assert projected["round_status"] == "active"
        assert projected["completed_round_count"] == 0
        assert projected["cycle_count"] == 1
        assert projected["current_cycle"]["starting_capital_ntd"] == "6500"
        assert projected["bankruptcy"] is None
        assert len(projected["selected_pairs"]) == 5
        assert projected["risk_settings"]["max_position_allocation_pct"] == "15.00"
        assert projected["agent_activity"] == {
            "status": "starting",
            "title": "Starting round monitoring",
            "detail": "Waiting for the worker's first market evaluation.",
            "updated_at": "2026-01-08T12:00:00Z",
        }

        assert app.state.worker.step().outcome == "advanced"
        activity = client.get("/api/dashboard").json()["agent_activity"]
        assert activity["status"] == "monitoring"
        assert activity["title"] == "Monitoring active round"
        assert activity["detail"] == "Watching 5 selected markets; last worker outcome: advanced."

        assert client.post("/api/run/stop").json()["desired_state"] == "stopped"
        stopped = client.get("/api/dashboard").json()
        assert stopped["agent_activity"]["status"] == "idle"
        assert stopped["agent_activity"]["title"] == "Agent stopped"

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


def test_fresh_round_completes_paused_round_and_starts_a_new_one(tmp_path: Path) -> None:
    database = tmp_path / "fresh-round.sqlite3"
    app = create_app(
        database_path=database,
        market_data=FixtureMarketData(),
        clock=lambda: datetime(2026, 1, 8, 12, tzinfo=UTC),
        start_worker=False,
    )

    with TestClient(app) as client:
        client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
        first = client.post("/api/run/start")
        assert first.status_code == 200
        running_fresh = client.post("/api/run/fresh-round")
        assert running_fresh.status_code == 409
        assert running_fresh.json()["detail"] == "stop the run before starting fresh"
        assert client.post("/api/run/stop").status_code == 200

        fresh = client.post("/api/run/fresh-round")

        assert fresh.status_code == 200
        assert fresh.json()["desired_state"] == "running"
        assert fresh.json()["round_id"] != first.json()["round_id"]
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            rounds = connection.execute(
                "SELECT id, status, ended_at FROM trading_round ORDER BY id"
            ).fetchall()
            assert [row["status"] for row in rounds] == ["completed", "active"]
            assert rounds[0]["ended_at"] is not None
            assert connection.execute(
                "SELECT COUNT(*) FROM round_retrospectives"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT reason FROM lifecycle_transitions"
            ).fetchone()[0] == "fresh round requested"

        dashboard = client.get("/api/dashboard").json()
        assert dashboard["completed_round_count"] == 1
        assert dashboard["current_round"]["id"] == fresh.json()["round_id"]


def test_fresh_round_returns_retryable_error_without_finalizing_on_unsafe_data(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsafe-fresh-round.sqlite3"
    market_data = FixtureMarketData()
    app = create_app(
        database_path=database,
        market_data=market_data,
        clock=lambda: datetime(2026, 1, 8, 12, tzinfo=UTC),
        start_worker=False,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
        assert client.post("/api/run/start").status_code == 200
        assert client.post("/api/run/stop").status_code == 200
        market_data.count = 0

        fresh = client.post("/api/run/fresh-round")

        assert fresh.status_code == 503
        assert fresh.json()["detail"].startswith("fresh round finalization failed:")
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["desired_state"] == "stopped"
        assert dashboard["round_status"] == "active"
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM round_retrospectives"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM lifecycle_transitions"
            ).fetchone() == (0,)
