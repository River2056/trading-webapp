from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backend.app.market_data import BinanceMarketData, MarketDataError


def test_binance_adapter_maps_public_data_and_records_fx_conversion_provenance() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def transport(url: str, params: dict[str, str]) -> object:
        calls.append((url, params))
        if url.endswith("/ticker/24hr"):
            return [
                {
                    "symbol": "BTCUSDT",
                    "lastPrice": "100.25",
                    "quoteVolume": "12345.67",
                    "closeTime": 1_767_225_600_000,
                },
                {
                    "symbol": "BTCEUR",
                    "lastPrice": "99",
                    "quoteVolume": "12",
                    "closeTime": 1_767_225_601_000,
                },
            ]
        if url.endswith("/ticker/price"):
            return {"symbol": "USDCUSDT", "price": "1.001001001001001001"}
        if url == "https://open.er-api.com/v6/latest/USD":
            return {
                "result": "success",
                "time_last_update_unix": 1_767_225_600,
                "rates": {"TWD": "31.75"},
            }
        return [
            [1_700_000_000_000 + index * 3_600_000, "10", "12", "9", "11", "100"]
            for index in range(30)
        ]

    def clock() -> datetime:
        return datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC)

    adapter = BinanceMarketData(transport=transport, clock=clock)

    summaries = adapter.market_summaries()
    candles = adapter.historical_candles("BTCUSDT", "1h", 30)
    conversion = adapter.ntd_conversion("USDT")

    assert [(item.symbol, item.last_price, item.quote_volume) for item in summaries] == [
        ("BTCUSDT", Decimal("100.25"), Decimal("12345.67")),
        ("BTCEUR", Decimal("99"), Decimal("12")),
    ]
    assert summaries[0].observed_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert summaries[1].observed_at == datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
    assert len(candles) == 30
    assert candles[0].opened_at < candles[-1].opened_at
    assert conversion.rate.quantize(Decimal("0.00001")) == Decimal("31.71825")
    assert conversion.path == "USDC/USDT Binance (inverted) -> USD/TWD open.er-api.com"
    assert conversion.observed_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert conversion.provenance is not None
    assert conversion.provenance.stablecoin.rate.quantize(Decimal("0.001")) == Decimal("0.999")
    assert conversion.provenance.fx.rate == Decimal("31.75")
    assert calls[1][1] == {"symbol": "BTCUSDT", "interval": "1h", "limit": "30"}
    assert calls[2] == (
        "https://api.binance.com/api/v3/ticker/price",
        {"symbol": "USDCUSDT"},
    )
    assert calls[3] == ("https://open.er-api.com/v6/latest/USD", {})
    assert all("key" not in str(call).lower() for call in calls)


def test_binance_adapter_rejects_malformed_or_out_of_order_data() -> None:
    adapter = BinanceMarketData(transport=lambda _url, _params: {"not": "a list"})
    with pytest.raises(MarketDataError, match="malformed Binance ticker"):
        adapter.market_summaries()

    rows = [[1_700_000_000_000, "10", "12", "9", "11", "100"] for _ in range(30)]
    adapter = BinanceMarketData(transport=lambda _url, _params: rows)
    with pytest.raises(MarketDataError, match="out-of-order"):
        adapter.historical_candles("BTCUSDT", "1h", 30)


def test_binance_adapter_exposes_unsupported_quote_assets_for_recorded_exclusion() -> None:
    payload = [
        {
            "symbol": "BTCUSDT",
            "lastPrice": "100",
            "quoteVolume": "10",
            "closeTime": 1_700_000_000_000,
        },
        {
            "symbol": "ETHEUR",
            "lastPrice": "90",
            "quoteVolume": "20",
            "closeTime": 1_700_000_000_000,
        },
    ]
    adapter = BinanceMarketData(transport=lambda _url, _params: payload)

    summaries = adapter.market_summaries()

    assert [(item.symbol, item.quote_asset) for item in summaries] == [
        ("BTCUSDT", "USDT"),
        ("ETHEUR", "EUR"),
    ]
    with pytest.raises(MarketDataError, match="no NTD conversion path for EUR"):
        adapter.ntd_conversion("EUR")


