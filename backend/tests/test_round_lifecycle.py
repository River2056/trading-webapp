from __future__ import annotations

import json
import sqlite3
import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from backend.app.database import MIGRATIONS, Database
from backend.app.lifecycle import RoundLifecycle
from backend.tests.test_paper_trading import active_engine, rows
from backend.tests.test_round_planning import FixtureMarketData


def test_round_closes_at_persisted_duration_boundary_and_carries_equity(
    tmp_path: Path,
) -> None:
    engine, database, data = active_engine(tmp_path, settings={"round_duration_days": 7})
    lifecycle = RoundLifecycle(database, engine.market_data, lambda: data.now)

    data.now += timedelta(days=7) - timedelta(microseconds=1)
    data.observed_at = data.now
    assert lifecycle.close_due_round() is None

    data.now += timedelta(microseconds=1)
    data.observed_at = data.now
    result = lifecycle.close_due_round()

    assert result is not None
    assert result.outcome == "completed"
    completed = rows(database, "SELECT * FROM trading_round WHERE status='completed'")[0]
    assert completed["ended_at"] == data.now.isoformat()
    assert completed["ending_equity_ntd"] == "5000"
    assert rows(database, "SELECT * FROM round_retrospectives")[0]["trade_count"] == 0
    run = rows(database, "SELECT current_capital_ntd FROM trading_run")[0]
    assert run["current_capital_ntd"] == "5000"
    assert lifecycle.close_due_round() is None


