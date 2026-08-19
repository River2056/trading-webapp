from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.tests.test_paper_trading import active_engine


def authenticated_client(tmp_path: Path) -> tuple[TestClient, object, object, object]:
    engine, database, data = active_engine(tmp_path, settings={"max_concurrent_positions": 2})
    app = create_app(database.path, market_data=data, clock=lambda: data.now, start_worker=False)
    client = TestClient(app)
    client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
    return client, engine, database, data


def test_analytics_are_authenticated_and_project_persisted_capital_rules_and_series(
    tmp_path: Path,
) -> None:
    client, engine, _database, data = authenticated_client(tmp_path)
    assert TestClient(client.app).get("/api/analytics/charts").status_code == 401

    engine.evaluate_active_round()
    data.now += timedelta(minutes=5)
    data.observed_at = data.now
    data.prices["BTCUSDT"] *= 2
    data.histories["BTCUSDT"] = [data.prices["BTCUSDT"]] * 30
    data.histories["ETHUSDT"] = [data.prices["ETHUSDT"]] * 30
    engine.evaluate_active_round()

    dashboard = client.get("/api/dashboard").json()
    assert dashboard["initial_capital_ntd"] == "5000.00"
    assert float(dashboard["current_capital_ntd"]) > 5000
    assert float(dashboard["available_capital_ntd"]) < float(dashboard["current_capital_ntd"])
    assert float(dashboard["total_profit_ntd"]) > 0
    assert float(dashboard["total_profit_pct"]) > 0
    assert dashboard["profit_direction"] == "positive"
    assert float(dashboard["modeled_costs_ntd"]) > 0
    assert float(dashboard["estimated_liquidation_equity_ntd"]) < float(
        dashboard["current_capital_ntd"]
    )
    assert float(dashboard["estimated_liquidation_profit_ntd"]) < float(
        dashboard["total_profit_ntd"]
    )
    assert [pair["symbol"] for pair in dashboard["selected_pairs"]] == ["BTCUSDT", "ETHUSDT"]
    assert dashboard["selected_pairs"][0]["strategy_version"] == "rsi-v1"
    assert dashboard["risk_settings"]["max_concurrent_positions"] == 2

    charts = client.get("/api/analytics/charts").json()
    assert len(charts["equity"]) == 2
    assert charts["equity"][0]["at"] < charts["equity"][1]["at"]
    assert charts["profit"][0]["value_ntd"] != charts["profit"][1]["value_ntd"]
    assert charts["exposure"][0]["value_ntd"] == charts["equity"][0]["position_value_ntd"]
    assert charts["round_performance"] == []

    # Projections survive application reload because they are read only from SQLite.
    reloaded = create_app(client.app.state.database.path, market_data=data, clock=lambda: data.now,
                          start_worker=False)
    with TestClient(reloaded) as reload_client:
        reload_client.post("/api/auth/login", json={"password": "correct horse battery staple"})
        assert reload_client.get("/api/analytics/charts").json() == charts


