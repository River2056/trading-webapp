from __future__ import annotations

# SQL fixtures and semantic contract strings remain explicit.
# ruff: noqa: E501
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.reporting import _data_cutoff, build_run_report
from backend.tests.test_analytics_api import authenticated_client

REPORT_TIME = datetime(2025, 2, 3, 4, 5, 6, tzinfo=UTC)


def test_report_download_is_authenticated_with_stable_contract_and_explicit_empty_sections(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "empty.sqlite3", clock=lambda: REPORT_TIME, start_worker=False)
    assert TestClient(app).get("/api/reports/run.md").status_code == 401

    client = TestClient(app)
    client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
    response = client.get("/api/reports/run.md")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="paper-trading-run-report.md"'
    )
    assert response.text.startswith("# Paper Trading Run Report\n")
    assert "**Mode:** PAPER TRADING ONLY — no real orders" in response.text
    assert "**Generated at:** 2025-02-03T04:05:06Z" in response.text
    assert "**Data cutoff:**" in response.text
    assert "**Data cutoff:** unavailable" not in response.text
    assert "## Current Status and Health" in response.text
    assert "## Current Cycle and Round" in response.text
    assert "No trading cycles have been persisted." in response.text
    assert "No trading rounds have been persisted." in response.text
    assert "## Full Trade Results" in response.text
    assert "No paper trades have been persisted." in response.text
    assert "## Rejected Decisions and Planning Evidence" in response.text
    assert "No rejected decisions or planning failures have been persisted." in response.text
    assert "## Completed Round, Cycle, Bankruptcy, and Reset Retrospectives" in response.text
    assert (
        "No completed-round, completed-cycle, bankruptcy, or reset retrospectives have been persisted."
        in response.text
    )


def test_active_report_is_complete_escaped_and_restart_deterministic(tmp_path: Path) -> None:
    client, engine, database, data = authenticated_client(tmp_path)
    data.now = REPORT_TIME
    data.observed_at = REPORT_TIME
    engine.evaluate_active_round()
    with database.connect() as connection, connection:
        connection.execute(
            "UPDATE paper_trades SET reason='audit | value *bold* [link](x) <tag>' WHERE id=(SELECT MIN(id) FROM paper_trades)"
        )
        connection.execute(
            "INSERT INTO trading_signals(round_id,symbol,interval_key,signal_id,evaluated_at,source_timestamp,strategy_version,action,outcome,reason,market_evidence_json) "
            "SELECT id,'BTCUSDT','reject-report','reject-report',?,?, 'rsi-v1','buy','rejected','risk | limit','{\"source\":\"fixture\"}' FROM trading_round WHERE status='active'",
            (REPORT_TIME.isoformat(), REPORT_TIME.isoformat()),
        )

    first = client.get("/api/reports/run.md").text
    assert "## Capital, Profit, Exposure, and Performance" in first
    assert "## Selected Pair Rankings, Strategies, Settings, and Backtests" in first
    assert "BTCUSDT" in first and "rsi\\-v1" in first
    assert "audit \\| value \\*bold\\* \\[link\\]\\(x\\) &lt;tag&gt;" in first
    assert "risk \\| limit" in first
    assert "Binance public market data" in first
    assert "The complete persisted run audit is included" in first

    with database.connect() as connection, connection:
        connection.execute("UPDATE run_settings SET starting_capital_ntd='9000' WHERE id=1")
    changed_defaults = client.get("/api/reports/run.md").text
    assert "**Configured starting capital (mutable default):** 9000.00 NTD" in changed_defaults
    assert (
        "**Experiment initial capital (first persisted cycle, immutable history):** 5000.00 NTD"
        in changed_defaults
    )
    assert "### Round 1 frozen settings" in changed_defaults

    reloaded = create_app(
        database.path, market_data=data, clock=lambda: REPORT_TIME, start_worker=False
    )
    with TestClient(reloaded) as reload_client:
        reload_client.post("/api/auth/login", json={"password": "correct horse battery staple"})
        assert reload_client.get("/api/reports/run.md").text == changed_defaults

    data.now += timedelta(seconds=1)
    changed_clock = create_app(
        database.path, market_data=data, clock=lambda: data.now, start_worker=False
    )
    with TestClient(changed_clock) as changed_client:
        changed_client.post("/api/auth/login", json={"password": "correct horse battery staple"})
        second = changed_client.get("/api/reports/run.md").text
    assert second.replace("2025-02-03T04:05:07Z", "2025-02-03T04:05:06Z") == changed_defaults


