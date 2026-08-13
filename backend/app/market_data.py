from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketSummary:
    symbol: str
    base_asset: str
    quote_asset: str
    last_price: Decimal
    quote_volume: Decimal
    observed_at: datetime


@dataclass(frozen=True)
class ConversionLeg:
    source: str
    observed_at: datetime
    rate: Decimal


@dataclass(frozen=True)
class ConversionProvenance:
    stablecoin: ConversionLeg
    fx: ConversionLeg


@dataclass(frozen=True)
class NtdConversion:
    quote_asset: str
    rate: Decimal
    path: str
    observed_at: datetime
    provenance: ConversionProvenance | None = None


@dataclass(frozen=True)
class Candle:
    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class MarketData(Protocol):
    def market_summaries(self) -> list[MarketSummary]: ...
    def ntd_conversion(self, quote_asset: str) -> NtdConversion: ...
    def historical_candles(self, symbol: str, interval: str, limit: int) -> list[Candle]: ...


JsonTransport = Callable[[str, dict[str, str]], object]


def _public_json(url: str, params: dict[str, str]) -> object:
    request_url = f"{url}?{urlencode(params)}" if params else url
    try:
        with urlopen(request_url, timeout=10) as response:  # noqa: S310 -- fixed endpoints
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise MarketDataError(f"public market-data request failed: {request_url}") from error


