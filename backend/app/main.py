from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response, status
from pwdlib import PasswordHash

from .database import Database, utc_now
from .engine import RoundPlanningError, RoundPlanningSettings, TradingEngine
from .market_data import BinanceMarketData, MarketData
from .schemas import PasswordInput, RunSettings
from .worker import TradingWorker

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


def create_app(
    database_path: Path | str = "data/paper-trading.sqlite3",
    market_data: MarketData | None = None,
    clock: Callable[[], datetime] | None = None,
    start_worker: bool | None = None,
) -> FastAPI:
    database = Database(Path(database_path))
    database.migrate()
    database.ensure_defaults()
    planning_clock = clock or (lambda: datetime.now(UTC))
    engine = TradingEngine(
        database, market_data or BinanceMarketData(clock=planning_clock), planning_clock
    )
    worker = TradingWorker(database, engine, planning_clock)
    should_start_worker = market_data is None if start_worker is None else start_worker

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        worker_thread: threading.Thread | None = None
        if should_start_worker:
            worker_thread = threading.Thread(
                target=worker.run_forever, name="paper-trading-worker"
            )
            worker_thread.start()
        yield
        if should_start_worker:
            worker.stop()
            if worker_thread is not None:
                worker_thread.join()

    app = FastAPI(title="Paper Trading Only", version="0.1.0", lifespan=lifespan)
    app.state.database = database
    app.state.engine = engine
    app.state.worker = worker

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
                daily_loss_limit_pct=?, fee_pct=?, slippage_pct=?, candle_interval=?,
                backtest_lookback_candles=?, minimum_liquidity_ntd=?, minimum_net_return_pct=?,
                minimum_entry_count=?, minimum_trade_count=?, max_conversion_age_seconds=?,
                max_candle_age_seconds=?, updated_at=? WHERE id=1""",
                (*values.values(), utc_now()),
            )
            connection.execute(
                "UPDATE trading_run SET current_capital_ntd=?, updated_at=? WHERE id=1",
                (str(payload.starting_capital_ntd), utc_now()),
            )
        return values

    def change_state(desired_state: RunState) -> dict[str, str]:
        with database.connect() as connection, connection:
            active_incident = connection.execute(
                "SELECT 1 FROM market_data_incidents WHERE active=1 LIMIT 1"
            ).fetchone()
            operational_state = (
                "degraded"
                if desired_state is RunState.RUNNING and active_incident is not None
                else desired_state.value
            )
            connection.execute(
                "UPDATE trading_run SET desired_state=?, operational_state=?, "
                "updated_at=? WHERE id=1",
                (desired_state.value, operational_state, utc_now()),
            )
        return {
            "desired_state": desired_state.value,
            "operational_state": operational_state,
        }

    @app.post("/api/run/start", dependencies=[Depends(authenticated)])
    def start() -> dict[str, object]:
        with database.connect() as connection:
            active = connection.execute(
                "SELECT id FROM trading_round WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if active is not None:
                selections = connection.execute(
                    "SELECT symbol FROM round_selections WHERE round_id=? ORDER BY selection_rank",
                    (active["id"],),
                ).fetchall()
                state = change_state(RunState.RUNNING)
                return {
                    **state,
                    "round_id": active["id"],
                    "selections": [selection["symbol"] for selection in selections],
                }
            row = connection.execute("SELECT * FROM run_settings WHERE id=1").fetchone()
        planning_settings = RoundPlanningSettings.from_mapping(dict(row))
        try:
            plan = engine.activate_round(planning_settings)
        except RoundPlanningError as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, f"round planning failed: {error}"
            ) from error
        state = change_state(RunState.RUNNING)
        return {
            **state,
            "round_id": plan.round_id,
            "selections": [selection.symbol for selection in plan.selections],
        }

    @app.post("/api/run/stop", dependencies=[Depends(authenticated)])
    def stop() -> dict[str, str]:
        return change_state(RunState.STOPPED)

    @app.get("/api/dashboard", dependencies=[Depends(authenticated)])
    def dashboard() -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute(
                """SELECT desired_state, operational_state, current_capital_ntd,
                starting_capital_ntd
                FROM trading_run JOIN run_settings ON run_settings.id = trading_run.id
                WHERE trading_run.id = 1"""
            ).fetchone()
            failure = connection.execute(
                "SELECT round_id, occurred_at, reason, active FROM planning_failures "
                "WHERE active=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            incident = connection.execute(
                """SELECT round_id, cause, occurred_at, retry_count, next_retry_at,
                recovered_at, active FROM market_data_incidents
                ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        active_incident = incident is not None and bool(incident["active"])
        health = "degraded" if failure or active_incident else "healthy"
        detail = incident["cause"] if active_incident else failure["reason"] if failure else None
        return {
            "product": "Paper Trading Only",
            "desired_state": row["desired_state"],
            "configured_capital_ntd": f"{float(row['starting_capital_ntd']):.2f}",
            "current_capital_ntd": f"{float(row['current_capital_ntd']):.2f}",
            "engine_health": health,
            "health": health,
            "health_detail": detail,
            "operational_state": row["operational_state"],
            "market_data_incident": dict(incident) if incident else None,
            "planning_failure": (
                {
                    "active": True,
                    "round_id": failure["round_id"],
                    "occurred_at": failure["occurred_at"],
                    "reason": failure["reason"],
                }
                if failure
                else None
            ),
        }

    return app


app = create_app(os.environ.get("TRADING_DATABASE_PATH", "data/paper-trading.sqlite3"))