def test_report_current_capital_ignores_prior_cycle_snapshots_after_reset(tmp_path: Path) -> None:
    client, _engine, database, data = authenticated_client(tmp_path)
    with database.connect() as connection, connection:
        first_round = connection.execute("SELECT id,cycle_id FROM trading_round").fetchone()
        connection.execute(
            "INSERT INTO portfolio_snapshots(round_id,interval_key,valued_at,cash_ntd,"
            "position_value_ntd,realized_pnl_ntd,unrealized_pnl_ntd,costs_ntd,"
            "available_capital_ntd,total_equity_ntd) VALUES(?,'bankrupt',?,'0','0','-5000',"
            "'0','0','0','0')", (first_round["id"], data.now.isoformat()),
        )
        connection.execute(
            "UPDATE trading_round SET status='completed',ended_at=?,ending_equity_ntd='0' WHERE id=?",
            (data.now.isoformat(), first_round["id"]),
        )
        connection.execute(
            "UPDATE cycles SET status='completed',ended_at=?,ending_capital_ntd='0',"
            "end_reason='bankruptcy' WHERE id=?", (data.now.isoformat(), first_round["cycle_id"]),
        )
        cycle_id = connection.execute(
            "INSERT INTO cycles(status,started_at,starting_capital_ntd) VALUES('active',?,'5000')",
            (data.now.isoformat(),),
        ).lastrowid
        connection.execute(
            "INSERT INTO trading_round(status,started_at,frozen_settings_json,cycle_id) "
            "VALUES('active',?,'{\"starting_capital_ntd\":\"5000\"}',?)",
            (data.now.isoformat(), cycle_id),
        )
        connection.execute("UPDATE trading_run SET current_capital_ntd='5000' WHERE id=1")

    report = client.get("/api/reports/run.md").text
    assert "**Current equity:** 5000.00 NTD" in report
    assert "**Available capital:** 5000.00 NTD" in report
    assert "**Current-cycle profit:** 0.00 NTD" in report


def test_data_cutoff_compares_offset_timestamps_as_instants_and_renders_utc(tmp_path: Path) -> None:
    _client, _engine, database, _data = authenticated_client(tmp_path)
    with database.connect() as connection, connection:
        round_id = connection.execute("SELECT id FROM trading_round").fetchone()[0]
        connection.execute(
            "INSERT INTO planning_failures(round_id,occurred_at,reason,active) VALUES(?,?,'offset',0)",
            (round_id, "2030-01-01T12:30:00+02:00"),
        )
        connection.execute("UPDATE run_settings SET updated_at='2030-01-01T11:00:00Z'")
        assert _data_cutoff(connection) == "2030-01-01T11:00:00Z"
        connection.execute("UPDATE planning_failures SET occurred_at='not-a-timestamp'")
        try:
            _data_cutoff(connection)
        except ValueError as error:
            assert "malformed persisted timestamp" in str(error)
        else:
            raise AssertionError("malformed persisted audit timestamps must be rejected")


