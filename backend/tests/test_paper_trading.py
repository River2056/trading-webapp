from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.database import Database
from backend.app.engine import RoundPlanningError, TradingEngine
from backend.app.market_data import Candle, MarketDataError, MarketSummary, NtdConversion


class ExecutionMarketData:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.observed_at = now
        self.prices = {"BTCUSDT": Decimal("100"), "ETHUSDT": Decimal("50")}
        self.quote_assets = {"BTCUSDT": "USDT", "ETHUSDT": "USDT"}
        self.conversions = {"USDT": Decimal("30")}
        self.conversion_observed_at: dict[str, datetime] = {}
        self.conversion_errors: set[str] = set()
        self.candle_errors: set[str] = set()
        self.histories: dict[str, list[Decimal]] = {
            "BTCUSDT": list(map(Decimal, range(30, 14, -1))),
            "ETHUSDT": list(map(Decimal, range(30, 14, -1))),
        }

    def market_summaries(self) -> list[MarketSummary]:
        return [
            MarketSummary(
                symbol,
                symbol.removesuffix("USDT"),
                self.quote_assets[symbol],
                price,
                Decimal("1000000"),
                self.observed_at,
            )
            for symbol, price in self.prices.items()
        ]

    def ntd_conversion(self, quote_asset: str) -> NtdConversion:
        if quote_asset in self.conversion_errors:
            raise MarketDataError(f"conversion unavailable for {quote_asset}")
        return NtdConversion(
            quote_asset,
            self.conversions[quote_asset],
            f"{quote_asset}/TWD fixture",
            self.conversion_observed_at.get(quote_asset, self.observed_at),
        )

    def historical_candles(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        if symbol in self.candle_errors:
            raise MarketDataError(f"candles unavailable for {symbol}")
        values = self.histories[symbol]
        if len(values) < limit:
            values = [values[0]] * (limit - len(values)) + values
        start = self.now - timedelta(hours=len(values) - 1)
        return [
            Candle(start + timedelta(hours=index), value, value, value, value, Decimal("1000"))
            for index, value in enumerate(values)
        ]


def active_engine(
    tmp_path: Path,
    *,
    settings: dict[str, object] | None = None,
    selection_order: tuple[str, str] = ("BTCUSDT", "ETHUSDT"),
) -> tuple[TradingEngine, Database, ExecutionMarketData]:
    now = datetime(2026, 1, 2, 12, tzinfo=UTC)
    database = Database(tmp_path / "paper.sqlite3")
    database.migrate()
    database.ensure_defaults()
    frozen: dict[str, object] = {
        "candle_interval": "1h",
        "backtest_lookback_candles": 80,
        "minimum_liquidity_ntd": "1000000",
        "fee_pct": "0.10",
        "slippage_pct": "0.20",
        "max_position_allocation_pct": "10",
        "max_concurrent_positions": 1,
        "stop_loss_pct": "5",
        "take_profit_pct": "10",
        "daily_loss_limit_pct": "3",
        "minimum_net_return_pct": "0",
        "minimum_entry_count": 1,
        "minimum_trade_count": 1,
        "max_conversion_age_seconds": 300,
        "max_candle_age_seconds": 300,
        "strategy_cadence_seconds": 300,
        "starting_capital_ntd": "5000",
    }
    frozen.update(settings or {})
    with database.connect() as connection, connection:
        cursor = connection.execute(
            "INSERT INTO trading_round"
            "(status, started_at, frozen_settings_json) VALUES('planning', ?, ?)",
            (now.isoformat(), json.dumps(frozen)),
        )
        round_id = cursor.lastrowid
        assert round_id is not None
        for rank, symbol in enumerate(selection_order, 1):
            connection.execute(
                "INSERT INTO round_selections VALUES (?, ?, ?, 'rsi-v1', ?, '{}')",
                (
                    round_id,
                    symbol,
                    rank,
                    json.dumps({"period": 14, "entry_below": 30, "exit_above": 70}),
                ),
            )
        connection.execute("UPDATE trading_round SET status='active' WHERE id=?", (round_id,))
        connection.execute("UPDATE trading_run SET desired_state='running' WHERE id=1")
    data = ExecutionMarketData(now)
    return TradingEngine(database, data, lambda: data.now), database, data


def rows(database: Database, statement: str) -> list[dict[str, object]]:
    with database.connect() as connection:
        return [dict(row) for row in connection.execute(statement).fetchall()]


def test_stopped_run_cannot_evaluate_or_fill_active_round(tmp_path: Path) -> None:
    engine, database, _data = active_engine(tmp_path)
    with database.connect() as connection, connection:
        connection.execute("UPDATE trading_run SET desired_state='stopped' WHERE id=1")

    with pytest.raises(RoundPlanningError, match="stopped"):
        engine.evaluate_active_round()

    assert rows(database, "SELECT * FROM trading_signals") == []
    assert rows(database, "SELECT * FROM paper_trades") == []


def test_fresh_entry_fill_records_evidence_costs_and_reconciled_ntd_accounting(
    tmp_path: Path,
) -> None:
    engine, database, _data = active_engine(tmp_path)

    result = engine.evaluate_active_round()

    assert [
        (decision.symbol, decision.action, decision.outcome) for decision in result.decisions
    ] == [
        ("BTCUSDT", "buy", "filled"),
        ("ETHUSDT", "buy", "rejected"),
    ]
    signals = rows(database, "SELECT * FROM trading_signals ORDER BY symbol")
    assert signals[0]["strategy_version"] == "rsi-v1"
    assert signals[0]["market_evidence_json"]
    assert signals[0]["source_timestamp"] == "2026-01-02T12:00:00+00:00"
    assert signals[1]["reason"] == "maximum concurrent positions reached"
    trades = rows(database, "SELECT * FROM paper_trades")
    assert len(trades) == 1
    assert {
        key: trades[0][key]
        for key in (
            "symbol",
            "side",
            "quantity",
            "market_price_ntd",
            "fill_price_ntd",
            "fee_ntd",
            "slippage_ntd",
        )
    } == {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": "0.166167",
        "market_price_ntd": "3000",
        "fill_price_ntd": "3006",
        "fee_ntd": "0.499498002",
        "slippage_ntd": "0.997002",
    }
    account = result.account
    assert account.cash_ntd == Decimal("4500.002499998")
    assert account.position_value_ntd == Decimal("498.501")
    assert account.available_capital_ntd == account.cash_ntd
    assert account.total_equity_ntd == Decimal("4998.503499998")
    assert account.total_equity_ntd == account.cash_ntd + account.position_value_ntd
    assert account.costs_ntd == Decimal("1.496500002")

    duplicate = engine.evaluate_active_round()
    assert duplicate.decisions == ()
    assert len(rows(database, "SELECT * FROM paper_trades")) == 1
    assert duplicate.account == account


def test_below_minimum_executable_quantity_rejects_symbol_without_rolling_back_interval(
    tmp_path: Path,
) -> None:
    engine, database, data = active_engine(
        tmp_path, settings={"max_concurrent_positions": 2}
    )
    data.prices["BTCUSDT"] = Decimal("1000000000000")

    result = engine.evaluate_active_round()

    btc = next(decision for decision in result.decisions if decision.symbol == "BTCUSDT")
    eth = next(decision for decision in result.decisions if decision.symbol == "ETHUSDT")
    assert (btc.outcome, btc.reason) == (
        "rejected",
        "below minimum executable quantity",
    )
    assert eth.outcome == "filled"
    assert len(rows(database, "SELECT * FROM trading_signals")) == 2
    assert len(rows(database, "SELECT * FROM paper_trades")) == 1
    assert len(rows(database, "SELECT * FROM portfolio_snapshots")) == 1


def test_stop_loss_exit_and_daily_loss_rejection_are_auditable_and_reconcile(
    tmp_path: Path,
) -> None:
    engine, database, data = active_engine(
        tmp_path,
        settings={"stop_loss_pct": "1", "daily_loss_limit_pct": "0.1"},
    )
    opened = engine.evaluate_active_round()
    assert opened.decisions[0].outcome == "filled"

    data.now += timedelta(minutes=5)
    data.prices["BTCUSDT"] = Decimal("95")
    data.histories["BTCUSDT"] = [Decimal("95")] * 30
    data.histories["ETHUSDT"] = list(map(Decimal, range(30, 14, -1)))
    closed = engine.evaluate_active_round()

    assert closed.decisions[0].reason == "stop-loss threshold reached"
    assert closed.decisions[0].outcome == "filled"
    assert closed.decisions[1].reason == "daily loss limit reached"
    trades = rows(database, "SELECT * FROM paper_trades ORDER BY id")
    assert [trade["side"] for trade in trades] == ["buy", "sell"]
    assert Decimal(str(trades[1]["realized_pnl_ntd"])) < Decimal("-5")
    assert closed.account.position_value_ntd == 0
    assert closed.account.total_equity_ntd == closed.account.cash_ntd
    assert closed.account.realized_pnl_ntd == Decimal(str(trades[1]["realized_pnl_ntd"]))


def test_same_interval_exit_loss_blocks_earlier_ranked_entry(tmp_path: Path) -> None:
    engine, database, data = active_engine(
        tmp_path,
        settings={
            "max_concurrent_positions": 2,
            "stop_loss_pct": "1",
            "daily_loss_limit_pct": "0.1",
        },
        selection_order=("ETHUSDT", "BTCUSDT"),
    )
    data.histories["ETHUSDT"] = [Decimal("50")] * 30
    opened = engine.evaluate_active_round()
    assert (
        next(decision for decision in opened.decisions if decision.symbol == "BTCUSDT").outcome
        == "filled"
    )

    data.now += timedelta(minutes=5)
    data.observed_at = data.now
    data.prices["BTCUSDT"] = Decimal("95")
    data.histories["BTCUSDT"] = [Decimal("95")] * 30
    data.histories["ETHUSDT"] = list(map(Decimal, range(30, 14, -1)))

    result = engine.evaluate_active_round()

    btc = next(decision for decision in result.decisions if decision.symbol == "BTCUSDT")
    eth = next(decision for decision in result.decisions if decision.symbol == "ETHUSDT")
    assert (btc.outcome, btc.reason) == ("filled", "stop-loss threshold reached")
    assert (eth.outcome, eth.reason) == ("rejected", "daily loss limit reached")
    assert rows(database, "SELECT * FROM paper_positions") == []
    interval_signals = rows(
        database,
        "SELECT symbol FROM trading_signals ORDER BY id DESC LIMIT 2",
    )
    assert [row["symbol"] for row in reversed(interval_signals)] == ["BTCUSDT", "ETHUSDT"]


def test_take_profit_exits_even_without_strategy_exit_signal(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path, settings={"take_profit_pct": "1"})
    engine.evaluate_active_round()

    data.now += timedelta(minutes=5)
    data.observed_at = data.now
    data.prices["BTCUSDT"] = Decimal("105")
    data.histories["BTCUSDT"] = [Decimal("105")] * 30
    data.histories["ETHUSDT"] = [Decimal("50")] * 30
    result = engine.evaluate_active_round()

    assert result.decisions[0].outcome == "filled"
    assert result.decisions[0].reason == "take-profit threshold reached"
    sides = [trade["side"] for trade in rows(database, "SELECT * FROM paper_trades ORDER BY id")]
    assert sides == [
        "buy",
        "sell",
    ]
    assert result.account.realized_pnl_ntd > 0


def test_stale_prices_never_fill_and_record_rejection_evidence(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path)
    data.now += timedelta(hours=1)

    result = engine.evaluate_active_round()

    assert all(decision.outcome == "rejected" for decision in result.decisions)
    assert all("stale" in decision.reason for decision in result.decisions)
    assert rows(database, "SELECT * FROM paper_trades") == []
    signals = rows(database, "SELECT * FROM trading_signals")
    assert all(signal["market_evidence_json"] for signal in signals)


def test_missing_summary_marks_market_source_unavailable_without_claiming_evaluation_time(
    tmp_path: Path,
) -> None:
    engine, database, data = active_engine(tmp_path)
    del data.prices["ETHUSDT"]

    engine.evaluate_active_round()

    signal = rows(database, "SELECT * FROM trading_signals WHERE symbol='ETHUSDT'")[0]
    evidence = json.loads(str(signal["market_evidence_json"]))
    assert signal["source_timestamp"] == signal["evaluated_at"]
    assert evidence["source_timestamp_available"] is False
    assert evidence["source_timestamp_unavailable_reason"] == "market summary unavailable"


def _eth_entry_quantity_after_btc_appreciates(tmp_path: Path, order: tuple[str, str]) -> Decimal:
    engine, database, data = active_engine(
        tmp_path, settings={"max_concurrent_positions": 2}, selection_order=order
    )
    data.histories["ETHUSDT"] = [Decimal("50")] * 30
    engine.evaluate_active_round()
    data.now += timedelta(minutes=5)
    data.observed_at = data.now
    data.prices["BTCUSDT"] = Decimal("200")
    data.histories["BTCUSDT"] = [Decimal("200")] * 30
    data.histories["ETHUSDT"] = list(map(Decimal, range(30, 14, -1)))

    engine.evaluate_active_round()

    return Decimal(
        str(
            rows(database, "SELECT quantity FROM paper_positions WHERE symbol='ETHUSDT'")[0][
                "quantity"
            ]
        )
    )


def test_selection_order_does_not_change_sizing_when_existing_position_appreciates(
    tmp_path: Path,
) -> None:
    btc_first = _eth_entry_quantity_after_btc_appreciates(
        tmp_path / "btc-first", ("BTCUSDT", "ETHUSDT")
    )
    eth_first = _eth_entry_quantity_after_btc_appreciates(
        tmp_path / "eth-first", ("ETHUSDT", "BTCUSDT")
    )

    assert eth_first == btc_first


def test_invalid_later_symbol_price_uses_entry_basis_and_cannot_distort_equity(
    tmp_path: Path,
) -> None:
    engine, database, data = active_engine(
        tmp_path,
        settings={"max_concurrent_positions": 2},
        selection_order=("ETHUSDT", "BTCUSDT"),
    )
    data.histories["ETHUSDT"] = [Decimal("50")] * 30
    engine.evaluate_active_round()
    data.now += timedelta(minutes=5)
    data.observed_at = data.now
    data.prices["BTCUSDT"] = Decimal("-100")
    data.histories["ETHUSDT"] = list(map(Decimal, range(30, 14, -1)))

    result = engine.evaluate_active_round()

    btc = rows(database, "SELECT * FROM paper_positions WHERE symbol='BTCUSDT'")[0]
    btc_basis = Decimal(str(btc["quantity"])) * Decimal(str(btc["entry_price_ntd"]))
    eth = rows(database, "SELECT * FROM paper_positions WHERE symbol='ETHUSDT'")[0]
    eth_value = Decimal(str(eth["quantity"])) * Decimal("1500")
    assert result.account.position_value_ntd == btc_basis + eth_value
    assert (
        next(decision for decision in result.decisions if decision.symbol == "BTCUSDT").outcome
        == "rejected"
    )


def test_conversion_and_candle_errors_are_rejected_per_symbol_without_aborting_others(
    tmp_path: Path,
) -> None:
    engine, database, data = active_engine(tmp_path, settings={"max_concurrent_positions": 2})
    data.quote_assets["ETHUSDT"] = "USD"
    data.conversions["USD"] = Decimal("31")
    data.conversion_errors.add("USD")

    result = engine.evaluate_active_round()

    assert (
        next(decision for decision in result.decisions if decision.symbol == "BTCUSDT").outcome
        == "filled"
    )
    eth_decision = next(decision for decision in result.decisions if decision.symbol == "ETHUSDT")
    assert eth_decision.outcome == "rejected"
    assert "conversion unavailable" in eth_decision.reason
    assert len(rows(database, "SELECT * FROM paper_trades")) == 1

    engine2, database2, data2 = active_engine(
        tmp_path / "candles", settings={"max_concurrent_positions": 2}
    )
    data2.candle_errors.add("ETHUSDT")
    result2 = engine2.evaluate_active_round()
    btc2 = next(decision for decision in result2.decisions if decision.symbol == "BTCUSDT")
    eth2 = next(decision for decision in result2.decisions if decision.symbol == "ETHUSDT")
    assert btc2.outcome == "filled"
    assert eth2.outcome == "rejected"
    assert "candles unavailable" in eth2.reason
    assert len(rows(database2, "SELECT * FROM paper_trades")) == 1


def test_candle_failure_uses_validated_ticker_conversion_source_timestamp(
    tmp_path: Path,
) -> None:
    engine, database, data = active_engine(tmp_path)
    data.observed_at = data.now - timedelta(minutes=1)
    data.conversion_observed_at["USDT"] = data.now - timedelta(minutes=2)
    data.candle_errors.add("ETHUSDT")

    engine.evaluate_active_round()

    signal = rows(database, "SELECT * FROM trading_signals WHERE symbol='ETHUSDT'")[0]
    evidence = json.loads(str(signal["market_evidence_json"]))
    assert signal["source_timestamp"] == (data.now - timedelta(minutes=2)).isoformat()
    assert signal["source_timestamp"] != signal["evaluated_at"]
    assert evidence["price_observed_at"] == (data.now - timedelta(minutes=1)).isoformat()
    assert evidence["conversion_observed_at"] == (
        data.now - timedelta(minutes=2)
    ).isoformat()


def test_conversion_freshness_uses_conversion_age_limit_not_candle_limit(tmp_path: Path) -> None:
    engine, database, data = active_engine(
        tmp_path,
        settings={"max_conversion_age_seconds": 60, "max_candle_age_seconds": 7200},
    )
    data.conversion_observed_at["USDT"] = data.now - timedelta(minutes=2)

    result = engine.evaluate_active_round()

    assert all(decision.outcome == "rejected" for decision in result.decisions)
    assert all("conversion" in decision.reason for decision in result.decisions)
    assert rows(database, "SELECT * FROM paper_trades") == []


def test_position_and_trade_provenance_must_match_signal_round_and_symbol(tmp_path: Path) -> None:
    _engine, database, _data = active_engine(tmp_path)
    with database.connect() as connection, connection:
        round_id = connection.execute(
            "SELECT id FROM trading_round WHERE status='active'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO trading_signals VALUES "
            "(NULL, ?, 'BTCUSDT', 'test:1', 'signal-1', ?, ?, 'rsi-v1', "
            "'buy', 'filled', 'fixture', '{}')",
            (
                round_id,
                datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
                datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
            ),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO paper_positions VALUES "
                "(?, 'ETHUSDT', '1', '100', '1', ?, 'rsi-v1', 'signal-1')",
                (round_id, datetime(2026, 1, 2, tzinfo=UTC).isoformat()),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO paper_trades VALUES "
                "(NULL, ?, 'signal-1', 'ETHUSDT', 'buy', '1', '100', '100', '100', "
                "'1', '0', ?, ?, 'rsi-v1', 'fixture', '0')",
                (
                    round_id,
                    datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
                    datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
                ),
            )
