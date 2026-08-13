from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.database import MIGRATIONS, Database
from backend.app.reporting import build_run_report

TABLES = (
    "cycles",
    "trading_round",
    "market_rankings",
    "backtest_results",
    "round_selections",
    "trading_signals",
    "paper_trades",
    "paper_positions",
    "portfolio_snapshots",
    "market_data_incidents",
    "worker_checkpoint",
    "round_retrospectives",
    "cycle_retrospectives",
    "bankruptcies",
    "lifecycle_transitions",
)


def test_v7_to_v8_preserves_complete_linked_history_and_adds_typed_incidents(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v7-history.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for migration in sorted(MIGRATIONS.glob("*.sql"))[:7]:
        version = int(migration.name.split("_", 1)[0])
        connection.executescript(migration.read_text())
        connection.execute(
            "INSERT INTO schema_migrations VALUES(?, '2026-01-01T00:00:00Z')", (version,)
        )
    connection.execute(
        "INSERT INTO run_settings(id,starting_capital_ntd,round_duration_days,"
        "strategy_cadence_seconds,max_position_allocation_pct,max_concurrent_positions,"
        "stop_loss_pct,take_profit_pct,daily_loss_limit_pct,fee_pct,slippage_pct,updated_at) "
        "VALUES(1,'5000',7,300,'10',3,'5','10','3','0.1','0.2','2026-01-01T00:00:00Z')"
    )
    connection.execute(
        "INSERT INTO trading_run(id,desired_state,current_capital_ntd,updated_at,"
        "operational_state) "
        "VALUES(1,'running','5100','2026-01-08T00:00:00Z','degraded')"
    )
    completed_cycle = connection.execute(
        "INSERT INTO cycles(status,started_at,starting_capital_ntd) "
        "VALUES('active','2026-01-01T00:00:00Z','5000')"
    ).lastrowid
    completed_round = connection.execute(
        "INSERT INTO trading_round(status,started_at,frozen_settings_json,cycle_id) "
        "VALUES('planning','2026-01-01T00:00:00Z',?,?)",
        (json.dumps({"starting_capital_ntd": "5000", "audit": "v7-completed"}), completed_cycle),
    ).lastrowid
    assert completed_round is not None and completed_cycle is not None
    for rank, symbol in enumerate(("BTCUSDT", "ETHUSDT"), 1):
        connection.execute(
            "INSERT INTO market_rankings(round_id,symbol,observed_at,base_asset,quote_asset,"
            "last_price,quote_volume,conversion_path,conversion_rate,conversion_observed_at,"
            "conversion_provenance_json,liquidity_ntd,score,rank,selected,exclusion_reason) "
            "VALUES(?,?,? ,?,'USDT','100','1000','USDT/TWD','32',?,'{}','32000','99',?,1,NULL)",
            (
                completed_round,
                symbol,
                "2026-01-01T00:00:00Z",
                symbol[:-4],
                "2026-01-01T00:00:00Z",
                rank,
            ),
        )
        connection.execute(
            "INSERT INTO backtest_results VALUES(?,?, 'rsi-v1','{\"fee\":\"0.1\"}',"
            "'{\"return\":\"2\"}',1,'2')",
            (completed_round, symbol),
        )
        connection.execute(
            "INSERT INTO round_selections VALUES(?,?,?,'rsi-v1','{\"period\":14}',"
            '\'{"fixture":"v7"}\')',
            (completed_round, symbol, rank),
        )
    connection.execute("UPDATE trading_round SET status='active' WHERE id=?", (completed_round,))
    connection.execute(
        "INSERT INTO trading_signals(round_id,symbol,interval_key,signal_id,evaluated_at,"
        "source_timestamp,strategy_version,action,outcome,reason,market_evidence_json) "
        "VALUES(?,'BTCUSDT','v7:1','v7-signal','2026-01-02T00:00:00Z',"
        "'2026-01-02T00:00:00Z','rsi-v1','buy','filled','linked v7 fill','{\"rsi\":20}')",
        (completed_round,),
    )
    connection.execute(
        "INSERT INTO paper_trades(round_id,signal_id,symbol,side,quantity,market_price_ntd,"
        "fill_price_ntd,notional_ntd,fee_ntd,slippage_ntd,executed_at,source_timestamp,"
        "strategy_version,reason,realized_pnl_ntd) VALUES(?,'v7-signal','BTCUSDT','buy','1',"
        "'100','101','101','0.101','1','2026-01-02T00:00:00Z','2026-01-02T00:00:00Z',"
        "'rsi-v1','linked v7 fill','0')",
        (completed_round,),
    )
    connection.execute(
        "INSERT INTO portfolio_snapshots(round_id,interval_key,valued_at,cash_ntd,"
        "position_value_ntd,realized_pnl_ntd,unrealized_pnl_ntd,costs_ntd,"
        "available_capital_ntd,total_equity_ntd) VALUES(?,'v7:1','2026-01-02T00:00:00Z',"
        "'4898.899','101','0','0','1.101','4898.899','4999.899')",
        (completed_round,),
    )
    connection.execute(
        "UPDATE trading_round SET status='completed',ended_at='2026-01-08T00:00:00Z',"
        "ending_equity_ntd='4999.899' WHERE id=?",
        (completed_round,),
    )
    connection.execute(
        "INSERT INTO round_retrospectives VALUES(?,'2026-01-08T00:00:00Z','5000','4999.899',"
        "'-0.00202','0.00202','1.101',1,0,0,0,'[\"BTCUSDT\"]','[\"rsi-v1\"]',"
        "'{\"linked\":true}','v7 round retrospective')",
        (completed_round,),
    )
    transition = connection.execute(
        "INSERT INTO lifecycle_transitions(cycle_id,completed_round_id,status,created_at,"
        "ending_equity_ntd,next_starting_capital_ntd,reason,completed_at) VALUES(?,?,'completed',"
        "'2026-01-08T00:00:00Z','4999.899','5000','bankruptcy reset',"
        "'2026-01-08T00:00:01Z')",
        (completed_cycle, completed_round),
    ).lastrowid
    connection.execute(
        "UPDATE cycles SET status='completed',ended_at='2026-01-08T00:00:00Z',"
        "ending_capital_ntd='4999.899',completed_round_count=1,end_reason='bankruptcy',"
        "evidence_json='{\"v7\":true}' WHERE id=?",
        (completed_cycle,),
    )
    connection.execute(
        "INSERT INTO cycle_retrospectives VALUES(?,'2026-01-08T00:00:00Z','5000','4999.899',1,"
        "'bankruptcy','{\"v7\":true}','v7 cycle retrospective')",
        (completed_cycle,),
    )
    connection.execute(
        "INSERT INTO bankruptcies(cycle_id,round_id,declared_at,ending_equity_ntd,"
        "completed_round_count,reason,evidence_json) VALUES(?,?,'2026-01-08T00:00:00Z',"
        "'4999.899',1,'v7 bankruptcy','{\"v7\":true}')",
        (completed_cycle, completed_round),
    )
    active_cycle = connection.execute(
        "INSERT INTO cycles(status,started_at,starting_capital_ntd) "
        "VALUES('active','2026-01-08T00:00:01Z','5000')"
    ).lastrowid
    active_round = connection.execute(
        "INSERT INTO trading_round(status,started_at,frozen_settings_json,cycle_id,"
        "lifecycle_transition_id) VALUES('planning','2026-01-08T00:00:01Z',"
        '\'{"starting_capital_ntd":"5000","audit":"v7-active"}\',?,?)',
        (active_cycle, transition),
    ).lastrowid
    connection.execute(
        "INSERT INTO round_selections VALUES(?,'SOLUSDT',1,'macd-v1','{\"fast\":12}',"
        '\'{"fixture":"active-v7"}\')',
        (active_round,),
    )
    connection.execute("UPDATE trading_round SET status='active' WHERE id=?", (active_round,))
    connection.execute(
        "INSERT INTO trading_signals(round_id,symbol,interval_key,signal_id,evaluated_at,"
        "source_timestamp,strategy_version,action,outcome,reason,market_evidence_json) "
        "VALUES(?,'SOLUSDT','v7:open','v7-open','2026-01-08T01:00:00Z',"
        "'2026-01-08T01:00:00Z','macd-v1','buy','filled','active linked fill','{}')",
        (active_round,),
    )
    connection.execute(
        "INSERT INTO paper_positions VALUES(?,'SOLUSDT','2','50','0.2',"
        "'2026-01-08T01:00:00Z','macd-v1','v7-open')",
        (active_round,),
    )
    connection.execute(
        "INSERT INTO market_data_incidents(round_id,cause,occurred_at,retry_count,next_retry_at) "
        "VALUES(?,'v7 provider incident','2026-01-08T02:00:00Z',2,'2026-01-08T02:01:00Z')",
        (active_round,),
    )
    connection.execute(
        "INSERT INTO worker_checkpoint VALUES(1,'2026-01-08T02:00:00Z',"
        "'2026-01-08T01:00:00Z','degraded','2026-01-08T02:00:00Z')"
    )
    connection.commit()
    before = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        for table in TABLES
    }
    linked_values = connection.execute(
        "SELECT tr.id,tr.cycle_id,rs.strategy_version,pt.reason FROM trading_round tr "
        "JOIN round_selections rs ON rs.round_id=tr.id "
        "JOIN paper_trades pt ON pt.round_id=tr.id "
        "WHERE tr.id=?",
        (completed_round,),
    ).fetchone()
    connection.close()

    database = Database(path)
    database.migrate()
    with database.connect() as upgraded:
        after = {
            table: upgraded.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in TABLES
        }
        assert before == after
        assert (
            upgraded.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()[
                -1
            ][0]
            == 8
        )
        assert (
            tuple(
                upgraded.execute(
                    "SELECT tr.id,tr.cycle_id,rs.strategy_version,pt.reason FROM trading_round tr "
                    "JOIN round_selections rs ON rs.round_id=tr.id "
                    "JOIN paper_trades pt ON pt.round_id=tr.id "
                    "WHERE tr.id=?",
                    (completed_round,),
                ).fetchone()
            )
            == linked_values
        )
        incident = upgraded.execute(
            "SELECT cause,incident_kind FROM market_data_incidents"
        ).fetchone()
        assert tuple(incident) == ("v7 provider incident", "market_data")
        report = build_run_report(upgraded, lambda: datetime(2026, 1, 9, tzinfo=UTC))
        assert "v7 bankruptcy" in report
        assert "v7 round retrospective" in report
        assert "active linked fill" in report
        with pytest.raises(sqlite3.IntegrityError):
            upgraded.execute("UPDATE bankruptcies SET reason='mutated'")
