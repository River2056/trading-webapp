from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.app.engine import Backtester, BollingerBandStrategy, MacdStrategy, RsiStrategy
from backend.app.market_data import Candle


def candles(closes: list[int]) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            start + timedelta(hours=index),
            Decimal(close),
            Decimal(close),
            Decimal(close),
            Decimal(close),
            Decimal("100"),
        )
        for index, close in enumerate(closes)
    ]


def test_rsi_backtest_uses_signals_and_charges_each_fill() -> None:
    # Falling prices trigger an oversold entry; the recovery triggers an overbought exit.
    history = candles(list(range(30, 14, -1)) + list(range(15, 36)))

    result = Backtester(Decimal("1"), Decimal("1")).run(RsiStrategy(), history)

    assert result.strategy_version == "rsi-v1"
    assert result.trade_count == 2
    assert result.entry_count == 1
    assert result.exit_count == 1
    assert result.total_cost_pct > Decimal("3.9")
    assert result.net_return_pct > 0
    assert result.configuration == {"period": 14, "entry_below": 30, "exit_above": 70}


def test_macd_backtest_uses_ema_signal_line_crossings_not_price_bias() -> None:
    # Flat, then down, then strong recovery produces a bullish signal-line crossing and later exit.
    history = candles(
        [100] * 30 + list(range(100, 79, -1)) + list(range(80, 131)) + list(range(130, 99, -1))
    )

    result = Backtester(Decimal("0"), Decimal("0")).run(MacdStrategy(), history)

    assert result.strategy_version == "macd-v1"
    assert result.trade_count >= 2
    assert result.entry_count >= 1
    assert result.exit_count == result.entry_count
    assert result.configuration == {"fast_period": 12, "slow_period": 26, "signal_period": 9}


def test_bollinger_band_backtest_buys_lower_band_break_and_exits_at_mean() -> None:
    history = candles([100] * 28 + [50, 100])

    result = Backtester(Decimal("0"), Decimal("0")).run(BollingerBandStrategy(), history)

    assert result.strategy_version == "bollinger-v1"
    assert result.trade_count == 2
    assert result.entry_count == 1
    assert result.exit_count == 1
    assert result.net_return_pct == Decimal("100")
    assert result.configuration == {"period": 20, "standard_deviations": Decimal("2")}


def test_no_signal_backtest_has_no_fake_buy_and_hold_return() -> None:
    history = candles([100] * 80)

    for strategy in (RsiStrategy(), MacdStrategy()):
        result = Backtester(Decimal("0.1"), Decimal("0.1")).run(strategy, history)
        assert result.trade_count == 0
        assert result.net_return_pct == 0
        assert not result.qualified
