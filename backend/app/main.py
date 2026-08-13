from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response, status
from pwdlib import PasswordHash

from .database import Database, utc_now
from .schemas import PasswordInput, RunSettings

SESSION_COOKIE = "paper_session"
_password_hash = PasswordHash.recommended()


class RunState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _set_session(response: Response, database: Database) -> None:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=7)
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO sessions VALUES (?, 1, ?, ?)",
            (
                _token_hash(token),
                utc_now(),
                expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
            ),
        )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=False,
        samesite="strict",
        max_age=7 * 24 * 60 * 60,
    )


def create_app(database_path: Path | str = "data/paper-trading.sqlite3") -> FastAPI:
    database = Database(Path(database_path))
    database.migrate()
    database.ensure_defaults()
    app = FastAPI(title="Paper Trading Only", version="0.1.0")
    app.state.database = database

    def authenticated(
        paper_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> None:
        if not paper_session:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
        with database.connect() as connection:
            row = connection.execute(
                "SELECT expires_at FROM sessions WHERE token_hash = ?",
                (_token_hash(paper_session),),
            ).fetchone()
        if row is None or row["expires_at"] <= utc_now():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired session")

    @app.post("/api/auth/signup", status_code=status.HTTP_201_CREATED)
    def signup(payload: PasswordInput, response: Response) -> dict[str, str]:
        try:
            with database.connect() as connection, connection:
                connection.execute(
                    "INSERT INTO operator_account VALUES (1, ?, ?)",
                    (_password_hash.hash(payload.password), utc_now()),
                )
        except sqlite3.IntegrityError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "local account already exists") from error
        _set_session(response, database)
        return {"status": "created"}

    @app.post("/api/auth/login")
    def login(payload: PasswordInput, response: Response) -> dict[str, str]:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM operator_account WHERE id = 1"
            ).fetchone()
        if row is None or not _password_hash.verify(payload.password, row["password_hash"]):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        _set_session(response, database)
        return {"status": "authenticated"}

    @app.post(
        "/api/auth/logout",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(authenticated)],
    )
    def logout(
        response: Response,
        paper_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        if paper_session:
            with database.connect() as connection, connection:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash = ?", (_token_hash(paper_session),)
                )
        response.delete_cookie(SESSION_COOKIE)
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @app.get("/api/settings", dependencies=[Depends(authenticated)])
    def get_settings() -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM run_settings WHERE id = 1").fetchone()
        return dict(row)

    @app.put("/api/settings", dependencies=[Depends(authenticated)])
    def update_settings(payload: RunSettings) -> dict[str, object]:
        values = payload.model_dump(mode="json")
        with database.connect() as connection, connection:
            state = connection.execute(
                "SELECT desired_state FROM trading_run WHERE id = 1"
            ).fetchone()[0]
            if state == "running":
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "stop the run before changing settings"
                )
            connection.execute(
                """UPDATE run_settings SET starting_capital_ntd=?, round_duration_days=?,
                strategy_cadence_seconds=?, max_position_allocation_pct=?,
                max_concurrent_positions=?, stop_loss_pct=?, take_profit_pct=?,
                daily_loss_limit_pct=?, fee_pct=?, slippage_pct=?, updated_at=? WHERE id=1""",
                (*values.values(), utc_now()),
            )
            connection.execute(
                "UPDATE trading_run SET current_capital_ntd=?, updated_at=? WHERE id=1",
                (str(payload.starting_capital_ntd), utc_now()),
            )
        return values

    def change_state(desired_state: RunState) -> dict[str, str]:
        with database.connect() as connection, connection:
            connection.execute(
                "UPDATE trading_run SET desired_state=?, updated_at=? WHERE id=1",
                (desired_state.value, utc_now()),
            )
        return {"desired_state": desired_state.value}

    @app.post("/api/run/start", dependencies=[Depends(authenticated)])
    def start() -> dict[str, str]:
        return change_state(RunState.RUNNING)

    @app.post("/api/run/stop", dependencies=[Depends(authenticated)])
    def stop() -> dict[str, str]:
        return change_state(RunState.STOPPED)

    @app.get("/api/dashboard", dependencies=[Depends(authenticated)])
    def dashboard() -> dict[str, str]:
        with database.connect() as connection:
            row = connection.execute(
                """SELECT desired_state, current_capital_ntd, starting_capital_ntd
                FROM trading_run JOIN run_settings ON run_settings.id = trading_run.id
                WHERE trading_run.id = 1"""
            ).fetchone()
        return {
            "product": "Paper Trading Only",
            "desired_state": row["desired_state"],
            "configured_capital_ntd": f"{float(row['starting_capital_ntd']):.2f}",
            "current_capital_ntd": f"{float(row['current_capital_ntd']):.2f}",
        }

    return app


app = create_app()
