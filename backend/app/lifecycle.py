from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .database import Database
from .engine import MarketDataSafetyError
from .market_data import MarketData, MarketDataError


@dataclass(frozen=True)
class RolloverResult:
    outcome: str
    round_id: int
    ending_equity_ntd: Decimal


def _text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _json(value: object) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


class RoundLifecycle:
    """Transactionally finalizes due rounds; planning remains a separate restartable phase."""

    def __init__(
        self, database: Database, market_data: MarketData, clock: Callable[[], datetime]
    ) -> None:
        self.database = database
        self.market_data = market_data
        self.clock = clock

    def close_due_round(
        self, connection: sqlite3.Connection | None = None
    ) -> RolloverResult | None:
        return self._close_round(connection, require_running=True, require_due=True)

    def close_paused_round(
        self, connection: sqlite3.Connection | None = None
    ) -> RolloverResult | None:
        return self._close_round(
            connection,
            require_running=False,
            require_due=False,
            reason="fresh round requested",
        )

    def _close_round(
        self,
        connection: sqlite3.Connection | None,
        *,
        require_running: bool,
        require_due: bool,
        reason: str = "round completed",
    ) -> RolloverResult | None:
        if connection is None:
            with self.database.connect() as owned:
                owned.execute("BEGIN IMMEDIATE")
                try:
                    result = self._close(owned, require_running, require_due, reason)
                    owned.commit()
                    return result
                except BaseException:
                    owned.rollback()
                    raise
        return self._close(connection, require_running, require_due, reason)

    def _now(self) -> datetime:
        now = self.clock()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    @staticmethod
    def _timestamp(now: datetime) -> str:
        return now.isoformat(timespec="seconds").replace("+00:00", "Z")

    def _close(
        self,
        connection: sqlite3.Connection,
        require_running: bool,
        require_due: bool,
        reason: str,
    ) -> RolloverResult | None:
        now = self._now()
        run = connection.execute(
            "SELECT desired_state FROM trading_run WHERE id=1"
        ).fetchone()
        if run is None or (require_running and run["desired_state"] != "running"):
            return None
        row = connection.execute(
            "SELECT * FROM trading_round WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        settings = json.loads(str(row["frozen_settings_json"]))
        started = datetime.fromisoformat(str(row["started_at"]).replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        duration = timedelta(days=int(settings.get("round_duration_days", 7)))
        if require_due and now < started + duration:
            return None

        round_id = int(row["id"])
        cycle_id = row["cycle_id"]
        if cycle_id is None:
            active_cycle = connection.execute(
                "SELECT id FROM cycles WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if active_cycle is None:
                raise RuntimeError("active round has no active cycle")
            cycle_id = int(active_cycle["id"])
        prices, evidence = self._validated_prices(connection, round_id, settings, now)
        self._liquidate(connection, round_id, settings, prices, evidence, now)
        metrics = self._metrics(connection, round_id, settings, now)
        ending = Decimal(str(metrics["ending_equity_ntd"]))
        retrospective = self._summary(metrics)
        connection.execute(
            "INSERT INTO round_retrospectives VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                round_id,
                now.isoformat(),
                metrics["starting_equity_ntd"],
                metrics["ending_equity_ntd"],
                metrics["return_pct"],
                metrics["max_drawdown_pct"],
                metrics["total_costs_ntd"],
                metrics["trade_count"],
                metrics["win_count"],
                metrics["loss_count"],
                metrics["rejected_action_count"],
                _json(metrics["pairs"]),
                _json(metrics["strategies"]),
                _json(metrics),
                retrospective,
            ),
        )
        connection.execute(
            "UPDATE trading_round SET status='completed', ended_at=?, ending_equity_ntd=?, "
            "retrospective_json=? WHERE id=?",
            (now.isoformat(), _text(ending), _json(metrics), round_id),
        )
        connection.execute(
            "UPDATE trading_run SET current_capital_ntd=?, updated_at=? WHERE id=1",
            (_text(ending), self._timestamp(now)),
        )
        connection.execute(
            "INSERT INTO lifecycle_transitions(cycle_id, completed_round_id, status, created_at, "
            "ending_equity_ntd, next_starting_capital_ntd, reason) "
            "VALUES(?, ?, 'pending_plan', ?, ?, ?, ?)",
            (cycle_id, round_id, now.isoformat(), _text(ending), _text(ending), reason),
        )
        connection.execute(
            "UPDATE trading_run SET terminal_state=NULL, terminal_detail=NULL WHERE id=1"
        )
        return RolloverResult("completed", round_id, ending)

    def reset_bankrupt_cycle(
        self, connection: sqlite3.Connection, transition_id: int, reason: str
    ) -> Decimal:
        """Record bankruptcy and prepare a default-capital reset atomically."""
        now = self._now()
        timestamp = self._timestamp(now)
        transition = connection.execute(
            "SELECT * FROM lifecycle_transitions WHERE id=? AND status='pending_plan'",
            (transition_id,),
        ).fetchone()
        if transition is None:
            raise RuntimeError("bankruptcy transition is no longer pending")
        cycle = connection.execute(
            "SELECT * FROM cycles WHERE id=? AND status='active'", (transition["cycle_id"],)
        ).fetchone()
        settings = connection.execute(
            "SELECT starting_capital_ntd FROM run_settings WHERE id=1"
        ).fetchone()
        if cycle is None or settings is None:
            raise RuntimeError("bankruptcy reset requires active cycle and run settings")
        ending = Decimal(str(transition["ending_equity_ntd"]))
        default_capital = Decimal(str(settings["starting_capital_ntd"]))
        completed_count = int(connection.execute(
            "SELECT COUNT(*) FROM trading_round WHERE cycle_id=? AND status='completed'",
            (cycle["id"],),
        ).fetchone()[0])
        evidence = {
            "transition_id": transition_id,
            "completed_round_id": int(transition["completed_round_id"]),
            "planning_outcome": "all_qualified_candidates_unfundable",
            "reset_capital_ntd": _text(default_capital),
        }
        connection.execute(
            "UPDATE cycles SET status='completed', ended_at=?, ending_capital_ntd=?, "
            "completed_round_count=?, end_reason=?, evidence_json=? WHERE id=?",
            (timestamp, _text(ending), completed_count, reason, _json(evidence), cycle["id"]),
        )
        connection.execute(
            "INSERT INTO bankruptcies(cycle_id, round_id, declared_at, ending_equity_ntd, "
            "completed_round_count, reason, evidence_json) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (cycle["id"], transition["completed_round_id"], timestamp, _text(ending),
             completed_count, reason, _json(evidence)),
        )
        connection.execute(
            "INSERT INTO cycle_retrospectives VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (cycle["id"], timestamp, cycle["starting_capital_ntd"], _text(ending),
             completed_count, reason, _json(evidence),
             f"Cycle ended in bankruptcy after {completed_count} completed rounds: {reason}."),
        )
        connection.execute(
            "UPDATE lifecycle_transitions SET next_starting_capital_ntd=?, reason=? WHERE id=?",
            (_text(default_capital), "bankruptcy reset: " + reason, transition_id),
        )
        connection.execute(
            "INSERT INTO cycles(status, started_at, starting_capital_ntd) VALUES('active', ?, ?)",
            (timestamp, _text(default_capital)),
        )
        connection.execute(
            "UPDATE trading_run SET current_capital_ntd=?, terminal_state=NULL, "
            "terminal_detail=NULL, updated_at=? WHERE id=1",
            (_text(default_capital), timestamp),
        )
        return default_capital

    def _validated_prices(
        self,
        connection: sqlite3.Connection,
        round_id: int,
        settings: dict[str, object],
        now: datetime,
    ) -> tuple[dict[str, Decimal], dict[str, dict[str, object]]]:
        summaries = {summary.symbol: summary for summary in self.market_data.market_summaries()}
        symbols = [
            str(row[0])
            for row in connection.execute(
                "SELECT symbol FROM round_selections WHERE round_id=? ORDER BY selection_rank",
                (round_id,),
            )
        ]
        prices: dict[str, Decimal] = {}
        evidence: dict[str, dict[str, object]] = {}
        for symbol in symbols:
            summary = summaries.get(symbol)
            if summary is None:
                raise MarketDataSafetyError(f"{symbol}: market summary unavailable at round end")
            try:
                conversion = self.market_data.ntd_conversion(summary.quote_asset)
            except MarketDataError as error:
                raise MarketDataSafetyError(f"{symbol}: {error}") from error
            price = summary.last_price * conversion.rate
            if (
                not summary.last_price.is_finite()
                or not conversion.rate.is_finite()
                or not price.is_finite()
                or price <= 0
            ):
                raise MarketDataSafetyError(f"{symbol}: unsafe round-end valuation")
            if summary.observed_at > now:
                raise MarketDataSafetyError(f"{symbol}: market price timestamp is in the future")
            if conversion.observed_at > now:
                raise MarketDataSafetyError(f"{symbol}: conversion timestamp is in the future")
            if (now - summary.observed_at).total_seconds() > int(
                str(settings.get("max_candle_age_seconds", 7200))
            ):
                raise MarketDataSafetyError(f"{symbol}: stale round-end market price")
            if (now - conversion.observed_at).total_seconds() > int(
                str(settings.get("max_conversion_age_seconds", 86400))
            ):
                raise MarketDataSafetyError(f"{symbol}: stale round-end conversion")
            prices[symbol] = price
            evidence[symbol] = {
                "market_price_ntd": _text(price),
                "price_observed_at": summary.observed_at.isoformat(),
                "conversion_observed_at": conversion.observed_at.isoformat(),
                "conversion_path": conversion.path,
                "source_timestamp": min(summary.observed_at, conversion.observed_at).isoformat(),
            }
        return prices, evidence

    def _liquidate(
        self,
        connection: sqlite3.Connection,
        round_id: int,
        settings: dict[str, object],
        prices: dict[str, Decimal],
        evidence: dict[str, dict[str, object]],
        now: datetime,
    ) -> None:
        fee_rate = Decimal(str(settings["fee_pct"])) / 100
        slippage_rate = Decimal(str(settings["slippage_pct"])) / 100
        positions = connection.execute(
            "SELECT * FROM paper_positions WHERE round_id=? ORDER BY symbol", (round_id,)
        ).fetchall()
        for position in positions:
            symbol = str(position["symbol"])
            quantity = Decimal(str(position["quantity"]))
            market = prices[symbol]
            fill = market * (1 - slippage_rate)
            notional = quantity * fill
            fee = notional * fee_rate
            slippage = quantity * (market - fill)
            basis = quantity * Decimal(str(position["entry_price_ntd"]))
            realized = notional - fee - basis - Decimal(str(position["entry_cost_ntd"]))
            signal_id = hashlib.sha256(f"round-end:{round_id}:{symbol}".encode()).hexdigest()
            interval = f"round-end:{round_id}"
            source_timestamp = str(evidence[symbol]["source_timestamp"])
            connection.execute(
                "INSERT INTO trading_signals VALUES(NULL, ?, ?, ?, ?, ?, ?, ?, 'sell', "
                "'filled', 'deterministic round-end liquidation', ?)",
                (
                    round_id,
                    symbol,
                    interval,
                    signal_id,
                    now.isoformat(),
                    source_timestamp,
                    position["strategy_version"],
                    _json(evidence[symbol]),
                ),
            )
            connection.execute(
                "DELETE FROM paper_positions WHERE round_id=? AND symbol=?", (round_id, symbol)
            )
            connection.execute(
                "INSERT INTO paper_trades VALUES(NULL, ?, ?, ?, 'sell', ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'deterministic round-end liquidation', ?)",
                (
                    round_id,
                    signal_id,
                    symbol,
                    _text(quantity),
                    _text(market),
                    _text(fill),
                    _text(notional),
                    _text(fee),
                    _text(slippage),
                    now.isoformat(),
                    source_timestamp,
                    position["strategy_version"],
                    _text(realized),
                ),
            )

    def _metrics(
        self,
        connection: sqlite3.Connection,
        round_id: int,
        settings: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        starting = Decimal(str(settings.get("starting_capital_ntd", "5000")))
        trades = connection.execute(
            "SELECT * FROM paper_trades WHERE round_id=? ORDER BY id", (round_id,)
        ).fetchall()
        cash = starting
        costs = Decimal()
        wins = losses = 0
        per_pair: dict[str, dict[str, object]] = {}
        per_strategy: dict[str, dict[str, object]] = {}
        for trade in trades:
            notional = Decimal(str(trade["notional_ntd"]))
            fee = Decimal(str(trade["fee_ntd"]))
            costs += fee + Decimal(str(trade["slippage_ntd"]))
            cash += notional - fee if trade["side"] == "sell" else -(notional + fee)
            pnl = Decimal(str(trade["realized_pnl_ntd"]))
            trade_cost = fee + Decimal(str(trade["slippage_ntd"]))
            for key, groups in (
                (str(trade["symbol"]), per_pair),
                (str(trade["strategy_version"]), per_strategy),
            ):
                group = groups.setdefault(key, {
                    "trade_count": 0, "realized_pnl_ntd": Decimal(),
                    "costs_ntd": Decimal(), "wins": 0, "losses": 0,
                })
                group["trade_count"] = int(str(group["trade_count"])) + 1
                group["realized_pnl_ntd"] = Decimal(str(group["realized_pnl_ntd"])) + pnl
                group["costs_ntd"] = Decimal(str(group["costs_ntd"])) + trade_cost
                if trade["side"] == "sell" and pnl > 0:
                    group["wins"] = int(str(group["wins"])) + 1
                elif trade["side"] == "sell" and pnl < 0:
                    group["losses"] = int(str(group["losses"])) + 1
            if trade["side"] == "sell" and pnl > 0:
                wins += 1
            elif trade["side"] == "sell" and pnl < 0:
                losses += 1
        equities = [starting] + [
            Decimal(str(row[0]))
            for row in connection.execute(
                "SELECT total_equity_ntd FROM portfolio_snapshots "
                "WHERE round_id=? ORDER BY valued_at",
                (round_id,),
            )
        ] + [cash]
        peak = equities[0]
        max_drawdown = Decimal()
        for equity in equities:
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        selections = connection.execute(
            "SELECT symbol, strategy_version FROM round_selections WHERE round_id=? "
            "ORDER BY selection_rank",
            (round_id,),
        ).fetchall()
        rejected = int(
            connection.execute(
                "SELECT COUNT(*) FROM trading_signals WHERE round_id=? AND outcome='rejected'",
                (round_id,),
            ).fetchone()[0]
        )
        def serialize(groups: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
            return {
                key: {
                    **value,
                    "realized_pnl_ntd": _text(Decimal(str(value["realized_pnl_ntd"]))),
                    "costs_ntd": _text(Decimal(str(value["costs_ntd"]))),
                }
                for key, value in sorted(groups.items())
            }
        return {
            "starting_equity_ntd": _text(starting),
            "ending_equity_ntd": _text(cash),
            "pnl_ntd": _text(cash - starting),
            "return_pct": _text((cash - starting) / starting * 100) if starting else "0",
            "max_drawdown_pct": _text(max_drawdown),
            "total_costs_ntd": _text(costs),
            "trade_count": len(trades),
            "win_count": wins,
            "loss_count": losses,
            "rejected_action_count": rejected,
            "pairs": [str(row["symbol"]) for row in selections],
            "strategies": sorted({str(row["strategy_version"]) for row in selections}),
            "per_pair": serialize(per_pair),
            "per_strategy": serialize(per_strategy),
            "finalized_at": now.isoformat(),
        }

    @staticmethod
    def _summary(metrics: dict[str, object]) -> str:
        pnl = Decimal(str(metrics["pnl_ntd"]))
        performance = "performed well" if pnl > 0 else "performed poorly" if pnl < 0 else "was flat"
        pair_groups = metrics.get("per_pair", {})
        strategy_groups = metrics.get("per_strategy", {})
        def extrema(groups: object) -> str:
            if not isinstance(groups, dict) or not groups:
                return "none"
            ranked = sorted(
                groups,
                key=lambda key: (Decimal(str(groups[key]["realized_pnl_ntd"])), key),
            )
            return f"best {ranked[-1]}, worst {ranked[0]}"
        return (
            f"Round {performance}: P&L {_text(pnl)} NTD; "
            f"{metrics['win_count']} wins, {metrics['loss_count']} losses, "
            f"{metrics['rejected_action_count']} rejected actions. "
            f"Pairs: {extrema(pair_groups)}. Strategies: {extrema(strategy_groups)}."
        )