@pytest.mark.parametrize("close_time", [None, "not-a-timestamp"])
def test_binance_adapter_requires_valid_per_ticker_close_time(close_time: object) -> None:
    payload = [{"symbol": "BTCUSDT", "lastPrice": "100", "quoteVolume": "10"}]
    if close_time is not None:
        payload[0]["closeTime"] = close_time
    adapter = BinanceMarketData(transport=lambda _url, _params: payload)
    with pytest.raises(MarketDataError, match="malformed Binance ticker"):
        adapter.market_summaries()


@pytest.mark.parametrize(
    "ohlcv",
    [
        ("0", "12", "9", "11", "100"),
        ("10", "0", "9", "11", "100"),
        ("10", "12", "0", "11", "100"),
        ("10", "12", "9", "0", "100"),
        ("10", "12", "9", "11", "-1"),
        ("10", "12", "11", "9", "100"),
        ("10", "10", "9", "11", "100"),
    ],
)
def test_binance_adapter_rejects_invalid_ohlcv_invariants(ohlcv: tuple[str, ...]) -> None:
    payload = [[1_700_000_000_000 + index * 3_600_000, *ohlcv] for index in range(30)]
    adapter = BinanceMarketData(transport=lambda _url, _params: payload)
    with pytest.raises(MarketDataError, match="invalid or incomplete Binance candles"):
        adapter.historical_candles("BTCUSDT", "1h", 30)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_binance_adapter_rejects_non_finite_ticker_and_candles(value: str) -> None:
    ticker = [{"symbol": "BTCUSDT", "lastPrice": value, "quoteVolume": "1", "closeTime": 1}]
    with pytest.raises(MarketDataError):
        BinanceMarketData(transport=lambda _url, _params: ticker).market_summaries()
    candles = [[index, "1", "1", "1", value, "1"] for index in range(30)]
    with pytest.raises(MarketDataError):
        BinanceMarketData(transport=lambda _url, _params: candles).historical_candles(
            "BTCUSDT", "1h", 30
        )


@pytest.mark.parametrize(("leg", "value"), [("stablecoin", "NaN"), ("fx", "Infinity")])
def test_binance_adapter_rejects_non_finite_conversion_legs(leg: str, value: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def transport(url: str, _params: dict[str, str]) -> object:
        if url.endswith("/ticker/price"):
            return {
                "price": value if leg == "stablecoin" else "1",
                "closeTime": int(now.timestamp() * 1000),
            }
        return {
            "result": "success",
            "time_last_update_unix": int(now.timestamp()),
            "rates": {"TWD": value if leg == "fx" else "32"},
        }

    with pytest.raises(MarketDataError):
        BinanceMarketData(transport=transport, clock=lambda: now).ntd_conversion("USDT")


@pytest.mark.parametrize("source", ["ticker", "stablecoin", "fx", "candle"])
def test_binance_adapter_rejects_future_source_timestamps(source: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    future_ms = int((now + timedelta(seconds=1)).timestamp() * 1000)
    future_s = int((now + timedelta(seconds=1)).timestamp())

    def transport(url: str, _params: dict[str, str]) -> object:
        if url.endswith("/ticker/24hr"):
            return [
                {
                    "symbol": "BTCUSDT",
                    "lastPrice": "1",
                    "quoteVolume": "1",
                    "closeTime": future_ms,
                }
            ]
        if url.endswith("/ticker/price"):
            close = future_ms if source == "stablecoin" else int(now.timestamp() * 1000)
            return {"price": "1", "closeTime": close}
        if url.endswith("/klines"):
            return [[future_ms + index, "1", "1", "1", "1", "1"] for index in range(30)]
        updated = future_s if source == "fx" else int(now.timestamp())
        return {"result": "success", "time_last_update_unix": updated, "rates": {"TWD": "32"}}

    adapter = BinanceMarketData(transport=transport, clock=lambda: now)
    with pytest.raises(MarketDataError, match="future"):
        if source == "ticker":
            adapter.market_summaries()
        elif source in {"stablecoin", "fx"}:
            adapter.ntd_conversion("USDT")
        else:
            adapter.historical_candles("BTCUSDT", "1h", 30)
