from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class PasswordInput(BaseModel):
    password: str = Field(min_length=12, max_length=1024)


class RunSettings(BaseModel):
    starting_capital_ntd: Decimal = Field(default=Decimal("5000.00"), gt=0)
    round_duration_days: int = Field(default=7, ge=1, le=365)
    strategy_cadence_seconds: int = Field(default=300, ge=10, le=86400)
    max_position_allocation_pct: Decimal = Field(default=Decimal("10.00"), gt=0, le=100)
    max_concurrent_positions: int = Field(default=3, ge=1, le=5)
    stop_loss_pct: Decimal = Field(default=Decimal("5.00"), gt=0, lt=100)
    take_profit_pct: Decimal = Field(default=Decimal("10.00"), gt=0, le=1000)
    daily_loss_limit_pct: Decimal = Field(default=Decimal("3.00"), gt=0, lt=100)
    fee_pct: Decimal = Field(default=Decimal("0.10"), ge=0, lt=10)
    slippage_pct: Decimal = Field(default=Decimal("0.10"), ge=0, lt=10)
    candle_interval: str = Field(default="1h", pattern=r"^(1m|5m|15m|1h|4h|1d)$")
    backtest_lookback_candles: int = Field(default=80, ge=30, le=1000)
    minimum_liquidity_ntd: Decimal = Field(default=Decimal("1000000"), ge=0)
    minimum_net_return_pct: Decimal = Field(default=Decimal("0"))
    minimum_entry_count: int = Field(default=1, ge=1)
    minimum_trade_count: int = Field(default=2, ge=1)
    max_conversion_age_seconds: int = Field(default=86400, gt=0)
    max_candle_age_seconds: int = Field(default=7200, gt=0)

    @model_validator(mode="after")
    def validate_total_exposure(self) -> "RunSettings":
        if self.max_position_allocation_pct * self.max_concurrent_positions > 100:
            raise ValueError("maximum aggregate position allocation cannot exceed 100%")
        return self
