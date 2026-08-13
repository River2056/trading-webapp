from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.market_data import Candle, MarketDataError, MarketSummary, NtdConversion
from backend.app.worker import TradingWorker
from backend.tests.test_paper_trading import active_engine, rows


def test_worker_advances_persisted_running_run_after_restart_but_not_stopped_run(
    tmp_path: Path,
) -> None:
    engine, database, data = active_engine(tmp_path)
    restarted = TradingWorker(database, engine, lambda: data.now)

    assert restarted.step().outcome == "advanced"
    assert len(rows(database, "SELECT * FROM paper_trades")) == 1

    data.now += timedelta(minutes=5)
    data.observed_at = data.now
    with database.connect() as connection, connection:
        connection.execute("UPDATE trading_run SET desired_state='stopped' WHERE id=1")

    assert restarted.step().outcome == "stopped"
    assert len(rows(database, "SELECT * FROM trading_signals")) == 2


@pytest.mark.parametrize(
    ("failure", "expected_cause"),
    [
        ("missing", "market summary unavailable"),
        ("malformed", "market price or conversion rate is not positive"),
        ("out_of_order", "out-of-order required candles"),
        ("stale", "market price is stale"),
    ],
)
def test_required_data_failure_atomically_pauses_before_any_trade(
    tmp_path: Path, failure: str, expected_cause: str
) -> None:
    engine, database, data = active_engine(tmp_path)
    if failure == "missing":
        data.prices.clear()
    elif failure == "malformed":
        data.prices["BTCUSDT"] = Decimal("-1")
    elif failure == "out_of_order":
        candles = data.historical_candles("BTCUSDT", "1h", 80)
        original = data.historical_candles

        def out_of_order(symbol: str, interval: str, limit: int) -> list[Candle]:
            if symbol == "BTCUSDT":
                return list(reversed(candles))
            return original(symbol, interval, limit)

        data.historical_candles = out_of_order  # type: ignore[method-assign]
    else:
        data.now += timedelta(hours=1)

    result = TradingWorker(database, engine, lambda: data.now).step()

    assert result.outcome == "degraded"
    assert rows(database, "SELECT * FROM paper_trades") == []
    incident = rows(database, "SELECT * FROM market_data_incidents WHERE active=1")[0]
    assert expected_cause in str(incident["cause"])
    run = rows(database, "SELECT * FROM trading_run WHERE id=1")[0]
    assert run["desired_state"] == "running"
    assert run["operational_state"] == "degraded"


def test_retries_are_bounded_and_recovery_requires_fresh_data(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path)
    data.now += timedelta(hours=1)
    worker = TradingWorker(
        database,
        engine,
        lambda: data.now,
        base_backoff_seconds=2,
        max_backoff_seconds=4,
    )

    calls = 0
    original = data.market_summaries

    def counted_summaries():
        nonlocal calls
        calls += 1
        return original()

    data.market_summaries = counted_summaries  # type: ignore[method-assign]
    assert worker.step().retry_after_seconds == 2
    assert TradingWorker(database, engine, lambda: data.now).step().outcome == "backoff"
    assert calls == 1
    data.now += timedelta(seconds=2)
    assert worker.step().retry_after_seconds == 4
    data.now += timedelta(seconds=4)
    assert worker.step().retry_after_seconds == 4

    data.now += timedelta(seconds=4)
    data.observed_at = data.now
    data.histories = {symbol: values for symbol, values in data.histories.items()}
    recovered = worker.step()

    assert recovered.outcome == "advanced"
    incident = rows(database, "SELECT * FROM market_data_incidents")[0]
    assert incident["active"] == 0
    assert incident["recovered_at"] is not None
    run = rows(database, "SELECT operational_state FROM trading_run")[0]
    assert run["operational_state"] == "running"
    assert len(rows(database, "SELECT * FROM paper_trades")) == 1


def test_fault_after_signal_insert_rolls_back_signal_fill_and_snapshot(tmp_path: Path) -> None:
    engine, database, _data = active_engine(tmp_path)

    def crash(_boundary: str) -> None:
        raise RuntimeError("injected crash")

    engine.fault_injector = crash
    with pytest.raises(RuntimeError, match="injected crash"):
        engine.evaluate_active_round()

    assert rows(database, "SELECT * FROM trading_signals") == []
    assert rows(database, "SELECT * FROM paper_trades") == []
    assert rows(database, "SELECT * FROM portfolio_snapshots") == []

    engine.fault_injector = None
    engine.evaluate_active_round()
    restarted = TradingWorker(database, engine, engine.clock)
    assert restarted.step().outcome == "advanced"
    assert len(rows(database, "SELECT * FROM paper_trades")) == 1


