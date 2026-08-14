from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

from .database import Database
from .market_data import (
    Candle,
    MarketData,
    MarketDataError,
    MarketRules,
    MarketSummary,
    NtdConversion,
)


class RoundPlanningError(RuntimeError):
    pass


class UnfundablePlanningError(RoundPlanningError):
    """Every otherwise-qualified candidate failed the executable-quantity gate."""


class MarketDataSafetyError(RuntimeError):
    """Required execution data was unsafe; the enclosing transaction must roll back."""


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
    strategy_cadence_seconds: int = 300
    starting_capital_ntd: Decimal = Decimal("5000")
    minimum_net_return_pct: Decimal = Decimal("0")
    minimum_entry_count: int = 1
    minimum_trade_count: int = 2
    max_conversion_age_seconds: int = 86400
    max_candle_age_seconds: int = 7200
    round_duration_days: int = 7

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
            strategy_cadence_seconds=int(str(values.get("strategy_cadence_seconds", 300))),
            starting_capital_ntd=Decimal(str(values.get("starting_capital_ntd", "5000"))),
            minimum_net_return_pct=Decimal(str(values.get("minimum_net_return_pct", "0"))),
            minimum_entry_count=int(str(values.get("minimum_entry_count", 1))),
            minimum_trade_count=int(str(values.get("minimum_trade_count", 2))),
            max_conversion_age_seconds=int(str(values.get("max_conversion_age_seconds", 86400))),
            max_candle_age_seconds=int(str(values.get("max_candle_age_seconds", 7200))),
            round_duration_days=int(str(values.get("round_duration_days", 7))),
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
class BollingerBandStrategy:
    period: int = 20
    standard_deviations: Decimal = Decimal("2")
    version: str = "bollinger-v1"

    @property
    def configuration(self) -> dict[str, object]:
        return {
            "period": self.period,
            "standard_deviations": self.standard_deviations,
        }

    def signals(self, candles: list[Candle]) -> list[int]:
        closes = [candle.close for candle in candles]
        signals = [0] * len(closes)
        for index in range(self.period - 1, len(closes)):
            window = closes[index - self.period + 1 : index + 1]
            mean = sum(window, Decimal()) / self.period
            variance = sum(((value - mean) ** 2 for value in window), Decimal()) / self.period
            lower_band = mean - self.standard_deviations * variance.sqrt()
            if closes[index] < lower_band:
                signals[index] = 1
            elif closes[index] > mean:
                signals[index] = -1
        return signals


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


@dataclass(frozen=True)
class TradingDecision:
    symbol: str
    action: str
    outcome: str
    reason: str
    signal_id: str


@dataclass(frozen=True)
class PortfolioAccount:
    cash_ntd: Decimal
    position_value_ntd: Decimal
    realized_pnl_ntd: Decimal
    unrealized_pnl_ntd: Decimal
    costs_ntd: Decimal
    available_capital_ntd: Decimal
    total_equity_ntd: Decimal


@dataclass(frozen=True)
class EvaluationResult:
    decisions: tuple[TradingDecision, ...]
    account: PortfolioAccount


@dataclass(frozen=True)
class _ValidatedPrice:
    price_ntd: Decimal
    source_timestamp: datetime
    evidence: dict[str, object]


@dataclass(frozen=True)
class _PreparedDecision:
    symbol: str
    source_timestamp: datetime
    strategy_version: str
    action: str
    reason: str
    evidence: dict[str, object]
    price_ntd: Decimal


def _json(value: object) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def executable_quantity(
    equity: Decimal,
    market_price_ntd: Decimal,
    settings: RoundPlanningSettings | dict[str, object],
    rules: MarketRules | None = None,
) -> Decimal:
    """Authoritative executable entry quantity used by planning and execution."""
    values = settings.as_dict() if isinstance(settings, RoundPlanningSettings) else settings
    if (
        not equity.is_finite()
        or equity <= 0
        or not market_price_ntd.is_finite()
        or market_price_ntd <= 0
    ):
        return Decimal()
    allocation = Decimal(str(values["max_position_allocation_pct"])) / 100
    fee = Decimal(str(values["fee_pct"])) / 100
    slippage = Decimal(str(values["slippage_pct"])) / 100
    budget = equity * allocation
    raw = budget / (1 + fee) / (market_price_ntd * (1 + slippage))
    step = rules.step_size if rules is not None else Decimal("0.000001")
    quantity = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
    if rules is not None and (
        quantity < rules.min_quantity
        or quantity * market_price_ntd < rules.min_notional_ntd
    ):
        return Decimal()
    return quantity


