from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backend.app.market_data import BinanceMarketData, MarketDataError


def test_binance_adapter_maps_public_data_and_records_fx_conversion_provenance() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def transport(url: str, params: dict[str, str]) -> object:
        calls.append((url, params))
        if url.endswith("/ticker/24hr"):
            return [
                {"symbol": "BTCUSDT", "lastPrice": "100.25", "quoteVolume": "12345.67"},
                {"symbol": "BTCEUR", "lastPrice": "99", "quoteVolume": "12"},
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
        return datetime(2026, 1, 1, tzinfo=UTC)

    adapter = BinanceMarketData(transport=transport, clock=clock)

    summaries = adapter.market_summaries()
    candles = adapter.historical_candles("BTCUSDT", "1h", 30)
    conversion = adapter.ntd_conversion("USDT")

    assert [(item.symbol, item.last_price, item.quote_volume) for item in summaries] == [
        ("BTCUSDT", Decimal("100.25"), Decimal("12345.67")),
        ("BTCEUR", Decimal("99"), Decimal("12")),
    ]
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
        {"symbol": "BTCUSDT", "lastPrice": "100", "quoteVolume": "10"},
        {"symbol": "ETHEUR", "lastPrice": "90", "quoteVolume": "20"},
    ]
    adapter = BinanceMarketData(transport=lambda _url, _params: payload)

    summaries = adapter.market_summaries()

    assert [(item.symbol, item.quote_asset) for item in summaries] == [
        ("BTCUSDT", "USDT"),
        ("ETHEUR", "EUR"),
    ]
    with pytest.raises(MarketDataError, match="no NTD conversion path for EUR"):
        adapter.ntd_conversion("EUR")
