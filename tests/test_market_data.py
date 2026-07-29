import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from adaptive_trader.market_data.binance_public import BinancePublicClient
from adaptive_trader.market_data.context import MarketContextBuilder
from adaptive_trader.market_data.exceptions import (
    InvalidMarketDataError,
    MarketDataRateLimitError,
    MarketDataResponseError,
    MarketDataTimeoutError,
)
from adaptive_trader.market_data.history import HistoricalCandleDownloader
from adaptive_trader.storage.sqlite import DatabaseRepository


def kline_payload(open_time: int = 1_700_000_000_000) -> list[object]:
    return [
        open_time,
        "2000.10",
        "2010.20",
        "1990.00",
        "2005.50",
        "10.25",
        open_time + 59_999,
        "20500.00",
        42,
        "5.10",
        "10200.00",
        "0",
    ]


def test_public_client_parses_decimal_utc_and_sends_no_api_key() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update({key.lower(): value for key, value in request.headers.items()})
        return httpx.Response(200, json=[kline_payload()])

    async def run() -> tuple[object, ...]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url="https://test", transport=transport) as http_client:
            client = BinancePublicClient(
                http_client=http_client,
                base_url="https://test",
                clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
                sleep=lambda _: asyncio.sleep(0),
            )
            return await client.fetch_klines("ETHUSDT", "1m")

    candles = asyncio.run(run())
    candle = candles[0]
    assert candle.open == Decimal("2000.10")
    assert candle.timestamp.tzinfo is UTC
    assert seen_headers["user-agent"].startswith("AdaptiveTrader/")
    assert "x-mbx-apikey" not in seen_headers
    assert "authorization" not in seen_headers


def test_public_client_handles_rate_limit_with_bounded_retry() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"code": -1003})

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://test", transport=httpx.MockTransport(handler)
        ) as http_client:
            client = BinancePublicClient(
                http_client=http_client,
                maximum_retries=2,
                sleep=lambda _: asyncio.sleep(0),
            )
            with pytest.raises(MarketDataRateLimitError):
                await client.fetch_klines()

    asyncio.run(run())
    assert calls == 3


@pytest.mark.parametrize("status", [418, 500])
def test_public_client_retries_temporary_http_errors(status: int) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={})

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://test", transport=httpx.MockTransport(handler)
        ) as http_client:
            client = BinancePublicClient(
                http_client=http_client, maximum_retries=1, sleep=lambda _: asyncio.sleep(0)
            )
            with pytest.raises((MarketDataRateLimitError, MarketDataResponseError)):
                await client.fetch_klines()

    asyncio.run(run())
    assert calls == 2


def test_public_client_maps_timeout_and_invalid_payload() -> None:
    def timeout_handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    def invalid_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async def run(handler) -> None:
        async with httpx.AsyncClient(
            base_url="https://test", transport=httpx.MockTransport(handler)
        ) as http_client:
            client = BinancePublicClient(
                http_client=http_client, maximum_retries=0, sleep=lambda _: asyncio.sleep(0)
            )
            with pytest.raises((MarketDataTimeoutError, MarketDataResponseError)):
                await client.fetch_klines()

    asyncio.run(run(timeout_handler))
    asyncio.run(run(invalid_handler))


def test_public_client_rejects_malformed_kline() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[[1, 2]])

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://test", transport=httpx.MockTransport(handler)
        ) as http_client:
            client = BinancePublicClient(http_client=http_client)
            with pytest.raises(InvalidMarketDataError):
                await client.fetch_klines()

    asyncio.run(run())


def test_context_rejects_future_duplicate_and_mixed_symbol(candle) -> None:
    builder = MarketContextBuilder()
    analysis_time = candle.timestamp + timedelta(minutes=1)
    future = replace(candle, timestamp=analysis_time + timedelta(minutes=1))
    with pytest.raises(ValueError, match="future"):
        builder.build(
            (candle, future), symbol="ETHUSDT", interval="1m", analysis_time=analysis_time
        )
    with pytest.raises(ValueError, match="chronological"):
        builder.build(
            (candle, candle), symbol="ETHUSDT", interval="1m", analysis_time=analysis_time
        )


def test_history_download_is_idempotent_and_excludes_open_candle(tmp_path: Path) -> None:
    class StubClient:
        async def fetch_klines(self, *args, **kwargs):
            first = kline_payload(1_700_000_000_000)
            second = kline_payload(1_700_000_060_000)
            async_client = BinancePublicClient(clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
            parsed = tuple(
                async_client._parse_kline(item, "ETHUSDT", "1m") for item in (first, second)
            )
            return (parsed[0], replace(parsed[1], is_closed=False))

    repository = DatabaseRepository(tmp_path / "history.sqlite3")
    try:
        stats = asyncio.run(
            HistoricalCandleDownloader(StubClient(), repository).download(
                symbol="ETHUSDT", interval="1m"
            )
        )
        assert stats.persisted == 1
        assert repository.count_candles("ETHUSDT", "1m") == 1
        assert repository.latest_candle("ETHUSDT", "1m") is not None
    finally:
        repository.close()