class BinanceMarketData:
    """Read-only Binance public-data adapter; it has no credential or order interface."""

    def __init__(
        self,
        transport: JsonTransport = _public_json,
        base_url: str = "https://api.binance.com/api/v3",
        clock: Callable[[], datetime] | None = None,
        fx_url: str = "https://open.er-api.com/v6/latest/USD",
        max_conversion_age: timedelta = timedelta(hours=24),
    ) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._fx_url = fx_url
        self._max_conversion_age = max_conversion_age
        self.provider = "Binance public API"

    def market_summaries(self) -> list[MarketSummary]:
        payload = self._transport(f"{self._base_url}/ticker/24hr", {})
        if not isinstance(payload, list):
            raise MarketDataError("malformed Binance ticker response")
        summaries: list[MarketSummary] = []
        try:
            for item in payload:
                if not isinstance(item, dict):
                    raise MarketDataError("malformed Binance ticker item")
                symbol = str(item["symbol"])
                quote_asset = next(
                    (
                        quote
                        for quote in ("USDT", "USDC", "BTC", "ETH", "BNB", "EUR")
                        if symbol.endswith(quote)
                    ),
                    "UNKNOWN",
                )
                summaries.append(
                    MarketSummary(
                        symbol=symbol,
                        base_asset=symbol.removesuffix(quote_asset),
                        quote_asset=quote_asset,
                        last_price=Decimal(str(item["lastPrice"])),
                        quote_volume=Decimal(str(item["quoteVolume"])),
                        observed_at=datetime.fromtimestamp(int(item["closeTime"]) / 1000, UTC),
                    )
                )
        except (KeyError, InvalidOperation, ValueError, TypeError) as error:
            raise MarketDataError("malformed Binance ticker response") from error
        now = self._clock()
        if any(
            not item.last_price.is_finite()
            or not item.quote_volume.is_finite()
            or item.observed_at > now
            for item in summaries
        ):
            raise MarketDataError("invalid or future Binance ticker data")
        return summaries

    def ntd_conversion(self, quote_asset: str) -> NtdConversion:
        if quote_asset not in {"USDT", "USDC"}:
            raise MarketDataError(f"no NTD conversion path for {quote_asset}")
        stablecoin_rate = Decimal("1")
        stablecoin_observed_at = self._clock()
        stablecoin_source = "USDC/USD assumed par"
        path = "USDC/USD assumed par -> USD/TWD open.er-api.com"
        try:
            if quote_asset == "USDT":
                stablecoin_payload = self._transport(
                    f"{self._base_url}/ticker/price", {"symbol": "USDCUSDT"}
                )
                if not isinstance(stablecoin_payload, dict):
                    raise MarketDataError("malformed Binance conversion response")
                stablecoin_rate = Decimal("1") / Decimal(str(stablecoin_payload["price"]))
                close_time = stablecoin_payload.get("closeTime")
                if close_time is not None:
                    stablecoin_observed_at = datetime.fromtimestamp(int(close_time) / 1000, UTC)
                stablecoin_source = "Binance public ticker USDCUSDT (inverted)"
                path = "USDC/USDT Binance (inverted) -> USD/TWD open.er-api.com"
            fx_payload = self._transport(self._fx_url, {})
            if not isinstance(fx_payload, dict) or fx_payload.get("result") != "success":
                raise MarketDataError("malformed public FX response")
            rates = fx_payload["rates"]
            if not isinstance(rates, dict):
                raise MarketDataError("malformed public FX response")
            twd_rate = Decimal(str(rates["TWD"]))
            observed_at = datetime.fromtimestamp(int(fx_payload["time_last_update_unix"]), UTC)
        except (KeyError, InvalidOperation, ValueError, TypeError) as error:
            raise MarketDataError("malformed public conversion response") from error
        rate = stablecoin_rate * twd_rate
        if not all(value.is_finite() for value in (stablecoin_rate, twd_rate, rate)) or rate <= 0:
            raise MarketDataError("invalid public conversion rate")
        now = self._clock()
        if stablecoin_observed_at > now:
            raise MarketDataError("future stablecoin conversion leg")
        if observed_at > now:
            raise MarketDataError("future fx conversion leg")
        if now - stablecoin_observed_at > self._max_conversion_age:
            raise MarketDataError("stale stablecoin conversion leg")
        if now - observed_at > self._max_conversion_age:
            raise MarketDataError("stale fx conversion leg")
        return NtdConversion(
            quote_asset,
            rate,
            path,
            min(stablecoin_observed_at, observed_at),
            ConversionProvenance(
                ConversionLeg(stablecoin_source, stablecoin_observed_at, stablecoin_rate),
                ConversionLeg("open.er-api.com USD/TWD", observed_at, twd_rate),
            ),
        )

    def historical_candles(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        payload = self._transport(
            f"{self._base_url}/klines",
            {"symbol": symbol, "interval": interval, "limit": str(limit)},
        )
        if not isinstance(payload, list):
            raise MarketDataError("malformed Binance candle response")
        candles: list[Candle] = []
        try:
            for item in payload:
                if not isinstance(item, list) or len(item) < 6:
                    raise MarketDataError("malformed Binance candle item")
                candles.append(
                    Candle(
                        opened_at=datetime.fromtimestamp(int(item[0]) / 1000, UTC),
                        open=Decimal(str(item[1])),
                        high=Decimal(str(item[2])),
                        low=Decimal(str(item[3])),
                        close=Decimal(str(item[4])),
                        volume=Decimal(str(item[5])),
                    )
                )
        except (InvalidOperation, ValueError, TypeError) as error:
            raise MarketDataError("malformed Binance candle response") from error
        now = self._clock()
        if any(c.opened_at > now for c in candles):
            raise MarketDataError("future Binance candles")
        if len(candles) != limit or any(
            not all(value.is_finite() for value in (c.open, c.high, c.low, c.close, c.volume))
            or c.open <= 0
            or c.high <= 0
            or c.low <= 0
            or c.close <= 0
            or c.volume < 0
            or c.low > min(c.open, c.close)
            or max(c.open, c.close) > c.high
            or c.low > c.high
            for c in candles
        ):
            raise MarketDataError("invalid or incomplete Binance candles")
        ordered_pairs = zip(candles, candles[1:], strict=False)
        if any(left.opened_at >= right.opened_at for left, right in ordered_pairs):
            raise MarketDataError("out-of-order Binance candles")
        return candles