def test_trade_history_search_filters_paginates_stably_and_validates_queries(
    tmp_path: Path,
) -> None:
    client, engine, _database, data = authenticated_client(tmp_path)
    engine.evaluate_active_round()
    data.now += timedelta(minutes=5)
    data.observed_at = data.now
    data.prices["BTCUSDT"] *= 2
    data.histories["BTCUSDT"] = [data.prices["BTCUSDT"]] * 30
    data.histories["ETHUSDT"] = [data.prices["ETHUSDT"]] * 30
    engine.evaluate_active_round()

    response = client.get("/api/history/trades", params={"q": "take-profit", "side": "sell",
                                                          "page": 1, "page_size": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1 and body["pages"] == 1
    trade = body["items"][0]
    assert trade["symbol"] == "BTCUSDT"
    assert trade["strategy_version"] == "rsi-v1"
    assert float(trade["realized_pnl_ntd"]) > 0
    assert trade["signal"]["market_evidence_json"]
    assert client.get("/api/history/trades", params={"side": "short"}).status_code == 422
    assert client.get("/api/history/trades", params={"page_size": 101}).status_code == 422


def test_round_and_cycle_history_expose_retrospective_audit_and_empty_state(tmp_path: Path) -> None:
    client, _engine, database, _data = authenticated_client(tmp_path)
    empty = client.get("/api/history/rounds").json()
    assert empty["total"] == 1
    assert empty["items"][0]["status"] == "active"
    assert empty["items"][0]["retrospective"] is None

    cycles = client.get("/api/history/cycles", params={"status": "active"}).json()
    assert cycles["total"] == 1
    assert cycles["items"][0]["round_count"] == 1
    assert cycles["items"][0]["retrospective"] is None
    assert client.get("/api/history/rounds", params={"status": "unknown"}).status_code == 422

    with database.connect() as connection:
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list('paper_trades')")}
    assert "idx_paper_trades_history" in indexes


def test_dashboard_ignores_prior_cycle_snapshot_after_bankruptcy_reset(tmp_path: Path) -> None:
    client, _engine, database, data = authenticated_client(tmp_path)
    with database.connect() as connection, connection:
        first_round = connection.execute("SELECT id FROM trading_round").fetchone()["id"]
        first_cycle = connection.execute("SELECT id FROM cycles").fetchone()["id"]
        connection.execute(
            "INSERT INTO portfolio_snapshots(round_id, interval_key, valued_at, cash_ntd, "
            "position_value_ntd, realized_pnl_ntd, unrealized_pnl_ntd, costs_ntd, "
            "available_capital_ntd, total_equity_ntd) VALUES(?, 'old', ?, '0', '0', "
            "'-5000', '0', '0', '0', '0')",
            (first_round, data.now.isoformat()),
        )
        connection.execute(
            "UPDATE trading_round SET status='completed', ended_at=?, ending_equity_ntd='0' "
            "WHERE id=?", (data.now.isoformat(), first_round),
        )
        connection.execute(
            "UPDATE cycles SET status='completed', ended_at=?, ending_capital_ntd='0', "
            "end_reason='bankruptcy' WHERE id=?", (data.now.isoformat(), first_cycle),
        )
        new_cycle = connection.execute(
            "INSERT INTO cycles(status, started_at, starting_capital_ntd) "
            "VALUES('active', ?, '5000')",
            (data.now.isoformat(),),
        ).lastrowid
        connection.execute(
            "INSERT INTO trading_round(status, started_at, frozen_settings_json, cycle_id) "
            "VALUES('active', ?, ?, ?)",
            (data.now.isoformat(), json.dumps({"starting_capital_ntd": "5000"}), new_cycle),
        )
        connection.execute("UPDATE trading_run SET current_capital_ntd='5000' WHERE id=1")

    dashboard = client.get("/api/dashboard").json()
    assert dashboard["current_capital_ntd"] == "5000.00"
    assert dashboard["available_capital_ntd"] == "5000.00"
    assert dashboard["current_cycle"]["id"] == new_cycle
    assert dashboard["current_cycle_starting_capital_ntd"] == "5000.00"


def test_dashboard_estimates_net_liquidation_value_from_persisted_open_positions(
    tmp_path: Path,
) -> None:
    client, engine, _database, _data = authenticated_client(tmp_path)

    result = engine.evaluate_active_round()

    dashboard = client.get("/api/dashboard").json()
    expected_liquidation = result.account.cash_ntd + result.account.position_value_ntd * (
        Decimal("1") - Decimal("0.20") / 100
    ) * (Decimal("1") - Decimal("0.10") / 100)
    assert dashboard["current_capital_ntd"] == f"{result.account.total_equity_ntd:.2f}"
    assert dashboard["modeled_costs_ntd"] == f"{result.account.costs_ntd:.2f}"
    assert dashboard["estimated_liquidation_equity_ntd"] == f"{expected_liquidation:.2f}"
    assert dashboard["estimated_liquidation_profit_ntd"] == f"{expected_liquidation - 5000:.2f}"


def test_dashboard_does_not_value_a_previous_round_snapshot_with_new_round_settings(
    tmp_path: Path,
) -> None:
    client, engine, database, data = authenticated_client(tmp_path)
    engine.evaluate_active_round()

    with database.connect() as connection, connection:
        old_round = connection.execute(
            "SELECT id, cycle_id FROM trading_round WHERE status='active'"
        ).fetchone()
        connection.execute(
            "UPDATE trading_round SET status='completed', ended_at=?, ending_equity_ntd='5100' "
            "WHERE id=?",
            (data.now.isoformat(), old_round["id"]),
        )
        new_settings = {
            "starting_capital_ntd": "5100",
            "fee_pct": "9",
            "slippage_pct": "9",
        }
        new_round = connection.execute(
            "INSERT INTO trading_round(status, started_at, frozen_settings_json, cycle_id) "
            "VALUES('active', ?, ?, ?)",
            (data.now.isoformat(), json.dumps(new_settings), old_round["cycle_id"]),
        ).lastrowid
        connection.execute("UPDATE trading_run SET current_capital_ntd='5100' WHERE id=1")

    dashboard = client.get("/api/dashboard").json()
    assert dashboard["current_round"]["id"] == new_round
    assert dashboard["current_capital_ntd"] == "5100.00"
    assert dashboard["modeled_costs_ntd"] == "0.00"
    assert dashboard["estimated_liquidation_equity_ntd"] == "5100.00"
    assert dashboard["estimated_liquidation_profit_ntd"] == "100.00"


def test_profit_chart_uses_each_round_frozen_baseline_not_mutable_defaults(tmp_path: Path) -> None:
    client, engine, database, data = authenticated_client(tmp_path)
    engine.evaluate_active_round()
    before = client.get("/api/analytics/charts").json()["profit"]
    assert before[0]["baseline_ntd"] == "5000"
    assert before[0]["round_id"]
    assert before[0]["cycle_id"]
    with database.connect() as connection, connection:
        connection.execute("UPDATE run_settings SET starting_capital_ntd='9000' WHERE id=1")
    after = client.get("/api/analytics/charts").json()["profit"]
    assert after == before


def test_trade_search_treats_like_metacharacters_as_literals(tmp_path: Path) -> None:
    client, engine, database, _data = authenticated_client(tmp_path)
    engine.evaluate_active_round()
    with database.connect() as connection, connection:
        trade_ids = [
            row["id"]
            for row in connection.execute("SELECT id FROM paper_trades ORDER BY id")
        ]
        assert len(trade_ids) >= 2
        connection.execute(
            "UPDATE paper_trades SET reason='literal%percent' WHERE id=?", (trade_ids[0],)
        )
        connection.execute(
            "UPDATE paper_trades SET reason='literal_under\\score' WHERE id=?", (trade_ids[1],)
        )
    assert client.get("/api/history/trades", params={"q": "%"}).json()["total"] == 1
    assert client.get("/api/history/trades", params={"q": "_"}).json()["total"] == 1
    assert client.get("/api/history/trades", params={"q": "\\"}).json()["total"] == 1


def test_filter_leading_history_indexes_are_available_to_query_planner(tmp_path: Path) -> None:
    _client, _engine, database, _data = authenticated_client(tmp_path)
    with database.connect() as connection:
        index_names = {
            row["name"]
            for table in ("paper_trades", "trading_round", "cycles")
            for row in connection.execute(f"PRAGMA index_list('{table}')")
        }
        assert {
            "idx_paper_trades_symbol_history", "idx_paper_trades_side_history",
            "idx_trading_round_status_history", "idx_trading_round_cycle_history",
            "idx_cycles_status_history",
        } <= index_names
        plan = " ".join(
            str(value)
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM paper_trades WHERE symbol='BTCUSDT' "
                "ORDER BY executed_at DESC, id DESC"
            )
            for value in row
        )
        assert "idx_paper_trades_symbol_history" in plan


def test_portfolio_snapshots_are_append_only_analytics_evidence(tmp_path: Path) -> None:
    import sqlite3

    _client, engine, database, _data = authenticated_client(tmp_path)
    engine.evaluate_active_round()
    with database.connect() as connection:
        for statement in (
            "UPDATE portfolio_snapshots SET total_equity_ntd='123.45'",
            "DELETE FROM portfolio_snapshots",
        ):
            try:
                connection.execute(statement)
            except sqlite3.IntegrityError:
                connection.rollback()
            else:
                raise AssertionError(f"snapshot immutability did not reject {statement}")