def test_report_uses_one_wal_read_snapshot_when_writer_inserts_mid_generation(tmp_path: Path) -> None:
    client, _engine, database, _data = authenticated_client(tmp_path)
    with database.connect() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    inserted_at = "2030-01-01T00:00:00Z"

    def insert_after_snapshot_is_pinned() -> None:
        with database.connect() as writer, writer:
            round_id = writer.execute(
                "SELECT id FROM trading_round WHERE status='active'"
            ).fetchone()[0]
            writer.execute(
                "INSERT INTO trading_signals(round_id,symbol,interval_key,signal_id,evaluated_at,"
                "source_timestamp,strategy_version,action,outcome,reason,market_evidence_json) "
                "VALUES(?,'BTCUSDT','concurrent','concurrent-report',?,?,'rsi-v1','hold',"
                "'observed','inserted during report','{}')",
                (round_id, inserted_at, inserted_at),
            )

    with database.connect() as connection:
        report = build_run_report(
            connection, lambda: REPORT_TIME, after_snapshot_pinned=insert_after_snapshot_is_pinned
        )
    assert "concurrent\\-report" not in report
    assert inserted_at not in report
    assert "**Data cutoff:** 2030-01-01T00:00:00Z" in client.get("/api/reports/run.md").text


def test_report_exports_complete_cycle_round_signal_position_and_recovery_audit(tmp_path: Path) -> None:
    from backend.app.worker import TradingWorker
    from backend.tests.test_paper_trading import active_engine
    from backend.tests.test_round_planning import FixtureMarketData

    engine, database, data = active_engine(
        tmp_path, settings={"round_duration_days": 1, "starting_capital_ntd": "0.000001"}
    )
    with database.connect() as connection, connection:
        connection.execute("UPDATE run_settings SET starting_capital_ntd='5000' WHERE id=1")
        connection.execute(
            "INSERT INTO trading_signals(round_id,symbol,interval_key,signal_id,evaluated_at,"
            "source_timestamp,strategy_version,action,outcome,reason,market_evidence_json) "
            "SELECT id,'BTCUSDT','semantic','semantic-rejected',?,?,'rsi-v1','buy','rejected',"
            "'insufficient capital','{\"source\":\"Binance\"}' FROM trading_round WHERE status='active'",
            (data.now.isoformat(), data.observed_at.isoformat()),
        )
        connection.execute(
            "INSERT INTO market_data_incidents(round_id,cause,occurred_at,recovered_at,active,"
            "retry_count,next_retry_at) SELECT id,'temporary source outage',?,?,0,1,? "
            "FROM trading_round WHERE status='active'",
            (data.now.isoformat(), (data.now + timedelta(minutes=1)).isoformat(), data.now.isoformat()),
        )
    data.now += timedelta(days=1)
    data.observed_at = data.now
    replacement = FixtureMarketData()
    replacement.observed_at = data.now
    engine.market_data = replacement
    assert TradingWorker(database, engine, lambda: data.now).step().outcome == "rolled_over"

    with database.connect() as connection:
        cycle_rows = [dict(row) for row in connection.execute("SELECT * FROM cycles ORDER BY id")]
        report = build_run_report(connection, lambda: REPORT_TIME)
    end_reason = str(cycle_rows[0]["end_reason"])
    assert "minimum executable quantity" in end_reason
    assert "## Trading Cycles (complete)" in report
    assert "**End Reason:** all otherwise\\-qualified candidates are below minimum executable quantity" in report
    assert "**Completed Round Count:** 1" in report
    assert "## Trading Rounds (complete)" in report
    assert "**Status:** completed" in report and "**Ended At:**" in report
    assert "## Trading Signals (complete)" in report
    assert "**Market Evidence Json:**" in report and "**Source Timestamp:**" in report
    assert "## Current Paper Positions (complete)" in report
    assert "No current paper positions have been persisted." in report
    assert "### Completed round 1 retrospective" in report
    assert "### Completed cycle 1 retrospective" in report
    assert "### Bankruptcy 1" in report
    assert "### Round/cycle transition 1" in report
    assert "temporary source outage" in report
    assert "**Recovered At:**" in report
    assert "authentication, sessions, migration metadata, and internal schema" in report
