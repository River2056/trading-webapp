import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.tests.test_round_planning import FixtureMarketData


def test_api_is_protected_and_single_account_password_is_hashed(tmp_path: Path) -> None:
    database = tmp_path / "auth.sqlite3"
    with TestClient(create_app(database)) as client:
        assert client.get("/api/dashboard").status_code == 401
        assert client.post("/api/auth/signup", json={"password": "short"}).status_code == 422
        assert (
            client.post(
                "/api/auth/signup", json={"password": "correct horse battery staple"}
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/auth/signup", json={"password": "a different long password"}
            ).status_code
            == 409
        )
        client.cookies.clear()
        assert (
            client.post("/api/auth/login", json={"password": "this password is wrong"}).status_code
            == 401
        )

    with sqlite3.connect(database) as connection:
        stored = connection.execute("SELECT password_hash FROM operator_account").fetchone()[0]
        assert stored != "correct horse battery staple"
        assert stored.startswith("$argon2")
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [
            (1,),
            (2,),
            (3,),
            (4,),
            (5,),
            (6,),
        ]


def test_logout_revokes_the_server_side_session(tmp_path: Path) -> None:
    database = tmp_path / "logout.sqlite3"
    with TestClient(create_app(database)) as client:
        client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
        token = client.cookies.get("paper_session")
        assert token

        assert client.post("/api/auth/logout").status_code == 204

        client.cookies.set("paper_session", token)
        assert client.get("/api/dashboard").status_code == 401


def test_database_rejects_invalid_settings_even_outside_the_api(tmp_path: Path) -> None:
    database = tmp_path / "constraints.sqlite3"
    create_app(database)

    with sqlite3.connect(database) as connection:
        invalid_updates = (
            ("UPDATE run_settings SET starting_capital_ntd = ? WHERE id = 1", "0"),
            ("UPDATE run_settings SET max_position_allocation_pct = ? WHERE id = 1", "0"),
            ("UPDATE run_settings SET stop_loss_pct = ? WHERE id = 1", "100"),
            ("UPDATE run_settings SET take_profit_pct = ? WHERE id = 1", "0"),
            ("UPDATE run_settings SET daily_loss_limit_pct = ? WHERE id = 1", "100"),
            ("UPDATE run_settings SET fee_pct = ? WHERE id = 1", "-1"),
            ("UPDATE run_settings SET slippage_pct = ? WHERE id = 1", "10"),
        )
        for statement, value in invalid_updates:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, (value,))

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE run_settings SET max_position_allocation_pct='40', "
                "max_concurrent_positions=3 WHERE id=1"
            )


def test_settings_validation_and_running_settings_lock(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            tmp_path / "validation.sqlite3",
            market_data=FixtureMarketData(),
            clock=lambda: datetime(2026, 1, 8, 12, tzinfo=UTC),
        )
    ) as client:
        client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
        defaults = client.get("/api/settings")
        assert defaults.status_code == 200
        assert defaults.json()["starting_capital_ntd"] == "5000.00"
        assert defaults.json()["round_duration_days"] == 7

        invalid = client.put(
            "/api/settings",
            json={"max_position_allocation_pct": "40", "max_concurrent_positions": 3},
        )
        assert invalid.status_code == 422

        assert client.post("/api/run/start").status_code == 200
        locked = client.put("/api/settings", json={"starting_capital_ntd": "6000"})
        assert locked.status_code == 409
        assert locked.json()["detail"] == "stop the run before changing settings"
