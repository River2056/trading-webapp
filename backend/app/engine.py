from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from .database import Database
from .market_data import Candle, MarketData, MarketDataError, MarketSummary, NtdConversion


class RoundPlanningError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoundPlanningSettings:
    candle_interval: str
    backtest_lookback_candles: int
    minimum_liquidity_ntd: Decimal
    fee_pct: Decimal
    slippage_pct: Decimal
    max_position_allocation_pct: Decimal
    max_concurrent_positions: int
    stop_loss_pct: Decimal
    take_profit_pct: Decimal
    daily_loss_limit_pct: Decimal
    minimum_net_return_pct: Decimal = Decimal("0")
    minimum_entry_count: int = 1
    minimum_trade_count: int = 2
    max_conversion_age_seconds: int = 86400
    max_candle_age_seconds: int = 7200

    @classmethod
    def from_mapping(cls, values: dict[str, object]) -> RoundPlanningSettings:
        return cls(
            candle_interval=str(values["candle_interval"]),
            backtest_lookback_candles=int(str(values["backtest_lookback_candles"])),
            minimum_liquidity_ntd=Decimal(str(values["minimum_liquidity_ntd"])),
            fee_pct=Decimal(str(values["fee_pct"])),
            slippage_pct=Decimal(str(values["slippage_pct"])),
            max_position_allocation_pct=Decimal(str(values["max_position_allocation_pct"])),
            max_concurrent_positions=int(str(values["max_concurrent_positions"])),
            stop_loss_pct=Decimal(str(values["stop_loss_pct"])),
            take_profit_pct=Decimal(str(values["take_profit_pct"])),
            daily_loss_limit_pct=Decimal(str(values["daily_loss_limit_pct"])),
            minimum_net_return_pct=Decimal(str(values.get("minimum_net_return_pct", "0"))),
            minimum_entry_count=int(str(values.get("minimum_entry_count", 1))),
            minimum_trade_count=int(str(values.get("minimum_trade_count", 2))),
            max_conversion_age_seconds=int(str(values.get("max_conversion_age_seconds", 86400))),
            max_candle_age_seconds=int(str(values.get("max_candle_age_seconds", 7200))),
        )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    def __getitem__(self, key: str) -> object:
        if key not in self.__dataclass_fields__:
            raise KeyError(key)
        return getattr(self, key)


@dataclass(frozen=True)
class BacktestResult:
    strategy_version: str
    configuration: dict[str, object]
    assumptions: dict[str, object]
    net_return_pct: Decimal
    total_cost_pct: Decimal
    trade_count: int
    entry_count: int
    exit_count: int
    candle_count: int

    @property
    def qualified(self) -> bool:
        return self.entry_count > 0 and self.net_return_pct > 0

    def qualifies(self, settings: RoundPlanningSettings) -> bool:
        return (
            self.entry_count >= settings.minimum_entry_count
            and self.trade_count >= settings.minimum_trade_count
            and self.net_return_pct > settings.minimum_net_return_pct
        )

    @property
    def score(self) -> Decimal:
        return self.net_return_pct

    @property
    def metrics(self) -> dict[str, object]:
        return {
            "net_return_pct": _decimal_text(self.net_return_pct),
            "total_cost_pct": _decimal_text(self.total_cost_pct),
            "candle_count": self.candle_count,
            "trade_count": self.trade_count,
            "entry_count": self.entry_count,
            "exit_count": self.exit_count,
        }


class Strategy(Protocol):
    @property
    def version(self) -> str: ...

    @property
    def configuration(self) -> dict[str, object]: ...

    def signals(self, candles: list[Candle]) -> list[int]:
        """Return 1 for entry, -1 for exit, and 0 otherwise for each candle."""
        ...


@dataclass(frozen=True)
class RsiStrategy:
    period: int = 14
    entry_below: Decimal = Decimal("30")
    exit_above: Decimal = Decimal("70")
    version: str = "rsi-v1"

    @property
    def configuration(self) -> dict[str, object]:
        return {
            "period": self.period,
            "entry_below": int(self.entry_below),
            "exit_above": int(self.exit_above),
        }

    def signals(self, candles: list[Candle]) -> list[int]:
        closes = [candle.close for candle in candles]
        changes = [right - left for left, right in zip(closes, closes[1:], strict=False)]
        values: list[Decimal | None] = [None] * len(closes)
        for index in range(self.period, len(closes)):
            window = changes[index - self.period : index]
            average_gain = sum((change for change in window if change > 0), Decimal()) / self.period
            average_loss = (
                -sum((change for change in window if change < 0), Decimal()) / self.period
            )
            if average_loss == 0:
                values[index] = Decimal("100") if average_gain > 0 else Decimal("50")
            else:
                values[index] = Decimal("100") - Decimal("100") / (
                    Decimal("1") + average_gain / average_loss
                )
        signals = [0] * len(closes)
        for index, value in enumerate(values):
            if value is not None and value < self.entry_below:
                signals[index] = 1
            elif value is not None and value > self.exit_above:
                signals[index] = -1
        return signals


