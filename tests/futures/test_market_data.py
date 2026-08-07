import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from adaptive_trader.market_data.binance_futures_public import BinanceFuturesPublicClient
from adaptive_trader.market_data.exceptions import MarketDataRateLimitError
from adaptive_trader.market_data.futures_history import FuturesHistoricalDownloader
from adaptive_trader.storage.sqlite import DatabaseRepository


def kline(open_time: int = 1_735_689_600_000) -> list[object]:
    return [
        open_time,
        "100",
        "105",
        "95",
        "102",
        "10",
        open_time + 3_599_999,
        "1000",
        20,
        "5",
        "500",
        "0",
    ]


def test_public_futures_client_uses_only_documented_no_auth_endpoints() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/fundingRate"):
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "ETHUSDT",
                        "fundingTime": 1_735_693_200_000,
                        "fundingRate": "0.0001",
                        "markPrice": "103",
                    }
                ],
            )
        return httpx.Response(200, json=[kline()])

    async def run():
        async with httpx.AsyncClient(
            base_url="https://test",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = BinanceFuturesPublicClient(
                http_client=http_client,
                clock=lambda: datetime(2025, 1, 3, tzinfo=UTC),
            )
            candles = await client.fetch_klines("ETHUSDT", "1h")
            marks = await client.fetch_mark_price_klines("ETHUSDT", "1h")
            funding = await client.fetch_funding_rates("ETHUSDT")
            return candles, marks, funding

    candles, marks, funding = asyncio.run(run())
    assert candles[0].close == Decimal("102")
    assert marks[0].mark_price == Decimal("102")
    assert funding[0].funding_rate == Decimal("0.0001")
    assert {request.url.path for request in requests} == {
        "/fapi/v1/klines",
        "/fapi/v1/markPriceKlines",
        "/fapi/v1/fundingRate",
    }
    for request in requests:
        headers = {key.lower() for key in request.headers}
        assert "x-mbx-apikey" not in headers
        assert "authorization" not in headers


def test_public_futures_client_bounded_rate_limit_retry() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, json={})

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://test",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = BinanceFuturesPublicClient(
                http_client=http_client,
                maximum_retries=1,
                sleep=lambda _: asyncio.sleep(0),
            )
            with pytest.raises(MarketDataRateLimitError):
                await client.fetch_klines("ETHUSDT", "1h")

    asyncio.run(run())
    assert calls == 2


def test_public_funding_parser_accepts_empty_optional_mark_price() -> None:
    async def run():
        async with httpx.AsyncClient(
            base_url="https://test",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json=[
                        {
                            "symbol": "ETHUSDT",
                            "fundingTime": 1_735_693_200_006,
                            "fundingRate": "0.0001",
                            "markPrice": "",
                        }
                    ],
                )
            ),
        ) as http_client:
            client = BinanceFuturesPublicClient(http_client=http_client)
            return await client.fetch_funding_rates("ETHUSDT")

    funding = asyncio.run(run())
    assert funding[0].mark_price is None


def test_futures_downloads_are_idempotent_and_inclusive(
    tmp_path: Path,
    futures_candles,
    mark_prices,
    start_time,
) -> None:
    funding_time = start_time + timedelta(hours=4)

    class StubClient:
        async def fetch_klines(self, *args, **kwargs):
            return futures_candles

        async def fetch_mark_price_klines(self, *args, **kwargs):
            return mark_prices

        async def fetch_funding_rates(self, *args, **kwargs):
            from adaptive_trader.futures.models import FundingRate

            return (
                FundingRate(
                    symbol="ETHUSDT",
                    funding_time=funding_time,
                    funding_rate=Decimal("0.0001"),
                ),
            )

    repository = DatabaseRepository(tmp_path / "download.sqlite3")
    downloader = FuturesHistoricalDownloader(StubClient(), repository)
    end = futures_candles[-1].open_time
    try:
        first = asyncio.run(
            downloader.download_klines(
                symbol="ETHUSDT",
                interval="1h",
                start_time=start_time,
                end_time=end,
            )
        )
        asyncio.run(
            downloader.download_klines(
                symbol="ETHUSDT",
                interval="1h",
                start_time=start_time,
                end_time=end,
            )
        )
        mark_stats = asyncio.run(
            downloader.download_mark_prices(
                symbol="ETHUSDT",
                interval="1h",
                start_time=start_time,
                end_time=end,
            )
        )
        funding_stats = asyncio.run(
            downloader.download_funding(
                symbol="ETHUSDT",
                start_time=start_time,
                end_time=end,
            )
        )
        assert repository.count_futures_candles("ETHUSDT", "1h") == len(futures_candles)
        assert repository.count_mark_prices("ETHUSDT", "1h") == len(mark_prices)
        assert repository.count_funding_rates("ETHUSDT") == 1
        assert first.range_semantics == "start_and_end_inclusive"
        assert first.content_hash
        assert mark_stats.persisted == len(mark_prices)
        assert funding_stats.persisted == 1
    finally:
        repository.close()
