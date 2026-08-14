from __future__ import annotations

import itertools
import random
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from backend.tests.test_paper_trading import active_engine, rows


@pytest.mark.parametrize("seed", range(32))
def test_generated_accounting_sequences_preserve_independent_ledger_invariants(
    tmp_path: Path, seed: int
) -> None:
    """Fixed generated paths exercise the real engine, fixture provider, and SQLite ledger."""
    randomizer = random.Random(seed)  # noqa: S311 - deterministic test generator
    order = tuple(randomizer.sample(["BTCUSDT", "ETHUSDT"], 2))
    max_positions = randomizer.choice([1, 2])
    allocation = Decimal(randomizer.choice([5, 10, 20]))
    engine, database, data = active_engine(
        tmp_path / str(seed),
        settings={
            "max_concurrent_positions": max_positions,
            "max_position_allocation_pct": str(allocation),
            "stop_loss_pct": "1",
            "take_profit_pct": "1",
            "daily_loss_limit_pct": "0.1",
        },
        selection_order=order,  # type: ignore[arg-type]
    )
    if randomizer.choice([True, False]):
        data.histories[order[1]] = [data.prices[order[1]]] * 30

    first = engine.evaluate_active_round()
    before_duplicate = {
        table: rows(database, f"SELECT * FROM {table} ORDER BY rowid")  # noqa: S608
        for table in ("paper_trades", "paper_positions", "portfolio_snapshots")
    }
    duplicate = engine.evaluate_active_round()
    assert duplicate.account == first.account
    assert before_duplicate == {
        table: rows(database, f"SELECT * FROM {table} ORDER BY rowid")  # noqa: S608
        for table in before_duplicate
    }

    data.now += timedelta(minutes=5)
    data.observed_at = data.now
    multiplier = Decimal(randomizer.choice(["0.90", "0.95", "1.05", "1.10"]))
    for symbol in data.prices:
        data.prices[symbol] *= multiplier
        data.histories[symbol] = [data.prices[symbol]] * 30
    result = engine.evaluate_active_round()

    snapshots = rows(database, "SELECT * FROM portfolio_snapshots ORDER BY id")
    for snapshot in snapshots:
        cash = Decimal(str(snapshot["cash_ntd"]))
        marked = Decimal(str(snapshot["position_value_ntd"]))
        assert cash >= 0 and marked >= 0
        assert Decimal(str(snapshot["total_equity_ntd"])) == cash + marked
    positions = rows(database, "SELECT * FROM paper_positions")
    assert len(positions) <= max_positions
    assert all(Decimal(str(position["quantity"])) > 0 for position in positions)
    assert all(
        Decimal(str(position["quantity"])) * Decimal(str(position["entry_price_ntd"]))
        <= Decimal("5000") * allocation / Decimal("100")
        for position in positions
    )

    trades = rows(database, "SELECT * FROM paper_trades ORDER BY id")
    fee_rate = Decimal("0.001")
    slippage_rate = Decimal("0.002")
    for trade in trades:
        quantity = Decimal(str(trade["quantity"]))
        market = Decimal(str(trade["market_price_ntd"]))
        fill = Decimal(str(trade["fill_price_ntd"]))
        notional = Decimal(str(trade["notional_ntd"]))
        assert notional == quantity * fill
        assert Decimal(str(trade["fee_ntd"])) == notional * fee_rate
        assert Decimal(str(trade["slippage_ntd"])) == quantity * abs(fill - market)
        expected_fill = market * (
            Decimal("1") + slippage_rate if trade["side"] == "buy" else Decimal("1") - slippage_rate
        )
        assert fill == expected_fill
    assert result.account.costs_ntd == sum(
        (Decimal(str(trade["fee_ntd"])) + Decimal(str(trade["slippage_ntd"])) for trade in trades),
        Decimal(),
    )


@pytest.mark.parametrize("order", list(itertools.permutations(("BTCUSDT", "ETHUSDT"))))
def test_generated_pair_permutations_process_exits_before_daily_loss_limited_entries(
    tmp_path: Path, order: tuple[str, str]
) -> None:
    engine, database, data = active_engine(
        tmp_path / "-".join(order),
        settings={
            "max_concurrent_positions": 2,
            "stop_loss_pct": "1",
            "daily_loss_limit_pct": "0.1",
        },
        selection_order=order,
    )
    other = "ETHUSDT" if order[0] == "BTCUSDT" else "BTCUSDT"
    data.histories[other] = [data.prices[other]] * 30
    opened = engine.evaluate_active_round()
    held = next(decision.symbol for decision in opened.decisions if decision.outcome == "filled")
    candidate = "ETHUSDT" if held == "BTCUSDT" else "BTCUSDT"
    data.now += timedelta(minutes=5)
    data.observed_at = data.now
    data.prices[held] *= Decimal("0.90")
    data.histories[held] = [data.prices[held]] * 30
    data.histories[candidate] = list(map(Decimal, range(30, 14, -1)))
    result = engine.evaluate_active_round()
    exit_decision = next(decision for decision in result.decisions if decision.symbol == held)
    entry_decision = next(decision for decision in result.decisions if decision.symbol == candidate)
    assert exit_decision.outcome == "filled"
    assert "stop-loss" in exit_decision.reason
    assert (entry_decision.outcome, entry_decision.reason) == (
        "rejected",
        "daily loss limit reached",
    )
    assert rows(database, "SELECT * FROM paper_positions") == []