@dataclass(frozen=True)
class MacdStrategy:
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    version: str = "macd-v1"

    @property
    def configuration(self) -> dict[str, object]:
        return {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "signal_period": self.signal_period,
        }

    @staticmethod
    def _ema(values: list[Decimal], period: int) -> list[Decimal]:
        multiplier = Decimal("2") / Decimal(period + 1)
        ema = values[0]
        result = [ema]
        for value in values[1:]:
            ema = (value - ema) * multiplier + ema
            result.append(ema)
        return result

    def signals(self, candles: list[Candle]) -> list[int]:
        closes = [candle.close for candle in candles]
        fast = self._ema(closes, self.fast_period)
        slow = self._ema(closes, self.slow_period)
        macd = [fast_value - slow_value for fast_value, slow_value in zip(fast, slow, strict=True)]
        signal_line = self._ema(macd, self.signal_period)
        result = [0] * len(closes)
        for index in range(1, len(closes)):
            previous = macd[index - 1] - signal_line[index - 1]
            current = macd[index] - signal_line[index]
            if index >= self.slow_period - 1 and previous <= 0 < current:
                result[index] = 1
            elif index >= self.slow_period - 1 and previous >= 0 > current:
                result[index] = -1
        return result


@dataclass(frozen=True)
class Backtester:
    fee_pct: Decimal
    slippage_pct: Decimal

    def run(self, strategy: Strategy, candles: list[Candle]) -> BacktestResult:
        if len(candles) < 30:
            raise RoundPlanningError("insufficient historical candles")
        fee = self.fee_pct / 100
        slippage = self.slippage_pct / 100
        cash = Decimal("1")
        quantity = Decimal()
        fills = entries = exits = 0
        total_cost = Decimal()
        for candle, signal in zip(candles, strategy.signals(candles), strict=True):
            if signal == 1 and quantity == 0:
                market_notional = cash / ((Decimal("1") + slippage) * (Decimal("1") + fee))
                quantity = market_notional / candle.close
                total_cost += market_notional * (slippage + fee)
                cash = Decimal()
                fills += 1
                entries += 1
            elif signal == -1 and quantity > 0:
                market_notional = quantity * candle.close
                total_cost += market_notional * (slippage + fee)
                cash = market_notional * (Decimal("1") - slippage) * (Decimal("1") - fee)
                quantity = Decimal()
                fills += 1
                exits += 1
        if quantity > 0:
            market_notional = quantity * candles[-1].close
            total_cost += market_notional * (slippage + fee)
            cash = market_notional * (Decimal("1") - slippage) * (Decimal("1") - fee)
            fills += 1
            exits += 1
        return BacktestResult(
            strategy_version=strategy.version,
            configuration=strategy.configuration,
            assumptions={
                "long_only": True,
                "fill": "candle_close",
                "fee_pct": self.fee_pct,
                "slippage_pct": self.slippage_pct,
                "indicator": strategy.configuration,
            },
            net_return_pct=(cash - Decimal("1")) * 100,
            total_cost_pct=total_cost * 100,
            trade_count=fills,
            entry_count=entries,
            exit_count=exits,
            candle_count=len(candles),
        )


@dataclass(frozen=True)
class Selection:
    symbol: str
    strategy_version: str
    strategy_config: dict[str, object]


@dataclass(frozen=True)
class RoundPlan:
    round_id: int
    selections: tuple[Selection, ...]
    frozen_settings: RoundPlanningSettings


def _json(value: object) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


