from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Response, status
from pwdlib import PasswordHash

from .database import Database, utc_now
from .engine import RoundPlanningError, RoundPlanningSettings, TradingEngine
from .market_data import BinanceMarketData, MarketData
from .reporting import build_run_report
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
    control_lock = threading.Lock()
    should_start_worker = market_data is None if start_worker is None else start_worker

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        worker_thread: threading.Thread | None = None
        if should_start_worker:
            worker.prepare()
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
        with control_lock:
            return start_serialized()

    def start_serialized() -> dict[str, object]:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT id FROM trading_round WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if active is not None:
                selections = connection.execute(
                    "SELECT symbol FROM round_selections WHERE round_id=? ORDER BY selection_rank",
                    (active["id"],),
                ).fetchall()
                incident = connection.execute(
                    "SELECT 1 FROM market_data_incidents WHERE active=1 LIMIT 1"
                ).fetchone()
                operational_state = "degraded" if incident is not None else "running"
                connection.execute(
                    "UPDATE trading_run SET desired_state='running', operational_state=?, "
                    "updated_at=? WHERE id=1", (operational_state, utc_now())
                )
                connection.commit()
                return {
                    "desired_state": "running",
                    "operational_state": operational_state,
                    "round_id": active["id"],
                    "selections": [selection["symbol"] for selection in selections],
                }
            pending = connection.execute(
                "SELECT id FROM lifecycle_transitions WHERE status='pending_plan' LIMIT 1"
            ).fetchone()
            row = connection.execute("SELECT * FROM run_settings WHERE id=1").fetchone()
            if pending is not None:
                connection.execute(
                    "UPDATE trading_run SET desired_state='running', operational_state='running', "
                    "updated_at=? WHERE id=1", (utc_now(),)
                )
            connection.commit()
        if pending is not None:
            result = worker.step()
            if result.outcome in {"degraded", "backoff"}:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "round planning pending retry"
                )
            with database.connect() as connection:
                active = connection.execute(
                    "SELECT id FROM trading_round WHERE status='active' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if active is None:
                    raise HTTPException(
                        status.HTTP_503_SERVICE_UNAVAILABLE, "round planning remains pending"
                    )
                selections = connection.execute(
                    "SELECT symbol FROM round_selections WHERE round_id=? ORDER BY selection_rank",
                    (active["id"],),
                ).fetchall()
            return {
                "desired_state": "running", "operational_state": "running",
                "round_id": active["id"],
                "selections": [selection["symbol"] for selection in selections],
            }
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
        with control_lock:
            return change_state(RunState.STOPPED)

    @app.get("/api/analytics/charts", dependencies=[Depends(authenticated)])
    def analytics_charts() -> dict[str, list[dict[str, object]]]:
        with database.connect() as connection:
            snapshots = connection.execute(
                "SELECT ps.valued_at, ps.cash_ntd, ps.position_value_ntd, "
                "ps.realized_pnl_ntd, ps.unrealized_pnl_ntd, ps.available_capital_ntd, "
                "ps.total_equity_ntd, ps.round_id, tr.cycle_id, tr.frozen_settings_json, "
                "c.starting_capital_ntd cycle_starting_capital_ntd "
                "FROM portfolio_snapshots ps JOIN trading_round tr ON tr.id=ps.round_id "
                "LEFT JOIN cycles c ON c.id=tr.cycle_id ORDER BY ps.valued_at ASC, ps.id ASC"
            ).fetchall()
            rounds = connection.execute(
                "SELECT r.round_id, tr.cycle_id, r.created_at, r.starting_equity_ntd, "
                "r.ending_equity_ntd, r.starting_equity_ntd baseline_ntd, r.return_pct "
                "FROM round_retrospectives r JOIN trading_round tr ON tr.id=r.round_id "
                "ORDER BY r.created_at ASC, r.round_id ASC"
            ).fetchall()
        def snapshot_baseline(snapshot: sqlite3.Row) -> Decimal:
            frozen = json.loads(str(snapshot["frozen_settings_json"]))
            value = frozen.get("starting_capital_ntd") or snapshot["cycle_starting_capital_ntd"]
            return Decimal(str(value))

        equity = [
            {
                "at": row["valued_at"], "value_ntd": row["total_equity_ntd"],
                "cash_ntd": row["cash_ntd"], "position_value_ntd": row["position_value_ntd"],
                "round_id": row["round_id"], "cycle_id": row["cycle_id"],
            }
            for row in snapshots
        ]
        return {
            "equity": equity,
            "profit": [
                {
                    "at": row["valued_at"],
                    "value_ntd": format(
                        Decimal(str(row["total_equity_ntd"])) - snapshot_baseline(row), "f"
                    ),
                    "baseline_ntd": format(snapshot_baseline(row), "f"),
                    "round_id": row["round_id"], "cycle_id": row["cycle_id"],
                    "realized_ntd": row["realized_pnl_ntd"],
                    "unrealized_ntd": row["unrealized_pnl_ntd"],
                }
                for row in snapshots
            ],
            "exposure": [
                {
                    "at": row["valued_at"], "value_ntd": row["position_value_ntd"],
                    "available_ntd": row["available_capital_ntd"],
                    "round_id": row["round_id"], "cycle_id": row["cycle_id"],
                }
                for row in snapshots
            ],
            "round_performance": [dict(row) for row in rounds],
        }

    def page_result(
        connection: sqlite3.Connection,
        select_sql: str,
        count_sql: str,
        parameters: tuple[object, ...],
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        total = int(connection.execute(count_sql, parameters).fetchone()[0])
        rows = connection.execute(
            f"{select_sql} LIMIT ? OFFSET ?", (*parameters, page_size, (page - 1) * page_size)
        ).fetchall()
        return {
            "items": [dict(row) for row in rows], "page": page, "page_size": page_size,
            "total": total, "pages": math.ceil(total / page_size),
        }

    @app.get("/api/history/trades", dependencies=[Depends(authenticated)])
    def trade_history(
        q: Annotated[str | None, Query(max_length=100)] = None,
        symbol: Annotated[str | None, Query(pattern=r"^[A-Z0-9]{3,20}$")] = None,
        side: Literal["buy", "sell"] | None = None,
        round_id: Annotated[int | None, Query(ge=1)] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> dict[str, object]:
        clauses: list[str] = []
        values: list[object] = []
        if q:
            clauses.append(
                "(t.symbol LIKE ? ESCAPE '\\' OR t.reason LIKE ? ESCAPE '\\' "
                "OR t.strategy_version LIKE ? ESCAPE '\\')"
            )
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.extend([f"%{escaped}%"] * 3)
        if symbol:
            clauses.append("t.symbol=?")
            values.append(symbol)
        if side:
            clauses.append("t.side=?")
            values.append(side)
        if round_id:
            clauses.append("t.round_id=?")
            values.append(round_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        columns = (
            "t.id, t.round_id, t.signal_id, t.symbol, t.side, t.quantity, "
            "t.market_price_ntd, t.fill_price_ntd, t.notional_ntd, t.fee_ntd, "
            "t.slippage_ntd, t.executed_at, t.source_timestamp, t.strategy_version, "
            "t.reason, t.realized_pnl_ntd, s.action signal_action, s.outcome signal_outcome, "
            "s.market_evidence_json"
        )
        with database.connect() as connection:
            result = page_result(
                connection, f"SELECT {columns} FROM paper_trades t JOIN trading_signals s "  # noqa: S608
                f"ON s.signal_id=t.signal_id{where} ORDER BY t.executed_at DESC, t.id DESC",
                f"SELECT COUNT(*) FROM paper_trades t{where}",  # noqa: S608
                tuple(values), page, page_size,
            )
        items = cast(list[dict[str, object]], result["items"])
        for item in items:
            item["signal"] = {
                "action": item.pop("signal_action"), "outcome": item.pop("signal_outcome"),
                "market_evidence_json": item.pop("market_evidence_json"),
            }
        return result

    @app.get("/api/history/rounds", dependencies=[Depends(authenticated)])
    def round_history(
        status_filter: Annotated[
            Literal["planning", "active", "completed", "failed"] | None,
            Query(alias="status"),
        ] = None,
        cycle_id: Annotated[int | None, Query(ge=1)] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> dict[str, object]:
        clauses: list[str] = []
        values: list[object] = []
        if status_filter:
            clauses.append("tr.status=?")
            values.append(status_filter)
        if cycle_id:
            clauses.append("tr.cycle_id=?")
            values.append(cycle_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        columns = (
            "tr.*, rr.created_at retrospective_created_at, rr.return_pct, "
            "rr.max_drawdown_pct, rr.total_costs_ntd, rr.trade_count, rr.win_count, "
            "rr.loss_count, rr.rejected_action_count, rr.pairs_json, rr.strategies_json, "
            "rr.evidence_json, rr.summary"
        )
        with database.connect() as connection:
            result = page_result(
                connection, f"SELECT {columns} FROM trading_round tr LEFT JOIN "  # noqa: S608
                f"round_retrospectives rr ON rr.round_id=tr.id{where} "
                "ORDER BY tr.started_at DESC, tr.id DESC",
                f"SELECT COUNT(*) FROM trading_round tr{where}",  # noqa: S608
                tuple(values), page, page_size,
            )
        retrospective_keys = ("retrospective_created_at", "return_pct", "max_drawdown_pct",
            "total_costs_ntd", "trade_count", "win_count", "loss_count",
            "rejected_action_count", "pairs_json", "strategies_json", "evidence_json", "summary")
        items = cast(list[dict[str, object]], result["items"])
        for item in items:
            values_map = {key: item.pop(key) for key in retrospective_keys}
            item["frozen_settings"] = json.loads(str(item.pop("frozen_settings_json")))
            item["retrospective"] = values_map if values_map["retrospective_created_at"] else None
        return result

    @app.get("/api/history/cycles", dependencies=[Depends(authenticated)])
    def cycle_history(
        status_filter: Annotated[
            Literal["active", "completed"] | None, Query(alias="status")
        ] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> dict[str, object]:
        where = " WHERE c.status=?" if status_filter else ""
        values: tuple[object, ...] = (status_filter,) if status_filter else ()
        columns = (
            "c.*, COUNT(tr.id) round_count, cr.created_at retrospective_created_at, "
            "cr.reason retrospective_reason, cr.evidence_json retrospective_evidence_json, "
            "cr.summary retrospective_summary"
        )
        with database.connect() as connection:
            result = page_result(
                connection, f"SELECT {columns} FROM cycles c LEFT JOIN trading_round tr "  # noqa: S608
                "ON tr.cycle_id=c.id LEFT JOIN cycle_retrospectives cr ON cr.cycle_id=c.id"
                f"{where} GROUP BY c.id ORDER BY c.started_at DESC, c.id DESC",
                f"SELECT COUNT(*) FROM cycles c{where}",  # noqa: S608
                values, page, page_size,
            )
        items = cast(list[dict[str, object]], result["items"])
        for item in items:
            created = item.pop("retrospective_created_at")
            item["retrospective"] = ({
                "created_at": created, "reason": item.pop("retrospective_reason"),
                "evidence_json": item.pop("retrospective_evidence_json"),
                "summary": item.pop("retrospective_summary"),
            } if created else None)
            if not created:
                item.pop("retrospective_reason")
                item.pop("retrospective_evidence_json")
                item.pop("retrospective_summary")
        return result

    @app.get("/api/reports/run.md", dependencies=[Depends(authenticated)])
    def run_report() -> Response:
        with database.connect() as connection:
            report = build_run_report(connection, planning_clock)
        return Response(
            content=report,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="paper-trading-run-report.md"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/dashboard", dependencies=[Depends(authenticated)])
    def dashboard() -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute(
                """SELECT desired_state, operational_state, current_capital_ntd,
                starting_capital_ntd, terminal_state, terminal_detail
                FROM trading_run JOIN run_settings ON run_settings.id = trading_run.id
                WHERE trading_run.id = 1"""
            ).fetchone()
            failure = connection.execute(
                "SELECT round_id, occurred_at, reason, active FROM planning_failures "
                "WHERE active=1 UNION ALL SELECT NULL, occurred_at, reason, active "
                "FROM transition_planning_failures WHERE active=1 ORDER BY occurred_at DESC LIMIT 1"
            ).fetchone()
            incident = connection.execute(
                """SELECT round_id, cause, incident_kind, occurred_at, retry_count, next_retry_at,
                recovered_at, active FROM market_data_incidents
                ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            latest_round = connection.execute(
                "SELECT id, status, started_at, ended_at, ending_equity_ntd FROM trading_round "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            completed_round_count = int(connection.execute(
                "SELECT COUNT(*) FROM trading_round WHERE status='completed'"
            ).fetchone()[0])
            bankruptcy = connection.execute(
                "SELECT cycle_id, round_id, declared_at, ending_equity_ntd, "
                "completed_round_count, reason "
                "FROM bankruptcies ORDER BY id DESC LIMIT 1"
            ).fetchone()
            current_cycle = connection.execute(
                "SELECT id, status, started_at, starting_capital_ntd, completed_round_count "
                "FROM cycles WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            cycle_count = int(connection.execute("SELECT COUNT(*) FROM cycles").fetchone()[0])
            experiment_cycle = connection.execute(
                "SELECT starting_capital_ntd FROM cycles ORDER BY id ASC LIMIT 1"
            ).fetchone()
            latest_snapshot = connection.execute(
                "SELECT ps.* FROM portfolio_snapshots ps JOIN trading_round tr "
                "ON tr.id=ps.round_id WHERE tr.cycle_id=? "
                "ORDER BY ps.valued_at DESC, ps.id DESC LIMIT 1",
                (current_cycle["id"],),
            ).fetchone() if current_cycle else None
            active_round = connection.execute(
                "SELECT id, frozen_settings_json FROM trading_round WHERE status='active' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            selections = connection.execute(
                "SELECT symbol, selection_rank, strategy_version, strategy_config_json, "
                "backtest_provenance_json FROM round_selections WHERE round_id=? "
                "ORDER BY selection_rank",
                (active_round["id"],),
            ).fetchall() if active_round else []
        active_incident = incident is not None and bool(incident["active"])
        health = "degraded" if failure or active_incident else "healthy"
        detail = incident["cause"] if active_incident else failure["reason"] if failure else None
        configured = Decimal(str(row["starting_capital_ntd"]))
        experiment_initial = Decimal(str(
            experiment_cycle["starting_capital_ntd"] if experiment_cycle else configured
        ))
        cycle_initial = Decimal(str(
            current_cycle["starting_capital_ntd"] if current_cycle else row["current_capital_ntd"]
        ))
        current_value = (
            latest_snapshot["total_equity_ntd"]
            if latest_snapshot
            else row["current_capital_ntd"]
        )
        current = Decimal(str(current_value))
        available_value = latest_snapshot["available_capital_ntd"] if latest_snapshot else current
        available = Decimal(str(available_value))
        realized = Decimal(str(latest_snapshot["realized_pnl_ntd"] if latest_snapshot else "0"))
        unrealized = Decimal(str(latest_snapshot["unrealized_pnl_ntd"] if latest_snapshot else "0"))
        profit = current - cycle_initial
        profit_pct = profit / cycle_initial * 100 if cycle_initial else Decimal()
        direction = "positive" if profit > 0 else "negative" if profit < 0 else "neutral"
        return {
            "product": "Paper Trading Only",
            "desired_state": row["desired_state"],
            "configured_capital_ntd": f"{configured:.2f}",
            "initial_capital_ntd": f"{experiment_initial:.2f}",
            "experiment_initial_capital_ntd": f"{experiment_initial:.2f}",
            "current_cycle_starting_capital_ntd": f"{cycle_initial:.2f}",
            "current_capital_ntd": f"{current:.2f}",
            "available_capital_ntd": f"{available:.2f}",
            "realized_profit_ntd": f"{realized:.2f}",
            "unrealized_profit_ntd": f"{unrealized:.2f}",
            "total_profit_ntd": f"{profit:.2f}",
            "total_profit_pct": f"{profit_pct:.2f}",
            "profit_direction": direction,
            "selected_pairs": [
                {
                    **dict(selection),
                    "strategy_config": json.loads(str(selection["strategy_config_json"])),
                    "backtest_provenance": json.loads(str(selection["backtest_provenance_json"])),
                }
                for selection in selections
            ],
            "risk_settings": (
                json.loads(str(active_round["frozen_settings_json"])) if active_round else None
            ),
            "engine_health": health,
            "health": health,
            "health_detail": detail,
            "operational_state": row["operational_state"],
            "run_status": row["terminal_state"] or row["desired_state"],
            "round_status": latest_round["status"] if latest_round else None,
            "current_round": dict(latest_round) if latest_round else None,
            "completed_round_count": completed_round_count,
            "cycle_count": cycle_count,
            "current_cycle": dict(current_cycle) if current_cycle else None,
            "bankruptcy": dict(bankruptcy) if bankruptcy else None,
            "latest_bankruptcy": dict(bankruptcy) if bankruptcy else None,
            "days_since_bankruptcy": (
                max(
                    0,
                    (planning_clock() - datetime.fromisoformat(
                        str(bankruptcy["declared_at"]).replace("Z", "+00:00")
                    )).days,
                )
                if bankruptcy
                else None
            ),
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