def test_dashboard_projects_active_incident_retry_and_recovery(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path)
    data.now += timedelta(hours=1)
    worker = TradingWorker(database, engine, lambda: data.now)
    worker.step()
    app = create_app(
        database_path=database.path,
        market_data=data,
        clock=lambda: data.now,
        start_worker=False,
    )

    with TestClient(app) as client:
        client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
        dashboard = client.get("/api/dashboard").json()

    assert dashboard["engine_health"] == "degraded"
    assert dashboard["operational_state"] == "degraded"
    assert dashboard["market_data_incident"]["cause"]
    assert dashboard["market_data_incident"]["occurred_at"]
    assert dashboard["market_data_incident"]["retry_count"] == 1
    assert dashboard["market_data_incident"]["next_retry_at"]
    assert dashboard["market_data_incident"]["recovered_at"] is None
    assert dashboard["market_data_incident"]["active"] == 1

    data.now += timedelta(seconds=1)
    data.observed_at = data.now
    worker.step()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": "correct horse battery staple"})
        recovered = client.get("/api/dashboard").json()
    assert recovered["engine_health"] == "healthy"
    assert recovered["market_data_incident"]["active"] == 0
    assert recovered["market_data_incident"]["recovered_at"] is not None


def test_top_level_market_summary_failure_degrades_and_loop_survives(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path)
    attempts = 0

    def flaky():
        nonlocal attempts
        attempts += 1
        raise MarketDataError("ticker provider unavailable")

    data.market_summaries = flaky  # type: ignore[method-assign]
    worker = TradingWorker(database, engine, lambda: data.now, sleeper=lambda _: None)
    worker.run_forever(max_steps=2)
    assert attempts == 1
    assert rows(database, "SELECT outcome FROM worker_checkpoint")[0]["outcome"] == "backoff"
    assert "ticker provider unavailable" in str(
        rows(database, "SELECT cause FROM market_data_incidents")[0]["cause"]
    )