def _strategy_from_record(version: str, configuration_json: str) -> Strategy:
    configuration = json.loads(configuration_json)
    if version == "rsi-v1":
        return RsiStrategy(
            period=int(configuration["period"]),
            entry_below=Decimal(str(configuration["entry_below"])),
            exit_above=Decimal(str(configuration["exit_above"])),
        )
    if version == "macd-v1":
        return MacdStrategy(
            fast_period=int(configuration["fast_period"]),
            slow_period=int(configuration["slow_period"]),
            signal_period=int(configuration["signal_period"]),
        )
    if version == "bollinger-v1":
        return BollingerBandStrategy(
            period=int(configuration["period"]),
            standard_deviations=Decimal(str(configuration["standard_deviations"])),
        )
    raise RoundPlanningError(f"unsupported strategy version: {version}")


class TradingEngine:
    def __init__(
        self,
        database: Database,
        market_data: MarketData,
        clock: Callable[[], datetime],
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.database = database
        self.market_data = market_data
        self.clock = clock
        self.fault_injector = fault_injector
        self.last_exclusions: dict[str, str] = {}

    def evaluate_active_round(
        self,
        *,
        require_safe_data: bool = False,
        connection: sqlite3.Connection | None = None,
        completion_hook: Callable[[sqlite3.Connection], None] | None = None,
    ) -> EvaluationResult:
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if connection is None:
            with self.database.connect() as owned_connection, owned_connection:
                return self._evaluate_active_round_in_transaction(
                    owned_connection, now, require_safe_data, completion_hook
                )
        return self._evaluate_active_round_in_transaction(
            connection, now, require_safe_data, completion_hook
        )

    def _evaluate_active_round_in_transaction(
        self,
        connection: sqlite3.Connection,
        now: datetime,
        require_safe_data: bool,
        completion_hook: Callable[[sqlite3.Connection], None] | None,
    ) -> EvaluationResult:
            run_row = connection.execute(
                "SELECT desired_state FROM trading_run ORDER BY id LIMIT 1"
            ).fetchone()
            if run_row is None or str(run_row["desired_state"]) != "running":
                raise RoundPlanningError("trading run is stopped")
            round_row = connection.execute(
                "SELECT id, frozen_settings_json FROM trading_round "
                "WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if round_row is None:
                raise RoundPlanningError("no active round")
            round_id = int(round_row["id"])
            settings = json.loads(str(round_row["frozen_settings_json"]))
            cadence = int(settings.get("strategy_cadence_seconds", 300))
            interval_number = int(now.timestamp()) // cadence
            interval_key = f"{cadence}:{interval_number}"
            prior = connection.execute(
                "SELECT COUNT(*) FROM trading_signals WHERE round_id=? AND interval_key=?",
                (round_id, interval_key),
            ).fetchone()[0]
            if prior:
                snapshot = connection.execute(
                    "SELECT * FROM portfolio_snapshots WHERE round_id=? AND interval_key=?",
                    (round_id, interval_key),
                ).fetchone()
                if completion_hook is not None:
                    completion_hook(connection)
                return EvaluationResult(
                    (),
                    PortfolioAccount(
                        *(
                            Decimal(str(snapshot[column]))
                            for column in (
                                "cash_ntd",
                                "position_value_ntd",
                                "realized_pnl_ntd",
                                "unrealized_pnl_ntd",
                                "costs_ntd",
                                "available_capital_ntd",
                                "total_equity_ntd",
                            )
                        )
                    ),
                )

            summaries = {item.symbol: item for item in self.market_data.market_summaries()}
            selections = connection.execute(
                "SELECT symbol, strategy_version, strategy_config_json FROM round_selections "
                "WHERE round_id=? ORDER BY selection_rank",
                (round_id,),
            ).fetchall()
            decisions: list[TradingDecision] = []
            current_prices: dict[str, Decimal] = {}
            validated_prices: dict[str, _ValidatedPrice] = {}
            prepared: list[_PreparedDecision] = []
            unsafe_reasons: list[str] = []

            # Phase one: collect and validate all valuations before any fill can be sized.
            for selection in selections:
                symbol = str(selection["symbol"])
                strategy_version = str(selection["strategy_version"])
                summary = summaries.get(symbol)
                if summary is None:
                    unsafe_reasons.append(f"{symbol}: market summary unavailable")
                    decisions.append(
                        self._persist_rejection(
                            connection,
                            round_id,
                            symbol,
                            interval_key,
                            now,
                            now,
                            strategy_version,
                            "hold",
                            "market price unavailable",
                            {
                                "source_timestamp_available": False,
                                "source_timestamp_unavailable_reason": "market summary unavailable",
                            },
                        )
                    )
                    continue
                try:
                    conversion = self.market_data.ntd_conversion(summary.quote_asset)
                except MarketDataError as error:
                    unsafe_reasons.append(f"{symbol}: {error}")
                    decisions.append(
                        self._persist_rejection(
                            connection,
                            round_id,
                            symbol,
                            interval_key,
                            now,
                            summary.observed_at,
                            strategy_version,
                            "hold",
                            str(error),
                            {
                                "market_price": _decimal_text(summary.last_price),
                                "price_observed_at": summary.observed_at.isoformat(),
                                "quote_asset": summary.quote_asset,
                                "market_data_error": str(error),
                            },
                        )
                    )
                    continue
                price_ntd = summary.last_price * conversion.rate
                source_timestamp = min(summary.observed_at, conversion.observed_at)
                evidence: dict[str, object] = {
                    "market_price": _decimal_text(summary.last_price),
                    "market_price_ntd": _decimal_text(price_ntd),
                    "conversion_rate": _decimal_text(conversion.rate),
                    "conversion_path": conversion.path,
                    "price_observed_at": summary.observed_at.isoformat(),
                    "conversion_observed_at": conversion.observed_at.isoformat(),
                }
                reason: str | None = None
                rejected_timestamp = source_timestamp
                if not all(
                    value.is_finite()
                    for value in (
                        summary.last_price,
                        summary.quote_volume,
                        conversion.rate,
                        price_ntd,
                    )
                ):
                    reason = "market price or conversion rate is not finite"
                elif summary.observed_at > now:
                    reason = "market price timestamp is in the future"
                    rejected_timestamp = summary.observed_at
                elif conversion.observed_at > now:
                    reason = "NTD conversion timestamp is in the future"
                    rejected_timestamp = conversion.observed_at
                elif price_ntd <= 0:
                    reason = "market price or conversion rate is not positive"
                elif (now - summary.observed_at).total_seconds() > int(
                    settings.get("max_candle_age_seconds", 7200)
                ):
                    reason = "market price is stale"
                    rejected_timestamp = summary.observed_at
                elif (now - conversion.observed_at).total_seconds() > int(
                    settings.get("max_conversion_age_seconds", 86400)
                ):
                    reason = "NTD conversion is stale"
                    rejected_timestamp = conversion.observed_at
                if reason is not None:
                    unsafe_reasons.append(f"{symbol}: {reason}")
                    decisions.append(
                        self._persist_rejection(
                            connection,
                            round_id,
                            symbol,
                            interval_key,
                            now,
                            rejected_timestamp,
                            strategy_version,
                            "hold",
                            reason,
                            evidence,
                        )
                    )
                    continue
                current_prices[symbol] = price_ntd
                validated_prices[symbol] = _ValidatedPrice(price_ntd, source_timestamp, evidence)

            # Phase two: evaluate signals against the complete validated price map.
            sizing_account = self._account(connection, round_id, settings, current_prices)
            for selection in selections:
                symbol = str(selection["symbol"])
                validated = validated_prices.get(symbol)
                if validated is None:
                    continue
                strategy_version = str(selection["strategy_version"])
                price_ntd = validated.price_ntd
                source_timestamp = validated.source_timestamp
                evidence = dict(validated.evidence)
                requested_candle_count = int(settings["backtest_lookback_candles"])
                try:
                    candles = self.market_data.historical_candles(
                        symbol,
                        str(settings["candle_interval"]),
                        requested_candle_count,
                    )
                except MarketDataError as error:
                    evidence["market_data_error"] = str(error)
                    unsafe_reasons.append(f"{symbol}: {error}")
                    decisions.append(
                        self._persist_rejection(
                            connection,
                            round_id,
                            symbol,
                            interval_key,
                            now,
                            source_timestamp,
                            strategy_version,
                            "hold",
                            str(error),
                            evidence,
                        )
                    )
                    continue
                if len(candles) != requested_candle_count:
                    reason = (
                        "incomplete required candles: "
                        f"expected {requested_candle_count}, received {len(candles)}"
                    )
                    unsafe_reasons.append(f"{symbol}: {reason}")
                    decisions.append(
                        self._persist_rejection(
                            connection,
                            round_id,
                            symbol,
                            interval_key,
                            now,
                            source_timestamp,
                            strategy_version,
                            "hold",
                            reason,
                            evidence,
                        )
                    )
                    continue
                candle_timestamp = candles[-1].opened_at
                if any(
                    left.opened_at >= right.opened_at
                    for left, right in zip(candles, candles[1:], strict=False)
                ):
                    reason = "out-of-order required candles"
                    unsafe_reasons.append(f"{symbol}: {reason}")
                    decisions.append(
                        self._persist_rejection(
                            connection,
                            round_id,
                            symbol,
                            interval_key,
                            now,
                            candle_timestamp,
                            strategy_version,
                            "hold",
                            reason,
                            evidence,
                        )
                    )
                    continue
                if any(
                    not all(
                        value.is_finite()
                        for value in (
                            candle.open,
                            candle.high,
                            candle.low,
                            candle.close,
                            candle.volume,
                        )
                    )
                    or candle.close <= 0
                    or candle.open <= 0
                    or candle.high <= 0
                    or candle.low <= 0
                    or candle.volume < 0
                    or candle.low > min(candle.open, candle.close)
                    or max(candle.open, candle.close) > candle.high
                    or candle.low > candle.high
                    for candle in candles
                ):
                    reason = "malformed required candles"
                    unsafe_reasons.append(f"{symbol}: {reason}")
                    decisions.append(
                        self._persist_rejection(
                            connection,
                            round_id,
                            symbol,
                            interval_key,
                            now,
                            candle_timestamp,
                            strategy_version,
                            "hold",
                            reason,
                            evidence,
                        )
                    )
                    continue
                if candle_timestamp > now:
                    reason = "required candle timestamp is in the future"
                    unsafe_reasons.append(f"{symbol}: {reason}")
                    decisions.append(
                        self._persist_rejection(
                            connection,
                            round_id,
                            symbol,
                            interval_key,
                            now,
                            candle_timestamp,
                            strategy_version,
                            "hold",
                            reason,
                            evidence,
                        )
                    )
                    continue
                if (now - candle_timestamp).total_seconds() > int(
                    settings.get("max_candle_age_seconds", 7200)
                ):
                    unsafe_reasons.append(f"{symbol}: signal candles are stale")
                    decisions.append(
                        self._persist_rejection(
                            connection,
                            round_id,
                            symbol,
                            interval_key,
                            now,
                            candle_timestamp,
                            strategy_version,
                            "hold",
                            "signal candles are stale",
                            evidence,
                        )
                    )
                    continue
                strategy = _strategy_from_record(
                    strategy_version, str(selection["strategy_config_json"])
                )
                raw_signal = strategy.signals(candles)[-1]
                position = connection.execute(
                    "SELECT * FROM paper_positions WHERE round_id=? AND symbol=?",
                    (round_id, symbol),
                ).fetchone()
                action = "buy" if raw_signal == 1 else "sell" if raw_signal == -1 else "hold"
                reason = "strategy entry signal" if action == "buy" else "strategy exit signal"
                if position is not None:
                    entry_price = Decimal(str(position["entry_price_ntd"]))
                    change_pct = (price_ntd / entry_price - 1) * 100
                    if change_pct <= -Decimal(str(settings["stop_loss_pct"])):
                        action, reason = "sell", "stop-loss threshold reached"
                    elif change_pct >= Decimal(str(settings["take_profit_pct"])):
                        action, reason = "sell", "take-profit threshold reached"
                evidence.update(
                    {
                        "candle_timestamp": candle_timestamp.isoformat(),
                        "candle_close": _decimal_text(candles[-1].close),
                        "raw_signal": raw_signal,
                        "strategy_config": json.loads(str(selection["strategy_config_json"])),
                    }
                )
                source_timestamp = min(source_timestamp, candle_timestamp)
                prepared.append(
                    _PreparedDecision(
                        symbol,
                        source_timestamp,
                        strategy_version,
                        action,
                        reason,
                        evidence,
                        price_ntd,
                    )
                )

            # Required data is all-or-nothing: transaction rollback removes every
            # rejection/fill prepared in this interval before the worker records pause.
            if require_safe_data and unsafe_reasons:
                raise MarketDataSafetyError("; ".join(unsafe_reasons))

            # Realize every same-cadence exit before applying entry risk limits. Stable
            # partitioning preserves selection order within each execution class.
            for item in sorted(prepared, key=lambda item: item.action != "sell"):
                decisions.append(
                    self._execute_decision(
                        connection,
                        round_id,
                        item.symbol,
                        interval_key,
                        now,
                        item.source_timestamp,
                        item.strategy_version,
                        item.action,
                        item.reason,
                        item.evidence,
                        item.price_ntd,
                        settings,
                        current_prices,
                        sizing_account,
                    )
                )

            account = self._account(connection, round_id, settings, current_prices)
            connection.execute(
                "INSERT INTO portfolio_snapshots VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    round_id,
                    interval_key,
                    now.isoformat(),
                    _decimal_text(account.cash_ntd),
                    _decimal_text(account.position_value_ntd),
                    _decimal_text(account.realized_pnl_ntd),
                    _decimal_text(account.unrealized_pnl_ntd),
                    _decimal_text(account.costs_ntd),
                    _decimal_text(account.available_capital_ntd),
                    _decimal_text(account.total_equity_ntd),
                ),
            )
            if completion_hook is not None:
                completion_hook(connection)
            return EvaluationResult(tuple(decisions), account)

    def _execute_decision(
        self,
        connection: sqlite3.Connection,
        round_id: int,
        symbol: str,
        interval_key: str,
        now: datetime,
        source_timestamp: datetime,
        strategy_version: str,
        action: str,
        reason: str,
        evidence: dict[str, object],
        price_ntd: Decimal,
        settings: dict[str, object],
        current_prices: dict[str, Decimal],
        sizing_account: PortfolioAccount,
    ) -> TradingDecision:
        signal_id = hashlib.sha256(
            f"{round_id}:{symbol}:{interval_key}:{strategy_version}:{action}".encode()
        ).hexdigest()
        position = connection.execute(
            "SELECT * FROM paper_positions WHERE round_id=? AND symbol=?", (round_id, symbol)
        ).fetchone()
        rejection: str | None = None
        if action == "buy":
            if position is not None:
                rejection = "position already open"
            elif connection.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE round_id=?", (round_id,)
            ).fetchone()[0] >= int(str(settings["max_concurrent_positions"])):
                rejection = "maximum concurrent positions reached"
            elif self._daily_loss_limit_reached(connection, round_id, now, settings):
                rejection = "daily loss limit reached"
            elif self._buy_quantity(
                connection,
                round_id,
                symbol,
                price_ntd,
                settings,
                current_prices,
                sizing_account,
            ) <= 0:
                rejection = "below minimum executable quantity"
        elif action == "sell" and position is None:
            rejection = "no open position"
        if action == "hold":
            outcome, persisted_reason = "observed", "no actionable strategy signal"
        elif rejection:
            outcome, persisted_reason = "rejected", rejection
        else:
            outcome, persisted_reason = "filled", reason
        connection.execute(
            "INSERT INTO trading_signals VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                round_id,
                symbol,
                interval_key,
                signal_id,
                now.isoformat(),
                source_timestamp.isoformat(),
                strategy_version,
                action,
                outcome,
                persisted_reason,
                _json(evidence),
            ),
        )
        if self.fault_injector is not None:
            self.fault_injector("after_signal_insert")
        if outcome == "filled":
            self._fill(
                connection,
                round_id,
                signal_id,
                symbol,
                action,
                price_ntd,
                now,
                source_timestamp,
                strategy_version,
                persisted_reason,
                settings,
                current_prices,
                sizing_account,
            )
        return TradingDecision(symbol, action, outcome, persisted_reason, signal_id)

    def _persist_rejection(
        self,
        connection: sqlite3.Connection,
        round_id: int,
        symbol: str,
        interval_key: str,
        now: datetime,
        source_timestamp: datetime,
        strategy_version: str,
        action: str,
        reason: str,
        evidence: dict[str, object],
    ) -> TradingDecision:
        signal_id = hashlib.sha256(
            f"{round_id}:{symbol}:{interval_key}:{strategy_version}:{action}".encode()
        ).hexdigest()
        connection.execute(
            "INSERT INTO trading_signals VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'rejected', ?, ?)",
            (
                round_id,
                symbol,
                interval_key,
                signal_id,
                now.isoformat(),
                source_timestamp.isoformat(),
                strategy_version,
                action,
                reason,
                _json(evidence),
            ),
        )
        return TradingDecision(symbol, action, "rejected", reason, signal_id)

    def _fill(
        self,
        connection: sqlite3.Connection,
        round_id: int,
        signal_id: str,
        symbol: str,
        side: str,
        market_price_ntd: Decimal,
        now: datetime,
        source_timestamp: datetime,
        strategy_version: str,
        reason: str,
        settings: dict[str, object],
        current_prices: dict[str, Decimal],
        sizing_account: PortfolioAccount,
    ) -> None:
        fee_rate = Decimal(str(settings["fee_pct"])) / 100
        slippage_rate = Decimal(str(settings["slippage_pct"])) / 100
        if side == "buy":
            fill_price = market_price_ntd * (1 + slippage_rate)
            quantity = self._buy_quantity(
                connection,
                round_id,
                symbol,
                market_price_ntd,
                settings,
                current_prices,
                sizing_account,
            )
            notional = quantity * fill_price
            fee = notional * fee_rate
            slippage = quantity * (fill_price - market_price_ntd)
            realized = Decimal()
            connection.execute(
                "INSERT INTO paper_positions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    round_id,
                    symbol,
                    _decimal_text(quantity),
                    _decimal_text(fill_price),
                    _decimal_text(fee),
                    now.isoformat(),
                    strategy_version,
                    signal_id,
                ),
            )
        else:
            position = connection.execute(
                "SELECT * FROM paper_positions WHERE round_id=? AND symbol=?", (round_id, symbol)
            ).fetchone()
            quantity = Decimal(str(position["quantity"]))
            fill_price = market_price_ntd * (1 - slippage_rate)
            notional = quantity * fill_price
            fee = notional * fee_rate
            slippage = quantity * (market_price_ntd - fill_price)
            basis = quantity * Decimal(str(position["entry_price_ntd"]))
            realized = notional - fee - basis - Decimal(str(position["entry_cost_ntd"]))
            connection.execute(
                "DELETE FROM paper_positions WHERE round_id=? AND symbol=?", (round_id, symbol)
            )
        connection.execute(
            "INSERT INTO paper_trades VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                round_id,
                signal_id,
                symbol,
                side,
                _decimal_text(quantity),
                _decimal_text(market_price_ntd),
                _decimal_text(fill_price),
                _decimal_text(notional),
                _decimal_text(fee),
                _decimal_text(slippage),
                now.isoformat(),
                source_timestamp.isoformat(),
                strategy_version,
                reason,
                _decimal_text(realized),
            ),
        )

    def _buy_quantity(
        self,
        connection: sqlite3.Connection,
        round_id: int,
        symbol: str,
        market_price_ntd: Decimal,
        settings: dict[str, object],
        current_prices: dict[str, Decimal],
        sizing_account: PortfolioAccount,
    ) -> Decimal:
        account = self._account(connection, round_id, settings, current_prices)
        equity = min(
            sizing_account.total_equity_ntd,
            sizing_account.cash_ntd,
            account.cash_ntd,
        )
        try:
            rules = self.market_data.market_rules(symbol)
        except (AttributeError, MarketDataError) as error:
            raise MarketDataSafetyError(f"{symbol}: market rules unavailable: {error}") from error
        return executable_quantity(equity, market_price_ntd, settings, rules)

    def _account(
        self,
        connection: sqlite3.Connection,
        round_id: int,
        settings: dict[str, object],
        current_prices: dict[str, Decimal],
    ) -> PortfolioAccount:
        starting = Decimal(str(settings.get("starting_capital_ntd", "5000")))
        trades = connection.execute(
            "SELECT * FROM paper_trades WHERE round_id=? ORDER BY id", (round_id,)
        ).fetchall()
        cash = starting
        realized = Decimal()
        costs = Decimal()
        for trade in trades:
            notional = Decimal(str(trade["notional_ntd"]))
            fee = Decimal(str(trade["fee_ntd"]))
            slippage = Decimal(str(trade["slippage_ntd"]))
            cash += notional - fee if trade["side"] == "sell" else -(notional + fee)
            realized += Decimal(str(trade["realized_pnl_ntd"]))
            costs += fee + slippage
        position_value = unrealized = Decimal()
        for position in connection.execute(
            "SELECT * FROM paper_positions WHERE round_id=?", (round_id,)
        ).fetchall():
            symbol = str(position["symbol"])
            quantity = Decimal(str(position["quantity"]))
            market_price = current_prices.get(symbol)
            if market_price is None:
                market_price = Decimal(str(position["entry_price_ntd"]))
            value = quantity * market_price
            position_value += value
            unrealized += value - quantity * Decimal(str(position["entry_price_ntd"]))
        return PortfolioAccount(
            cash,
            position_value,
            realized,
            unrealized,
            costs,
            cash,
            cash + position_value,
        )

    def _daily_loss_limit_reached(
        self,
        connection: sqlite3.Connection,
        round_id: int,
        now: datetime,
        settings: dict[str, object],
    ) -> bool:
        day = now.astimezone(UTC).date().isoformat()
        realized = sum(
            (
                Decimal(str(row[0]))
                for row in connection.execute(
                    "SELECT realized_pnl_ntd FROM paper_trades "
                    "WHERE round_id=? AND side='sell' AND substr(executed_at, 1, 10)=?",
                    (round_id, day),
                )
            ),
            Decimal(),
        )
        limit = (
            Decimal(str(settings.get("starting_capital_ntd", "5000")))
            * Decimal(str(settings["daily_loss_limit_pct"]))
            / 100
        )
        return realized <= -limit

    def activate_round(
        self,
        settings: RoundPlanningSettings | dict[str, object],
        *,
        transition_id: int | None = None,
        connection: sqlite3.Connection | None = None,
        completion_hook: Callable[[sqlite3.Connection, RoundPlan], None] | None = None,
    ) -> RoundPlan:
        if not isinstance(settings, RoundPlanningSettings):
            settings = RoundPlanningSettings.from_mapping(settings)
        if connection is None:
            with self.database.connect() as owned:
                owned.execute("BEGIN IMMEDIATE")
                try:
                    plan = self.activate_round(
                        settings,
                        transition_id=transition_id,
                        connection=owned,
                        completion_hook=completion_hook,
                    )
                    owned.commit()
                    return plan
                except (MarketDataError, RoundPlanningError, ValueError, KeyError) as error:
                    round_row = owned.execute(
                        "SELECT id FROM trading_round WHERE status='planning' "
                        "ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    if round_row is not None:
                        owned.execute(
                            "INSERT INTO planning_failures"
                            "(round_id, occurred_at, reason, active) VALUES (?, ?, ?, 1)",
                            (round_row["id"], self.clock().isoformat(), str(error)),
                        )
                        owned.execute(
                            "UPDATE trading_round SET status='failed' WHERE id=?",
                            (round_row["id"],),
                        )
                    owned.commit()
                    raise
                except BaseException:
                    owned.rollback()
                    raise
        now = self.clock().isoformat(timespec="seconds").replace("+00:00", "Z")
        if transition_id is not None:
            transition = connection.execute(
                "SELECT status, activated_round_id FROM lifecycle_transitions WHERE id=?",
                (transition_id,),
            ).fetchone()
            if transition is None or transition["status"] != "pending_plan":
                raise RoundPlanningError("lifecycle transition is no longer pending")
        try:
            cycle = connection.execute(
                "SELECT id FROM cycles WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if cycle is None:
                cursor = connection.execute(
                    "INSERT INTO cycles(status, started_at, starting_capital_ntd) "
                    "VALUES('active', ?, ?)",
                    (now, _decimal_text(settings.starting_capital_ntd)),
                )
                cycle_id = cursor.lastrowid
            else:
                cycle_id = cycle["id"]
            cursor = connection.execute(
                "INSERT INTO trading_round(status, started_at, frozen_settings_json, cycle_id, "
                "lifecycle_transition_id) VALUES(?, ?, ?, ?, ?)",
                ("planning", now, _json(settings.as_dict()), cycle_id, transition_id),
            )
            if cursor.lastrowid is None:
                raise RoundPlanningError("failed to create round")
            round_id = int(cursor.lastrowid)
            plan = self._plan_round(connection, round_id, settings)
            if transition_id is not None:
                if self.fault_injector is not None:
                    self.fault_injector("after_plan_activation")
                changed = connection.execute(
                    "UPDATE lifecycle_transitions SET status='completed', "
                    "activated_round_id=?, completed_at=? WHERE id=? AND status='pending_plan'",
                    (round_id, now, transition_id),
                ).rowcount
                if changed != 1:
                    raise RoundPlanningError("lifecycle transition was concurrently completed")
            if completion_hook is not None:
                completion_hook(connection, plan)
            return plan
        except (MarketDataError, RoundPlanningError, ValueError, KeyError) as error:
            if isinstance(error, RoundPlanningError):
                raise
            raise RoundPlanningError(str(error)) from error

    def _plan_round(
        self, connection: sqlite3.Connection, round_id: int, settings: RoundPlanningSettings
    ) -> RoundPlan:
        summaries = sorted(self.market_data.market_summaries(), key=lambda item: item.symbol)
        self.last_exclusions = {}
        ranked: list[tuple[MarketSummary, NtdConversion, Decimal]] = []
        minimum = settings.minimum_liquidity_ntd
        for summary in summaries:
            if not summary.last_price.is_finite() or summary.last_price <= 0:
                self.last_exclusions[summary.symbol] = "invalid price: must be positive"
                continue
            if not summary.quote_volume.is_finite() or summary.quote_volume < 0:
                self.last_exclusions[summary.symbol] = "invalid quote volume: must not be negative"
                continue
            if summary.observed_at > self.clock():
                self.last_exclusions[summary.symbol] = "future market summary timestamp"
                continue
            try:
                conversion = self.market_data.ntd_conversion(summary.quote_asset)
            except MarketDataError as error:
                self.last_exclusions[summary.symbol] = str(error)
                continue
            if not conversion.rate.is_finite() or conversion.rate <= 0:
                self.last_exclusions[summary.symbol] = "invalid NTD conversion rate"
                continue
            if conversion.observed_at > self.clock():
                self.last_exclusions[summary.symbol] = "future NTD conversion timestamp"
                continue
            if conversion.provenance is None:
                self.last_exclusions[summary.symbol] = "missing structured conversion provenance"
                continue
            if any(
                not leg.rate.is_finite()
                or leg.observed_at > self.clock()
                or (self.clock() - leg.observed_at).total_seconds()
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
        strategies: list[Strategy] = [RsiStrategy(), MacdStrategy(), BollingerBandStrategy()]
        selections: list[Selection] = []

        qualified_candidates = 0
        fundable_candidates = 0
        unknown_fundability_candidates = 0
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
                rules = self.market_data.market_rules(summary.symbol)
                candles = self.market_data.historical_candles(
                    summary.symbol, interval, lookback
                )
                candle_age = (self.clock() - candles[-1].opened_at).total_seconds()
                if candle_age < 0:
                    raise MarketDataError("future candidate candles")
                if candle_age > settings.max_candle_age_seconds:
                    raise MarketDataError("stale candidate candles")
                if any(
                    candle.opened_at > self.clock()
                    or not all(
                        value.is_finite()
                        for value in (
                            candle.open,
                            candle.high,
                            candle.low,
                            candle.close,
                            candle.volume,
                        )
                    )
                    for candle in candles
                ):
                    raise MarketDataError("invalid candidate candles")
                results = [backtester.run(strategy, candles) for strategy in strategies]
            except (MarketDataError, RoundPlanningError, ValueError, IndexError) as error:
                # A candidate whose required rules/candles are unavailable cannot
                # prove that the portfolio is bankrupt. It remains excluded from
                # this plan, but forces a recoverable planning failure instead of
                # an irreversible cycle reset.
                unknown_fundability_candidates += 1
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
            qualified_candidates += 1
            market_price_ntd = summary.last_price * _conversion.rate
            if executable_quantity(
                settings.starting_capital_ntd, market_price_ntd, settings, rules
            ) <= 0:
                self.last_exclusions[summary.symbol] = "below minimum executable quantity"
                connection.execute(
                    "UPDATE market_rankings SET exclusion_reason=? "
                    "WHERE round_id=? AND symbol=?",
                    ("below minimum executable quantity", round_id, summary.symbol),
                )
                continue
            fundable_candidates += 1
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
                    _json({
                        "candles": candle_provenance,
                        "metrics": chosen.metrics,
                        "market_rules": asdict(rules),
                    }),
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
            if (
                qualified_candidates > 0
                and fundable_candidates == 0
                and unknown_fundability_candidates == 0
            ):
                raise UnfundablePlanningError(
                    "all otherwise-qualified candidates are below minimum executable quantity"
                )
            raise RoundPlanningError(
                "round requires at least five markets with qualifying strategies"
            )
        return RoundPlan(round_id, tuple(selections), settings)
