"""Inclusive, paginated and idempotent USD-M Futures downloads."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from adaptive_trader.futures.integrity import (
    funding_content_hash,
    futures_candle_content_hash,
    mark_price_content_hash,
)
from adaptive_trader.futures.models import FundingRate, FuturesCandle, MarkPriceCandle
from adaptive_trader.market_data.binance_futures_public import BinanceFuturesPublicClient
from adaptive_trader.market_data.history import _INTERVAL_DELTAS
from adaptive_trader.storage.sqlite import DatabaseRepository


@dataclass(frozen=True, slots=True)
class FuturesDownloadStats:
    pages: int
    received: int
    ignored: int
    persisted: int
    content_hash: str
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    duplicate_count: int = 0
    duration_seconds: Decimal = Decimal("0")
    requests: int = 0
    retries: int = 0
    warnings: tuple[str, ...] = ()
    range_semantics: str = "start_and_end_inclusive"


def _hash(items: Sequence[object]) -> str:
    if all(isinstance(item, FuturesCandle) for item in items):
        return futures_candle_content_hash(
            tuple(item for item in items if isinstance(item, FuturesCandle))
        )
    if all(isinstance(item, MarkPriceCandle) for item in items):
        return mark_price_content_hash(
            tuple(item for item in items if isinstance(item, MarkPriceCandle))
        )
    if all(isinstance(item, FundingRate) for item in items):
        return funding_content_hash(
            tuple(item for item in items if isinstance(item, FundingRate))
        )
    raise TypeError("download hash requires one supported Futures data type")


class FuturesHistoricalDownloader:
    def __init__(
        self,
        client: BinanceFuturesPublicClient,
        repository: DatabaseRepository,
    ) -> None:
        self._client = client
        self._repository = repository

    async def download_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FuturesDownloadStats:
        return await self._download_candles(
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            mark_prices=False,
        )

    async def download_mark_prices(
        self,
        *,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FuturesDownloadStats:
        return await self._download_candles(
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            mark_prices=True,
        )

    async def _download_candles(
        self,
        *,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        mark_prices: bool,
    ) -> FuturesDownloadStats:
        self._validate_range(start_time, end_time)
        started = time.monotonic()
        requests_before = int(getattr(self._client, "request_count", 0))
        retries_before = int(getattr(self._client, "retry_count", 0))
        current = start_time
        pages = received = ignored = persisted = 0
        downloaded: list[FuturesCandle | MarkPriceCandle] = []
        while current <= end_time:
            if mark_prices:
                page: tuple[FuturesCandle | MarkPriceCandle, ...] = (
                    await self._client.fetch_mark_price_klines(
                        symbol,
                        interval,
                        start_time=current,
                        end_time=end_time,
                    )
                )
            else:
                page = await self._client.fetch_klines(
                    symbol,
                    interval,
                    start_time=current,
                    end_time=end_time,
                )
            pages += 1
            received += len(page)
            if not page:
                break
            eligible = tuple(
                item
                for item in page
                if item.is_closed and start_time <= item.open_time <= end_time
            )
            ignored += len(page) - len(eligible)
            if mark_prices:
                typed_marks = tuple(item for item in eligible if isinstance(item, MarkPriceCandle))
                before = self._repository.count_mark_prices(symbol, interval)
                self._repository.upsert_mark_prices(typed_marks)
                after = self._repository.count_mark_prices(symbol, interval)
                persisted += after - before
                downloaded.extend(typed_marks)
            else:
                typed_candles = tuple(item for item in eligible if isinstance(item, FuturesCandle))
                before = self._repository.count_futures_candles(symbol, interval)
                self._repository.upsert_futures_candles(typed_candles)
                after = self._repository.count_futures_candles(symbol, interval)
                persisted += after - before
                downloaded.extend(typed_candles)
            last_time = page[-1].open_time
            if last_time >= end_time or len(page) < 1500:
                break
            next_time = last_time + _INTERVAL_DELTAS[interval]
            if next_time <= current:
                raise RuntimeError("futures pagination did not advance")
            current = next_time
        timestamps = tuple(item.open_time for item in downloaded)
        return FuturesDownloadStats(
            pages=pages,
            received=received,
            ignored=ignored,
            persisted=persisted,
            content_hash=_hash(downloaded),
            first_timestamp=min(timestamps, default=None),
            last_timestamp=max(timestamps, default=None),
            duplicate_count=len(timestamps) - len(set(timestamps)),
            duration_seconds=Decimal(str(time.monotonic() - started)),
            requests=int(getattr(self._client, "request_count", 0)) - requests_before,
            retries=int(getattr(self._client, "retry_count", 0)) - retries_before,
            warnings=("NO_DATA_DOWNLOADED",) if not downloaded else (),
        )

    async def download_funding(
        self,
        *,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FuturesDownloadStats:
        self._validate_range(start_time, end_time)
        started = time.monotonic()
        requests_before = int(getattr(self._client, "request_count", 0))
        retries_before = int(getattr(self._client, "retry_count", 0))
        current = start_time
        pages = received = ignored = persisted = 0
        downloaded: list[FundingRate] = []
        while current <= end_time:
            page = await self._client.fetch_funding_rates(
                symbol,
                start_time=current,
                end_time=end_time,
            )
            pages += 1
            received += len(page)
            if not page:
                break
            eligible = tuple(
                item
                for item in page
                if start_time <= item.funding_time <= end_time
            )
            ignored += len(page) - len(eligible)
            before = self._repository.count_funding_rates(symbol)
            self._repository.upsert_funding_rates(eligible)
            after = self._repository.count_funding_rates(symbol)
            persisted += after - before
            downloaded.extend(eligible)
            last_time = page[-1].funding_time
            if last_time >= end_time or len(page) < 1000:
                break
            next_time = last_time + timedelta(milliseconds=1)
            if next_time <= current:
                raise RuntimeError("funding pagination did not advance")
            current = next_time
        timestamps = tuple(item.funding_time for item in downloaded)
        return FuturesDownloadStats(
            pages=pages,
            received=received,
            ignored=ignored,
            persisted=persisted,
            content_hash=_hash(downloaded),
            first_timestamp=min(timestamps, default=None),
            last_timestamp=max(timestamps, default=None),
            duplicate_count=len(timestamps) - len(set(timestamps)),
            duration_seconds=Decimal(str(time.monotonic() - started)),
            requests=int(getattr(self._client, "request_count", 0)) - requests_before,
            retries=int(getattr(self._client, "retry_count", 0)) - retries_before,
            warnings=("NO_DATA_DOWNLOADED",) if not downloaded else (),
        )

    @staticmethod
    def _validate_range(start_time: datetime, end_time: datetime) -> None:
        if start_time.tzinfo is None or end_time.tzinfo is None:
            raise ValueError("download dates must be timezone-aware")
        if end_time < start_time:
            raise ValueError("end_time must not precede start_time")