def test_v5_migration_backfills_all_historical_rounds_into_one_active_cycle(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v5.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for migration in sorted(MIGRATIONS.glob("*.sql"))[:5]:
        version = int(migration.name.split("_", 1)[0])
        connection.executescript(migration.read_text())
        connection.execute(
            "INSERT INTO schema_migrations VALUES(?, '2026-01-01T00:00:00Z')", (version,)
        )
    connection.execute(
        "INSERT INTO run_settings(id, starting_capital_ntd, round_duration_days, "
        "strategy_cadence_seconds, max_position_allocation_pct, max_concurrent_positions, "
        "stop_loss_pct, take_profit_pct, daily_loss_limit_pct, fee_pct, slippage_pct, updated_at) "
        "VALUES(1, '5000', 7, 300, '10', 3, '5', '10', '3', '0.1', '0.1', "
        "'2026-01-01T00:00:00Z')"
    )
    connection.execute(
        "INSERT INTO trading_run(id, desired_state, current_capital_ntd, updated_at) "
        "VALUES(1, 'running', '5200', '2026-01-03T00:00:00Z')"
    )
    connection.executemany(
        "INSERT INTO trading_round(status, started_at, frozen_settings_json) VALUES(?, ?, '{}')",
        [("completed", "2026-01-01"), ("completed", "2026-01-02"),
         ("active", "2026-01-03")],
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.migrate()

    migrated = rows(database, "SELECT status, cycle_id FROM trading_round ORDER BY id")
    assert [row["status"] for row in migrated] == ["completed", "completed", "active"]
    assert {row["cycle_id"] for row in migrated} == {1}
    assert rows(database, "SELECT status, completed_round_count FROM cycles") == [
        {"status": "active", "completed_round_count": 2}
    ]


def test_rollover_liquidates_open_positions_with_fresh_prices_and_costs(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path, settings={"round_duration_days": 1})
    engine.evaluate_active_round()
    data.now += timedelta(days=1)
    data.observed_at = data.now

    result = RoundLifecycle(database, data, lambda: data.now).close_due_round()

    assert result is not None and result.outcome == "completed"
    assert rows(database, "SELECT * FROM paper_positions") == []
    trades = rows(database, "SELECT * FROM paper_trades ORDER BY id")
    assert [trade["side"] for trade in trades] == ["buy", "sell"]
    assert trades[-1]["reason"] == "deterministic round-end liquidation"
    retrospective = rows(database, "SELECT * FROM round_retrospectives")[0]
    assert retrospective["trade_count"] == 2
    assert Decimal(str(retrospective["total_costs_ntd"])) > 0


def test_unfundable_equity_resets_cycle_and_keeps_run_running(
    tmp_path: Path,
) -> None:
    engine, database, data = active_engine(
        tmp_path,
        settings={"round_duration_days": 1, "starting_capital_ntd": "0.000001"},
    )
    data.now += timedelta(days=1)
    data.observed_at = data.now

    result = RoundLifecycle(database, data, lambda: data.now).close_due_round()

    assert result is not None and result.outcome == "completed"
    run = rows(database, "SELECT * FROM trading_run")[0]
    assert run["terminal_state"] is None
    assert run["desired_state"] == "running"
    transition = rows(database, "SELECT * FROM lifecycle_transitions")[0]
    assert transition["status"] == "pending_plan"
    assert transition["completed_round_id"] == result.round_id
    assert RoundLifecycle(database, data, lambda: data.now).close_due_round() is None


def test_cycles_and_lifecycle_history_are_immutable(tmp_path: Path) -> None:
    import sqlite3
    engine, database, _data = active_engine(tmp_path)
    del engine
    with database.connect() as connection:
        for statement in ("DELETE FROM cycles",):
            try:
                connection.execute(statement)
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError(f"immutability trigger did not reject {statement}")


def test_worker_rollover_replans_with_carried_ending_equity(tmp_path: Path) -> None:
    from backend.app.engine import RoundPlan
    from backend.app.worker import TradingWorker

    engine, database, data = active_engine(tmp_path, settings={"round_duration_days": 1})
    data.now += timedelta(days=1)
    data.observed_at = data.now
    captured: list[Decimal] = []

    def activate(settings, **kwargs):
        del kwargs
        captured.append(settings.starting_capital_ntd)
        return RoundPlan(2, (), settings)

    engine.activate_round = activate  # type: ignore[method-assign]
    result = TradingWorker(database, engine, lambda: data.now).step()

    assert result.outcome == "rolled_over"
    assert captured == [Decimal("5000")]
    assert len(rows(database, "SELECT * FROM round_retrospectives")) == 1


def test_stop_after_close_leaves_transition_pending_until_start_resumes_it(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from backend.app.worker import TradingWorker

    engine, database, data = active_engine(tmp_path, settings={"round_duration_days": 1})
    data.now += timedelta(days=1)
    data.observed_at = data.now
    assert RoundLifecycle(database, data, lambda: data.now).close_due_round() is not None
    with database.connect() as connection, connection:
        connection.execute(
            "UPDATE trading_run SET desired_state='stopped', operational_state='stopped' WHERE id=1"
        )
    planning_data = FixtureMarketData()
    planning_data.observed_at = data.now
    engine.market_data = planning_data

    worker = TradingWorker(database, engine, lambda: data.now)
    assert worker.step().outcome == "stopped"
    assert rows(database, "SELECT status FROM lifecycle_transitions") == [
        {"status": "pending_plan"}
    ]
    assert rows(database, "SELECT * FROM trading_round WHERE status='active'") == []

    app = create_app(
        database.path, market_data=planning_data, clock=lambda: data.now, start_worker=False
    )
    with TestClient(app) as client:
        client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
        response = client.post("/api/run/start")

    assert response.status_code == 200
    assert response.json()["desired_state"] == "running"
    assert len(rows(database, "SELECT * FROM trading_round WHERE status='active'")) == 1
    assert len(rows(database, "SELECT * FROM trading_round")) == 2
    assert rows(database, "SELECT status FROM lifecycle_transitions") == [
        {"status": "completed"}
    ]


def test_start_during_pending_transition_backoff_returns_controlled_503(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    engine, database, data = active_engine(tmp_path, settings={"round_duration_days": 1})
    data.now += timedelta(days=1)
    data.observed_at = data.now
    RoundLifecycle(database, data, lambda: data.now).close_due_round()
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO market_data_incidents"
            "(round_id, cause, occurred_at, retry_count, next_retry_at) "
            "VALUES(NULL, 'planning unavailable', ?, 1, ?)",
            (data.now.isoformat(), (data.now + timedelta(minutes=5)).isoformat()),
        )
        connection.execute(
            "UPDATE trading_run SET desired_state='stopped', operational_state='stopped' WHERE id=1"
        )
    app = create_app(
        database.path, market_data=engine.market_data, clock=lambda: data.now, start_worker=False
    )

    with TestClient(app) as client:
        client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
        response = client.post("/api/run/start")

    assert response.status_code == 503
    assert response.json()["detail"] == "round planning pending retry"
    assert rows(database, "SELECT * FROM trading_round WHERE status='active'") == []
    assert rows(database, "SELECT status FROM lifecycle_transitions") == [
        {"status": "pending_plan"}
    ]


def test_dashboard_exposes_completed_round_and_bankruptcy_detail(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    engine, database, data = active_engine(
        tmp_path,
        settings={"round_duration_days": 1, "starting_capital_ntd": "0.000001"},
    )
    data.now += timedelta(days=1)
    data.observed_at = data.now
    RoundLifecycle(database, data, lambda: data.now).close_due_round()
    app = create_app(database.path, market_data=data, clock=lambda: data.now, start_worker=False)

    with TestClient(app) as client:
        client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
        dashboard = client.get("/api/dashboard").json()

    assert dashboard["run_status"] == "running"
    assert dashboard["round_status"] == "completed"
    assert dashboard["completed_round_count"] == 1
    assert dashboard["bankruptcy"] is None


def test_transition_activation_rolls_back_plan_and_retries_once_after_fault(tmp_path: Path) -> None:
    from backend.app.worker import TradingWorker

    engine, database, data = active_engine(tmp_path, settings={"round_duration_days": 1})
    data.now += timedelta(days=1)
    data.observed_at = data.now
    RoundLifecycle(database, data, lambda: data.now).close_due_round()
    planning_data = FixtureMarketData()
    planning_data.observed_at = data.now
    engine.market_data = planning_data

    def fail(stage: str) -> None:
        if stage == "after_plan_activation":
            raise RuntimeError("simulated crash")

    engine.fault_injector = fail
    worker = TradingWorker(database, engine, lambda: data.now)
    try:
        worker.step()
    except RuntimeError as error:
        assert str(error) == "simulated crash"
    else:
        raise AssertionError("fault injector did not interrupt activation")

    assert len(rows(database, "SELECT * FROM trading_round")) == 1
    assert rows(database, "SELECT status FROM lifecycle_transitions")[0]["status"] == "pending_plan"
    engine.fault_injector = None
    assert worker.step().outcome == "rolled_over"
    assert len(rows(database, "SELECT * FROM trading_round WHERE status='active'")) == 1
    assert len(rows(database, "SELECT * FROM trading_round")) == 2
    assert rows(database, "SELECT status FROM lifecycle_transitions")[0]["status"] == "completed"


def test_two_workers_cannot_create_duplicate_active_round_for_transition(tmp_path: Path) -> None:
    from backend.app.worker import TradingWorker

    engine, database, data = active_engine(tmp_path, settings={"round_duration_days": 1})
    data.now += timedelta(days=1)
    data.observed_at = data.now
    RoundLifecycle(database, data, lambda: data.now).close_due_round()
    planning_data = FixtureMarketData()
    planning_data.observed_at = data.now
    engine.market_data = planning_data
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            outcomes.append(TradingWorker(database, engine, lambda: data.now).step().outcome)
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=run), threading.Thread(target=run)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)

    assert errors == []
    assert sorted(outcomes) == ["advanced", "rolled_over"]
    assert len(rows(database, "SELECT * FROM trading_round WHERE status='active'")) == 1
    assert len(rows(database, "SELECT * FROM trading_round")) == 2


def test_bankruptcy_resets_cycle_to_configured_default_and_preserves_running_history(
    tmp_path: Path,
) -> None:
    from backend.app.worker import TradingWorker

    engine, database, data = active_engine(
        tmp_path,
        settings={"round_duration_days": 1, "starting_capital_ntd": "0.000001"},
    )
    with database.connect() as connection, connection:
        connection.execute("UPDATE run_settings SET starting_capital_ntd='5000' WHERE id=1")
    data.now += timedelta(days=1)
    data.observed_at = data.now
    planning_data = FixtureMarketData()
    planning_data.observed_at = data.now
    engine.market_data = planning_data

    assert TradingWorker(database, engine, lambda: data.now).step().outcome == "rolled_over"

    cycles = rows(database, "SELECT * FROM cycles ORDER BY id")
    assert [cycle["status"] for cycle in cycles] == ["completed", "active"]
    assert cycles[-1]["starting_capital_ntd"] == "5000"
    assert len(rows(database, "SELECT * FROM bankruptcies")) == 1
    assert len(rows(database, "SELECT * FROM cycle_retrospectives")) == 1
    assert rows(database, "SELECT desired_state, terminal_state FROM trading_run")[0] == {
        "desired_state": "running", "terminal_state": None,
    }
    active = rows(database, "SELECT * FROM trading_round WHERE status='active'")[0]
    assert json.loads(active["frozen_settings_json"])["starting_capital_ntd"] == "5000"
    with database.connect() as connection:
        for statement in (
            "UPDATE cycles SET end_reason='x' WHERE status='completed'",
            "DELETE FROM cycles WHERE status='completed'",
            "UPDATE cycle_retrospectives SET summary='x'",
            "DELETE FROM cycle_retrospectives",
            "UPDATE bankruptcies SET reason='x'",
            "DELETE FROM bankruptcies",
        ):
            try:
                connection.execute(statement)
            except sqlite3.IntegrityError:
                connection.rollback()
            else:
                raise AssertionError(f"immutability trigger did not reject {statement}")


def test_bankruptcy_history_survives_default_replan_failure_and_retry(
    tmp_path: Path,
) -> None:
    from backend.app.worker import TradingWorker

    engine, database, data = active_engine(
        tmp_path,
        settings={"round_duration_days": 1, "starting_capital_ntd": "0.000001"},
    )
    with database.connect() as connection, connection:
        connection.execute("UPDATE run_settings SET starting_capital_ntd='5000' WHERE id=1")
    data.now += timedelta(days=1)
    data.observed_at = data.now
    planning_data = FixtureMarketData(count=4)
    planning_data.observed_at = data.now
    engine.market_data = planning_data
    worker = TradingWorker(database, engine, lambda: data.now, base_backoff_seconds=0)

    assert worker.step().outcome == "degraded"
    assert len(rows(database, "SELECT * FROM bankruptcies")) == 1
    assert [row["status"] for row in rows(database, "SELECT status FROM cycles ORDER BY id")] == [
        "completed",
        "active",
    ]
    transition = rows(database, "SELECT status, reason FROM lifecycle_transitions")[0]
    assert transition["status"] == "pending_plan"
    assert str(transition["reason"]).startswith("bankruptcy reset:")
    assert rows(database, "SELECT * FROM trading_round WHERE status='active'") == []

    recovered = FixtureMarketData()
    recovered.observed_at = data.now
    engine.market_data = recovered
    assert worker.step().outcome == "rolled_over"
    assert len(rows(database, "SELECT * FROM bankruptcies")) == 1
    assert len(rows(database, "SELECT * FROM trading_round WHERE status='active'")) == 1


def test_liquidation_timestamps_and_performance_breakdowns_are_auditable(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path, settings={"round_duration_days": 1})
    engine.evaluate_active_round()
    data.now += timedelta(days=1)
    data.observed_at = data.now - timedelta(minutes=2)
    expected_source = data.observed_at.isoformat()

    RoundLifecycle(database, data, lambda: data.now).close_due_round()

    signal = rows(
        database, "SELECT * FROM trading_signals WHERE interval_key LIKE 'round-end:%'"
    )[0]
    trade = rows(database, "SELECT * FROM paper_trades WHERE side='sell'")[0]
    assert signal["evaluated_at"] == data.now.isoformat()
    assert trade["executed_at"] == data.now.isoformat()
    assert signal["source_timestamp"] == expected_source
    assert trade["source_timestamp"] == expected_source
    retrospective = rows(database, "SELECT * FROM round_retrospectives")[0]
    metrics = json.loads(retrospective["evidence_json"])
    pair = metrics["per_pair"][trade["symbol"]]
    strategy = metrics["per_strategy"][trade["strategy_version"]]
    assert set(pair) == {"trade_count", "realized_pnl_ntd", "costs_ntd", "wins", "losses"}
    assert set(strategy) == set(pair)
    assert "best" in retrospective["summary"] and "worst" in retrospective["summary"]


def test_all_completed_lifecycle_artifacts_reject_update_and_delete(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path, settings={"round_duration_days": 1})
    data.now += timedelta(days=1)
    data.observed_at = data.now
    RoundLifecycle(database, data, lambda: data.now).close_due_round()
    planning_data = FixtureMarketData()
    planning_data.observed_at = data.now
    engine.market_data = planning_data
    transition_id = rows(database, "SELECT id FROM lifecycle_transitions")[0]["id"]
    engine.activate_round(
        dict(rows(database, "SELECT * FROM run_settings WHERE id=1")[0]),
        transition_id=transition_id,
    )
    with database.connect() as connection:
        for statement in (
            "UPDATE trading_round SET ended_at='x' WHERE status='completed'",
            "DELETE FROM trading_round WHERE status='completed'",
            "UPDATE round_retrospectives SET summary='x'",
            "DELETE FROM round_retrospectives",
            "UPDATE lifecycle_transitions SET reason='x'",
            "DELETE FROM lifecycle_transitions",
        ):
            try:
                connection.execute(statement)
            except sqlite3.IntegrityError:
                connection.rollback()
            else:
                raise AssertionError(f"immutability trigger did not reject {statement}")