class TradingEngine:
    def __init__(
        self, database: Database, market_data: MarketData, clock: Callable[[], datetime]
    ) -> None:
        self.database = database
        self.market_data = market_data
        self.clock = clock
        self.last_exclusions: dict[str, str] = {}

    def activate_round(self, settings: RoundPlanningSettings | dict[str, object]) -> RoundPlan:
        if not isinstance(settings, RoundPlanningSettings):
            settings = RoundPlanningSettings.from_mapping(settings)
        now = self.clock().isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.database.connect() as connection, connection:
            cursor = connection.execute(
                "INSERT INTO trading_round(status, started_at, frozen_settings_json) "
                "VALUES(?, ?, ?)",
                ("planning", now, _json(settings.as_dict())),
            )
            if cursor.lastrowid is None:
                raise RoundPlanningError("failed to create round")
            round_id = cursor.lastrowid

        try:
            return self._plan_round(round_id, settings)
        except (MarketDataError, RoundPlanningError, ValueError, KeyError) as error:
            with self.database.connect() as connection, connection:
                connection.execute(
                    "INSERT INTO planning_failures"
                    "(round_id, occurred_at, reason, active) VALUES (?, ?, ?, 1)",
                    (round_id, now, str(error)),
                )
                connection.execute(
                    "UPDATE trading_round SET status='failed' WHERE id=?", (round_id,)
                )
            if isinstance(error, RoundPlanningError):
                raise
            raise RoundPlanningError(str(error)) from error

    def _plan_round(self, round_id: int, settings: RoundPlanningSettings) -> RoundPlan:
        summaries = sorted(self.market_data.market_summaries(), key=lambda item: item.symbol)
        self.last_exclusions = {}
        ranked: list[tuple[MarketSummary, NtdConversion, Decimal]] = []
        minimum = settings.minimum_liquidity_ntd
        for summary in summaries:
            if summary.last_price <= 0:
                self.last_exclusions[summary.symbol] = "invalid price: must be positive"
                continue
            if summary.quote_volume < 0:
                self.last_exclusions[summary.symbol] = "invalid quote volume: must not be negative"
                continue
            try:
                conversion = self.market_data.ntd_conversion(summary.quote_asset)
            except MarketDataError as error:
                self.last_exclusions[summary.symbol] = str(error)
                continue
            if conversion.rate <= 0:
                self.last_exclusions[summary.symbol] = "invalid NTD conversion rate"
                continue
            if conversion.provenance is None:
                self.last_exclusions[summary.symbol] = "missing structured conversion provenance"
                continue
            if any(
                (self.clock() - leg.observed_at).total_seconds()
                > settings.max_conversion_age_seconds
                for leg in (conversion.provenance.stablecoin, conversion.provenance.fx)
            ):
                self.last_exclusions[summary.symbol] = "stale NTD conversion leg"
                continue
            liquidity = summary.quote_volume * conversion.rate
            if liquidity < minimum:
                self.last_exclusions[summary.symbol] = "below minimum NTD liquidity"
                continue
            ranked.append((summary, conversion, liquidity))
        ranked.sort(key=lambda item: (-item[2], item[0].symbol))
        rank_by_symbol = {item[0].symbol: rank for rank, item in enumerate(ranked, 1)}
        interval = settings.candle_interval
        lookback = settings.backtest_lookback_candles
        backtester = Backtester(settings.fee_pct, settings.slippage_pct)
        strategies: list[Strategy] = [RsiStrategy(), MacdStrategy()]
        selections: list[Selection] = []

        with self.database.connect() as connection, connection:
            for summary in summaries:
                eligible = next((item for item in ranked if item[0].symbol == summary.symbol), None)
                persisted_conversion = eligible[1] if eligible else None
                persisted_liquidity = eligible[2] if eligible else None
                rank = rank_by_symbol.get(summary.symbol)
                connection.execute(
                    "INSERT INTO market_rankings VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        round_id,
                        summary.symbol,
                        summary.observed_at.isoformat(),
                        summary.base_asset,
                        summary.quote_asset,
                        _decimal_text(summary.last_price),
                        _decimal_text(summary.quote_volume),
                        persisted_conversion.path if persisted_conversion else None,
                        _decimal_text(persisted_conversion.rate) if persisted_conversion else None,
                        persisted_conversion.observed_at.isoformat()
                        if persisted_conversion
                        else None,
                        _json(asdict(persisted_conversion.provenance))
                        if persisted_conversion and persisted_conversion.provenance
                        else None,
                        _decimal_text(persisted_liquidity) if persisted_liquidity else None,
                        _decimal_text(persisted_liquidity) if persisted_liquidity else None,
                        rank,
                        0,
                        self.last_exclusions.get(summary.symbol),
                    ),
                )

            for summary, _conversion, _liquidity in ranked:
                try:
                    candles = self.market_data.historical_candles(
                        summary.symbol, interval, lookback
                    )
                    candle_age = (self.clock() - candles[-1].opened_at).total_seconds()
                    if candle_age > settings.max_candle_age_seconds:
                        raise MarketDataError("stale candidate candles")
                    results = [backtester.run(strategy, candles) for strategy in strategies]
                except (MarketDataError, RoundPlanningError, ValueError, IndexError) as error:
                    self.last_exclusions[summary.symbol] = str(error)
                    connection.execute(
                        "UPDATE market_rankings SET exclusion_reason=? "
                        "WHERE round_id=? AND symbol=?",
                        (str(error), round_id, summary.symbol),
                    )
                    continue
                canonical_rows = [
                    [
                        candle.opened_at.isoformat(),
                        _decimal_text(candle.open),
                        _decimal_text(candle.high),
                        _decimal_text(candle.low),
                        _decimal_text(candle.close),
                        _decimal_text(candle.volume),
                    ]
                    for candle in candles
                ]
                provider = getattr(self.market_data, "provider", "fixture-market-data")
                candle_provenance = {
                    "source": provider,
                    "provider": provider,
                    "symbol": summary.symbol,
                    "interval": interval,
                    "requested_count": lookback,
                    "actual_count": len(candles),
                    "first_candle_at": candles[0].opened_at.isoformat(),
                    "last_candle_at": candles[-1].opened_at.isoformat(),
                    "first_timestamp": candles[0].opened_at.isoformat(),
                    "last_timestamp": candles[-1].opened_at.isoformat(),
                    "sha256": hashlib.sha256(_json(canonical_rows).encode()).hexdigest(),
                }
                for result in results:
                    assumptions = dict(result.assumptions)
                    assumptions["candles"] = candle_provenance
                    assumptions["qualification"] = {
                        "minimum_net_return_pct": _decimal_text(settings.minimum_net_return_pct),
                        "minimum_entry_count": settings.minimum_entry_count,
                        "minimum_trade_count": settings.minimum_trade_count,
                    }
                    assumptions["qualification_gates"] = assumptions["qualification"]
                    connection.execute(
                        "INSERT INTO backtest_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            round_id,
                            summary.symbol,
                            result.strategy_version,
                            _json(assumptions),
                            _json(result.metrics),
                            int(result.qualifies(settings)),
                            _decimal_text(result.score),
                        ),
                    )
                qualified = sorted(
                    (result for result in results if result.qualifies(settings)),
                    key=lambda result: (-result.score, result.strategy_version),
                )
                if not qualified:
                    connection.execute(
                        "UPDATE market_rankings SET exclusion_reason=? "
                        "WHERE round_id=? AND symbol=?",
                        ("no qualifying strategy", round_id, summary.symbol),
                    )
                    continue
                chosen = qualified[0]
                selection = Selection(summary.symbol, chosen.strategy_version, chosen.configuration)
                selections.append(selection)
                selection_rank = len(selections)
                connection.execute(
                    "UPDATE market_rankings SET selected=1 WHERE round_id=? AND symbol=?",
                    (round_id, summary.symbol),
                )
                connection.execute(
                    "INSERT INTO round_selections VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        round_id,
                        summary.symbol,
                        selection_rank,
                        selection.strategy_version,
                        _json(selection.strategy_config),
                        _json({"candles": candle_provenance, "metrics": chosen.metrics}),
                    ),
                )
                # Continue through the eligible universe so every candidate's
                # backtest evidence is retained. Only the first five qualifiers
                # become selections.
                if len(selections) > 5:
                    selections.pop()
                    connection.execute(
                        "UPDATE market_rankings SET selected=0 WHERE round_id=? AND symbol=?",
                        (round_id, summary.symbol),
                    )
                    connection.execute(
                        "DELETE FROM round_selections WHERE round_id=? AND symbol=?",
                        (round_id, summary.symbol),
                    )
            enough_selections = len(selections) >= 5
            if enough_selections:
                connection.execute(
                    "UPDATE trading_round SET status='active' WHERE id=?", (round_id,)
                )
                connection.execute("UPDATE planning_failures SET active=0 WHERE active=1")
        if not enough_selections:
            raise RoundPlanningError(
                "round requires at least five markets with qualifying strategies"
            )
        return RoundPlan(round_id, tuple(selections), settings)