def test_concurrent_workers_serialize_same_interval_without_duplicates(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = data.market_summaries
    calls = 0

    def blocking():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(2)
        return original()

    data.market_summaries = blocking  # type: ignore[method-assign]
    errors: list[BaseException] = []

    def run() -> None:
        try:
            TradingWorker(database, engine, lambda: data.now).step()
        except BaseException as error:
            errors.append(error)

    first, second = threading.Thread(target=run), threading.Thread(target=run)
    first.start()
    assert entered.wait(2)
    second.start()
    release.set()
    first.join(3)
    second.join(3)
    assert errors == []
    assert len(rows(database, "SELECT * FROM paper_trades")) == 1
    assert len(rows(database, "SELECT * FROM portfolio_snapshots")) == 1


def test_worker_completion_fault_rolls_back_trade_recovery_and_checkpoint(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path)
    data.now += timedelta(hours=1)
    worker = TradingWorker(database, engine, lambda: data.now)
    worker.step()
    data.now += timedelta(seconds=1)
    data.observed_at = data.now
    previous = rows(database, "SELECT * FROM worker_checkpoint")[0]
    worker.fault_injector = lambda _: (_ for _ in ()).throw(RuntimeError("completion crash"))
    with pytest.raises(RuntimeError, match="completion crash"):
        worker.step()
    assert rows(database, "SELECT * FROM paper_trades") == []
    assert rows(database, "SELECT * FROM market_data_incidents")[0]["active"] == 1
    assert rows(database, "SELECT * FROM worker_checkpoint")[0] == previous


def test_engine_rejects_relationally_invalid_candle_and_worker_degrades(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path)
    original = data.historical_candles

    def malformed(symbol: str, interval: str, limit: int) -> list[Candle]:
        candles = original(symbol, interval, limit)
        if symbol == "BTCUSDT":
            bad = candles[-1]
            candles[-1] = Candle(
                bad.opened_at,
                Decimal("10"),
                Decimal("9"),
                Decimal("8"),
                Decimal("11"),
                Decimal("1"),
            )
        return candles

    data.historical_candles = malformed  # type: ignore[method-assign]
    assert TradingWorker(database, engine, lambda: data.now).step().outcome == "degraded"
    assert rows(database, "SELECT * FROM paper_trades") == []


def test_failed_evaluation_degrades_inside_same_writer_lock(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = data.historical_candles
    calls = 0

    def fail_first(symbol: str, interval: str, limit: int) -> list[Candle]:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(2)
            raise MarketDataError("failed attempt")
        return original(symbol, interval, limit)

    data.historical_candles = fail_first  # type: ignore[method-assign]
    outcomes: list[str] = []
    failed = threading.Thread(target=lambda: outcomes.append(
        TradingWorker(database, engine, lambda: data.now).step().outcome
    ))
    healthy = threading.Thread(target=lambda: outcomes.append(
        TradingWorker(database, engine, lambda: data.now).step().outcome
    ))
    failed.start()
    assert entered.wait(2)
    healthy.start()
    release.set()
    failed.join(3)
    healthy.join(3)

    assert outcomes == ["degraded", "backoff"]
    assert rows(database, "SELECT * FROM trading_signals") == []
    assert rows(database, "SELECT * FROM paper_trades") == []
    run = rows(database, "SELECT operational_state FROM trading_run")[0]
    assert run["operational_state"] == "degraded"


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_engine_non_finite_ticker_degrades_without_trade(tmp_path: Path, value: Decimal) -> None:
    engine, database, data = active_engine(tmp_path)
    data.prices["BTCUSDT"] = value
    assert TradingWorker(database, engine, lambda: data.now).step().outcome == "degraded"
    assert rows(database, "SELECT * FROM paper_trades") == []


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_engine_non_finite_conversion_degrades_without_trade(
    tmp_path: Path, value: Decimal
) -> None:
    engine, database, data = active_engine(tmp_path)
    data.conversions["USDT"] = value
    assert TradingWorker(database, engine, lambda: data.now).step().outcome == "degraded"
    assert rows(database, "SELECT * FROM paper_trades") == []


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_engine_non_finite_candle_degrades_without_trade(tmp_path: Path, field: str) -> None:
    engine, database, data = active_engine(tmp_path)
    original = data.historical_candles

    def malformed(symbol: str, interval: str, limit: int) -> list[Candle]:
        candles = original(symbol, interval, limit)
        if symbol == "BTCUSDT":
            values = candles[-1].__dict__ | {field: Decimal("NaN")}
            candles[-1] = Candle(**values)
        return candles

    data.historical_candles = malformed  # type: ignore[method-assign]
    assert TradingWorker(database, engine, lambda: data.now).step().outcome == "degraded"
    assert rows(database, "SELECT * FROM paper_trades") == []


@pytest.mark.parametrize("source", ["ticker", "conversion", "candle"])
def test_future_required_source_timestamp_degrades_without_trade(
    tmp_path: Path, source: str
) -> None:
    engine, database, data = active_engine(tmp_path)
    future = data.now + timedelta(seconds=1)
    if source == "ticker":
        data.observed_at = future
    elif source == "conversion":
        original_conversion = data.ntd_conversion

        def future_conversion(quote_asset: str) -> NtdConversion:
            conversion = original_conversion(quote_asset)
            return NtdConversion(
                conversion.quote_asset,
                conversion.rate,
                conversion.path,
                future,
                conversion.provenance,
            )

        data.ntd_conversion = future_conversion  # type: ignore[method-assign]
    else:
        original_candles = data.historical_candles

        def future_candles(symbol: str, interval: str, limit: int) -> list[Candle]:
            candles = original_candles(symbol, interval, limit)
            last = candles[-1]
            candles[-1] = Candle(future, last.open, last.high, last.low, last.close, last.volume)
            return candles

        data.historical_candles = future_candles  # type: ignore[method-assign]

    assert TradingWorker(database, engine, lambda: data.now).step().outcome == "degraded"
    assert rows(database, "SELECT * FROM paper_trades") == []


def test_incomplete_required_candle_history_degrades_without_advancing(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path)
    original = data.historical_candles

    def incomplete(symbol: str, interval: str, limit: int) -> list[Candle]:
        return original(symbol, interval, limit)[:-1]

    data.historical_candles = incomplete  # type: ignore[method-assign]

    result = TradingWorker(database, engine, lambda: data.now).step()

    assert result.outcome == "degraded"
    assert rows(database, "SELECT * FROM paper_trades") == []
    assert rows(database, "SELECT * FROM portfolio_snapshots") == []
    incident = rows(database, "SELECT cause FROM market_data_incidents WHERE active=1")[0]
    assert "expected 80, received 79" in str(incident["cause"])


def test_start_preserves_degraded_operational_state_during_active_backoff(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path)
    data.now += timedelta(hours=1)
    TradingWorker(database, engine, lambda: data.now).step()
    app = create_app(database.path, market_data=data, clock=lambda: data.now, start_worker=False)

    with TestClient(app) as client:
        client.post("/api/auth/signup", json={"password": "correct horse battery staple"})
        client.post("/api/run/stop")
        assert client.post("/api/run/start").status_code == 200
        dashboard = client.get("/api/dashboard").json()

    assert dashboard["desired_state"] == "running"
    assert dashboard["operational_state"] == "degraded"
    assert dashboard["market_data_incident"]["active"] == 1


def test_lifespan_waits_for_inflight_worker_and_exits_with_thread_dead(tmp_path: Path) -> None:
    engine, database, data = active_engine(tmp_path)
    entered, release = threading.Event(), threading.Event()
    context_entered, context_exited = threading.Event(), threading.Event()
    original = data.market_summaries

    def blocking() -> list[MarketSummary]:
        entered.set()
        assert release.wait(3)
        return original()

    data.market_summaries = blocking  # type: ignore[method-assign]
    app = create_app(database.path, market_data=data, clock=lambda: data.now, start_worker=True)

    def lifecycle() -> None:
        with TestClient(app):
            context_entered.set()
        context_exited.set()

    lifecycle_thread = threading.Thread(target=lifecycle)
    lifecycle_thread.start()
    assert context_entered.wait(2)
    assert entered.wait(2)
    assert not context_exited.wait(0.1)
    release.set()
    lifecycle_thread.join(3)

    assert context_exited.is_set()
    assert not any(
        thread.name == "paper-trading-worker" and thread.is_alive()
        for thread in threading.enumerate()
    )
