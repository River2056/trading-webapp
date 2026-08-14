from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.database import Database
from backend.app.engine import (
    RoundPlanningError,
    RoundPlanningSettings,
    TradingEngine,
    executable_quantity,
)
from backend.app.market_data import (
    Candle,
    ConversionLeg,
    ConversionProvenance,
    MarketDataError,
    MarketRules,
    MarketSummary,
    NtdConversion,
)


class FixtureMarketData:
    def __init__(self, *, malformed: bool = False, count: int = 6) -> None:
        self.observed_at = datetime(2026, 1, 8, 12, tzinfo=UTC)
        self.malformed = malformed
        self.count = count

    def market_summaries(self) -> list[MarketSummary]:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT", "DOGEUSDT"]
        return [
            MarketSummary(
                symbol=symbol,
                base_asset=symbol.removesuffix("USDT"),
                quote_asset="USDT",
                last_price=Decimal("0") if self.malformed and index == 0 else Decimal(100 + index),
                quote_volume=Decimal(1_000_000 - index * 50_000),
                observed_at=self.observed_at,
            )
            for index, symbol in enumerate(symbols[: self.count])
        ]

    def market_rules(self, symbol: str) -> MarketRules:
        return MarketRules(
            symbol,
            Decimal("0.000001"),
            Decimal("0.000001"),
            Decimal("0.000001"),
            "fixture",
        )

    def ntd_conversion(self, quote_asset: str) -> NtdConversion:
        assert quote_asset == "USDT"
        return NtdConversion(
            quote_asset="USDT",
            rate=Decimal("32"),
            path="USDT/TWD public reference",
            observed_at=self.observed_at,
            provenance=ConversionProvenance(
                ConversionLeg("fixture USDT/USD", self.observed_at, Decimal("1")),
                ConversionLeg("fixture USD/TWD", self.observed_at, Decimal("32")),
            ),
        )

    def historical_candles(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        assert interval == "1h"
        start = self.observed_at - timedelta(hours=limit)
        offset = sum(ord(character) for character in symbol) % 7
        candles = []
        price = Decimal(80 + offset)
        for index in range(limit):
            # Reproducible trend plus oscillation gives both candidates signals.
            price += Decimal("0.35") + Decimal((index % 8) - 4) / Decimal("10")
            candles.append(
                Candle(
                    opened_at=start + timedelta(hours=index),
                    open=price - Decimal("0.2"),
                    high=price + Decimal("0.4"),
                    low=price - Decimal("0.4"),
                    close=price,
                    volume=Decimal(10_000 + index),
                )
            )
        return candles


class QualificationFixtureMarketData(FixtureMarketData):
    def historical_candles(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        if symbol == "BTCUSDT":
            start = self.observed_at - timedelta(hours=limit)
            return [
                Candle(
                    start + timedelta(hours=index),
                    Decimal("100"),
                    Decimal("100"),
                    Decimal("100"),
                    Decimal("100"),
                    Decimal("1000"),
                )
                for index in range(limit)
            ]
        return super().historical_candles(symbol, interval, limit)


class SymbolRulesFixtureMarketData(FixtureMarketData):
    def market_rules(self, symbol: str) -> MarketRules:
        minimum_notional = Decimal("100000") if symbol == "BTCUSDT" else Decimal("1")
        return MarketRules(
            symbol, Decimal("0.000001"), Decimal("0.000001"), minimum_notional, "fixture"
        )


class BrokenCandidateMarketData(FixtureMarketData):
    def historical_candles(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        if symbol == "BTCUSDT":
            raise MarketDataError("malformed candidate candles")
        return super().historical_candles(symbol, interval, limit)


class MixedUnknownFundabilityMarketData(SymbolRulesFixtureMarketData):
    def market_rules(self, symbol: str) -> MarketRules:
        if symbol != "BTCUSDT":
            raise MarketDataError("exchange rules temporarily unavailable")
        return super().market_rules(symbol)


def settings() -> dict[str, object]:
    return {
        "candle_interval": "1h",
        "backtest_lookback_candles": 80,
        "minimum_liquidity_ntd": Decimal("1000000"),
        "fee_pct": Decimal("0.10"),
        "slippage_pct": Decimal("0.10"),
        "max_position_allocation_pct": Decimal("10.00"),
        "max_concurrent_positions": 3,
        "stop_loss_pct": Decimal("5.00"),
        "take_profit_pct": Decimal("10.00"),
        "daily_loss_limit_pct": Decimal("3.00"),
        "minimum_net_return_pct": Decimal("0"),
        "minimum_entry_count": 1,
        "minimum_trade_count": 1,
        "max_conversion_age_seconds": 86400,
        "max_candle_age_seconds": 7200,
    }


def test_executable_quantity_floors_step_and_enforces_exchange_minimums() -> None:
    configured = settings()
    configured["max_position_allocation_pct"] = Decimal("100")
    rules = MarketRules(
        "CHEAPUSDT", Decimal("0.1"), Decimal("0.1"), Decimal("300"), "fixture"
    )

    assert executable_quantity(Decimal("299"), Decimal("1"), configured, rules) == 0
    assert executable_quantity(Decimal("1000"), Decimal("3"), configured, rules) == Decimal(
        "332.6"
    )


def test_planning_excludes_high_ranked_min_notional_and_selects_affordable_candidate(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "rules.sqlite3")
    database.migrate()
    database.ensure_defaults()
    engine = TradingEngine(
        database,
        SymbolRulesFixtureMarketData(),
        lambda: datetime(2026, 1, 8, 12, tzinfo=UTC),
    )

    plan = engine.activate_round(settings())

    assert [selection.symbol for selection in plan.selections] == [
        "ETHUSDT",
        "SOLUSDT",
        "ADAUSDT",
        "XRPUSDT",
        "DOGEUSDT",
    ]
    with database.connect() as connection:
        btc = connection.execute(
            "SELECT exclusion_reason FROM market_rankings WHERE round_id=? AND symbol='BTCUSDT'",
            (plan.round_id,),
        ).fetchone()
    assert btc["exclusion_reason"] == "below minimum executable quantity"


def test_unknown_candidate_fundability_prevents_false_bankruptcy(tmp_path: Path) -> None:
    database = Database(tmp_path / "unknown-fundability.sqlite3")
    database.migrate()
    database.ensure_defaults()
    engine = TradingEngine(
        database,
        MixedUnknownFundabilityMarketData(),
        lambda: datetime(2026, 1, 8, 12, tzinfo=UTC),
    )

    with pytest.raises(RoundPlanningError, match="five markets") as raised:
        engine.activate_round(settings())

    assert raised.type is RoundPlanningError


def test_engine_selects_five_markets_and_persists_an_immutable_auditable_plan(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "plan.sqlite3")
    database.migrate()
    database.ensure_defaults()
    engine = TradingEngine(
        database, FixtureMarketData(), lambda: datetime(2026, 1, 8, 12, tzinfo=UTC)
    )

    plan = engine.activate_round(settings())

    assert [selection.symbol for selection in plan.selections] == [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "ADAUSDT",
        "XRPUSDT",
    ]
    assert all(
        selection.strategy_version in {"rsi-v1", "macd-v1", "bollinger-v1"}
        for selection in plan.selections
    )
    assert plan.frozen_settings["candle_interval"] == "1h"
    assert isinstance(plan.frozen_settings, RoundPlanningSettings)

    with sqlite3.connect(database.path) as connection:
        connection.row_factory = sqlite3.Row
        evidence = connection.execute(
            "SELECT symbol, rank, quote_volume, conversion_rate, score, selected, exclusion_reason "
            "FROM market_rankings WHERE round_id = ? ORDER BY rank",
            (plan.round_id,),
        ).fetchall()
        assert len(evidence) == 6
        assert dict(evidence[0]) == {
            "symbol": "BTCUSDT",
            "rank": 1,
            "quote_volume": "1000000",
            "conversion_rate": "32",
            "score": "32000000",
            "selected": 1,
            "exclusion_reason": None,
        }
        backtests = connection.execute(
            "SELECT strategy_version, assumptions_json, metrics_json FROM backtest_results "
            "WHERE round_id = ?",
            (plan.round_id,),
        ).fetchall()
        assert len(backtests) == 18
        assert {row["strategy_version"] for row in backtests} == {
            "rsi-v1",
            "macd-v1",
            "bollinger-v1",
        }
        assert all('"indicator"' in row["assumptions_json"] for row in backtests)
        assert all('"trade_count"' in row["metrics_json"] for row in backtests)
        frozen = connection.execute(
            "SELECT frozen_settings_json, status FROM trading_round WHERE id = ?",
            (plan.round_id,),
        ).fetchone()
        assert frozen["status"] == "active"
        assert '"candle_interval":"1h"' in frozen["frozen_settings_json"]
        provenance = connection.execute(
            "SELECT assumptions_json FROM backtest_results WHERE round_id=? LIMIT 1",
            (plan.round_id,),
        ).fetchone()[0]
        assert all(
            key in provenance
            for key in (
                "source",
                "provider",
                "symbol",
                "interval",
                "requested_count",
                "actual_count",
                "first_timestamp",
                "last_timestamp",
                "sha256",
            )
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE round_selections SET strategy_version='changed' WHERE round_id=?",
                (plan.round_id,),
            )
        immutable_operations = [
            ("DELETE FROM trading_round WHERE id=?", (plan.round_id,)),
            ("UPDATE market_rankings SET score='0' WHERE round_id=?", (plan.round_id,)),
            ("DELETE FROM market_rankings WHERE round_id=?", (plan.round_id,)),
            ("UPDATE backtest_results SET score='0' WHERE round_id=?", (plan.round_id,)),
            ("DELETE FROM backtest_results WHERE round_id=?", (plan.round_id,)),
            ("DELETE FROM round_selections WHERE round_id=?", (plan.round_id,)),
        ]
        for statement, parameters in immutable_operations:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, parameters)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO backtest_results VALUES (?, 'NEWUSDT', 'rsi-v1', '{}', '{}', 0, '0')",
                (plan.round_id,),
            )
        connection.execute(
            "UPDATE trading_round SET status='completed' WHERE id=?", (plan.round_id,)
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE trading_round SET frozen_settings_json='{}' WHERE id=?", (plan.round_id,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM round_selections WHERE round_id=?", (plan.round_id,))


def test_stale_conversion_leg_prevents_round_activation(tmp_path: Path) -> None:
    database = Database(tmp_path / "stale.sqlite3")
    database.migrate()
    data = FixtureMarketData()
    data.observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    engine = TradingEngine(database, data, lambda: datetime(2026, 1, 8, tzinfo=UTC))
    configured = settings()
    configured["max_conversion_age_seconds"] = 60
    with pytest.raises(RoundPlanningError, match="five.*qualifying"):
        engine.activate_round(configured)
    assert "stale" in next(iter(engine.last_exclusions.values()))


def test_configured_qualification_gates_are_frozen_and_persisted(tmp_path: Path) -> None:
    database = Database(tmp_path / "gates.sqlite3")
    database.migrate()
    configured = settings()
    configured["minimum_net_return_pct"] = Decimal("999")
    with pytest.raises(RoundPlanningError):
        TradingEngine(
            database, FixtureMarketData(), lambda: datetime(2026, 1, 8, 12, tzinfo=UTC)
        ).activate_round(configured)
    with sqlite3.connect(database.path) as connection:
        frozen, assumptions = connection.execute(
            "SELECT frozen_settings_json, assumptions_json FROM trading_round "
            "JOIN backtest_results ON trading_round.id=backtest_results.round_id LIMIT 1"
        ).fetchone()
    assert '"minimum_net_return_pct":"999"' in frozen
    assert '"qualification_gates"' in assumptions


def test_round_planning_does_not_activate_when_fewer_than_five_markets_qualify(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "few.sqlite3")
    database.migrate()
    database.ensure_defaults()
    engine = TradingEngine(database, FixtureMarketData(count=4), lambda: datetime.now(UTC))

    with pytest.raises(RoundPlanningError, match="five.*qualifying"):
        engine.activate_round(settings())

    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT status FROM trading_round").fetchone() == ("failed",)


def test_malformed_market_is_excluded_with_recorded_reason(tmp_path: Path) -> None:
    database = Database(tmp_path / "malformed.sqlite3")
    database.migrate()
    database.ensure_defaults()
    engine = TradingEngine(
        database, FixtureMarketData(malformed=True), lambda: datetime(2026, 1, 8, 12, tzinfo=UTC)
    )

    plan = engine.activate_round(settings())

    assert len(plan.selections) == 5
    assert "invalid price" in engine.last_exclusions["BTCUSDT"]
    with sqlite3.connect(database.path) as connection:
        excluded = connection.execute(
            "SELECT selected, exclusion_reason FROM market_rankings "
            "WHERE round_id=? AND symbol='BTCUSDT'",
            (plan.round_id,),
        ).fetchone()
    assert excluded == (0, "invalid price: must be positive")


def test_lower_liquidity_qualifier_replaces_higher_ranked_non_qualifier(tmp_path: Path) -> None:
    database = Database(tmp_path / "qualification.sqlite3")
    database.migrate()
    database.ensure_defaults()
    engine = TradingEngine(
        database, QualificationFixtureMarketData(), lambda: datetime(2026, 1, 8, 12, tzinfo=UTC)
    )

    plan = engine.activate_round(settings())

    assert "BTCUSDT" not in [selection.symbol for selection in plan.selections]
    assert "DOGEUSDT" in [selection.symbol for selection in plan.selections]
    with sqlite3.connect(database.path) as connection:
        evidence = connection.execute(
            "SELECT rank, selected, exclusion_reason FROM market_rankings "
            "WHERE round_id=? AND symbol='BTCUSDT'",
            (plan.round_id,),
        ).fetchone()
    assert evidence == (1, 0, "no qualifying strategy")


def test_bad_candidate_candles_are_excluded_and_lower_ranked_market_is_selected(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "bad-candles.sqlite3")
    database.migrate()
    plan = TradingEngine(
        database,
        BrokenCandidateMarketData(),
        lambda: datetime(2026, 1, 8, 12, tzinfo=UTC),
    ).activate_round(settings())
    assert "BTCUSDT" not in [selection.symbol for selection in plan.selections]
    assert "DOGEUSDT" in [selection.symbol for selection in plan.selections]
    with sqlite3.connect(database.path) as connection:
        assert connection.execute(
            "SELECT exclusion_reason FROM market_rankings WHERE symbol='BTCUSDT'"
        ).fetchone() == ("malformed candidate candles",)


def test_stale_candidate_candles_are_excluded(tmp_path: Path) -> None:
    database = Database(tmp_path / "stale-candles.sqlite3")
    database.migrate()
    configured = settings()
    configured["max_candle_age_seconds"] = 60
    with pytest.raises(RoundPlanningError, match="five.*qualifying"):
        TradingEngine(
            database,
            FixtureMarketData(),
            lambda: datetime(2026, 1, 8, 12, tzinfo=UTC),
        ).activate_round(configured)


def test_failed_planning_preserves_universe_and_failure_without_active_round(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "failure.sqlite3")
    database.migrate()
    database.ensure_defaults()
    engine = TradingEngine(database, FixtureMarketData(count=4), lambda: datetime.now(UTC))

    with pytest.raises(RoundPlanningError):
        engine.activate_round(settings())

    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT status FROM trading_round").fetchone() == ("failed",)
        assert connection.execute("SELECT COUNT(*) FROM market_rankings").fetchone() == (4,)
        failure = connection.execute("SELECT reason FROM planning_failures").fetchone()[0]
        assert "five markets with qualifying strategies" in failure
