from importlib.resources import files
from inspect import signature
from pathlib import Path

from backend.app.database import Database
from backend.app.engine import RoundPlanningSettings
from backend.app.schemas import RunSettings
from backend.app.worker import TradingWorker


def test_canonical_backend_package_contains_all_migrations() -> None:
    migrations = files("backend").joinpath("migrations")
    names = sorted(item.name for item in migrations.iterdir() if item.name.endswith(".sql"))
    assert names == [
        "001_bootstrap.sql",
        "002_round_plans.sql",
        "003_planning_settings.sql",
        "004_paper_trading.sql",
        "005_restart_safety.sql",
        "006_round_lifecycle.sql",
        "007_analytics_history_indexes.sql",
        "008_operational_contentions.sql",
    ]


def test_readme_numeric_defaults_match_fresh_persistence_and_runtime_constants(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "defaults.sqlite3")
    database.migrate()
    database.ensure_defaults()
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM run_settings WHERE id=1").fetchone()
        assert row is not None
        persisted = dict(row)

    expected = {
        "starting_capital_ntd": "5000.00",
        "round_duration_days": 7,
        "strategy_cadence_seconds": 300,
        "max_position_allocation_pct": "10.00",
        "max_concurrent_positions": 3,
        "stop_loss_pct": "5.00",
        "take_profit_pct": "10.00",
        "daily_loss_limit_pct": "3.00",
        "fee_pct": "0.10",
        "slippage_pct": "0.10",
        "candle_interval": "1h",
        "backtest_lookback_candles": 80,
        "minimum_liquidity_ntd": "1000000",
        "minimum_entry_count": 1,
        "minimum_trade_count": 2,
        "max_conversion_age_seconds": 86400,
        "max_candle_age_seconds": 7200,
    }
    assert {key: persisted[key] for key in expected} == expected

    schema_defaults = RunSettings().model_dump()
    planning_defaults = RoundPlanningSettings.from_mapping(schema_defaults)
    for key, value in expected.items():
        assert str(schema_defaults[key]) == str(value)
        assert str(getattr(planning_defaults, key)) == str(value)
    assert signature(TradingWorker.run_forever).parameters["poll_seconds"].default == 1

    readme = (Path(__file__).parents[2] / "README.md").read_text()
    documented = (
        "5,000 NTD virtual capital",
        "seven-day rounds",
        "five-minute strategy cadence",
        "10% per position",
        "at most three positions",
        "5% stop loss",
        "10% take profit",
        "3% daily realized-loss pause",
        "0.10% fee",
        "0.10% slippage",
        "1-hour candles",
        "80-candle lookback",
        "1,000,000 NTD equivalent daily quote liquidity",
        "at least one entry and two completed backtest trades",
        "86,400-second maximum conversion age",
        "7,200-second maximum candle age",
        "worker polls every 1 second",
    )
    assert all(text in readme for text in documented)
