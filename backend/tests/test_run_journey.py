from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_operator_can_configure_start_stop_and_observe_persisted_run(tmp_path: Path) -> None:
    database = tmp_path / "paper-trading.sqlite3"

    with TestClient(create_app(database_path=database)) as client:
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
            },
        )
        assert settings.status_code == 200

        started = client.post("/api/run/start")
        assert started.status_code == 200
        assert started.json()["desired_state"] == "running"

        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        assert dashboard.json() == {
            "product": "Paper Trading Only",
            "desired_state": "running",
            "configured_capital_ntd": "6500.00",
            "current_capital_ntd": "6500.00",
        }

        assert client.post("/api/run/stop").json()["desired_state"] == "stopped"

    with TestClient(create_app(database_path=database)) as restarted:
        assert (
            restarted.post(
                "/api/auth/login", json={"password": "correct horse battery staple"}
            ).status_code
            == 200
        )
        dashboard = restarted.get("/api/dashboard")
        assert dashboard.json()["desired_state"] == "stopped"
        assert dashboard.json()["current_capital_ntd"] == "6500.00"
