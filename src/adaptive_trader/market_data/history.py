"""Incremental, idempotent historical candle download service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from adaptive_trader.market_data.binance_public import (
    BinancePublicClient,
    validate_interval,
    validate_symbol,
)
from adaptive_trader.storage.sqlite import DatabaseRepository


@dataclass(frozen=True, slots=True)
class DownloadStats:
    pages: int
    received: int
    ignored: int
    persisted: int


_INTERVAL_DELTAS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


class HistoricalCandleDownloader:
    def __init__(self, client: BinancePublicClient, repository: DatabaseRepository) -> None:
        self._client = client
        self._repository = repository

    async def download(
        self,
        *,
        symbol: str = "ETHUSDT",
        interval: str = "1m",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        include_open_candle: bool = False,
        force: bool = False,
    ) -> DownloadStats:
        symbol = validate_symbol(symbol)
        interval = validate_interval(interval)
        if start_time is not None and (start_time.tzinfo is None or start_time.utcoffset() is None):
            raise ValueError("start_time must be timezone-aware")
        if end_time is not None and (end_time.tzinfo is None or end_time.utcoffset() is None):
            raise ValueError("end_time must be timezone-aware")
        if start_time is not None and end_time is not None and end_time < start_time:
            raise ValueError("end_time must not precede start_time")
        current_start = start_time
        if current_start is None and not force:
            latest = self._repository.latest_candle(symbol, interval)
            if latest is not None:
                current_start = latest.open_time + _INTERVAL_DELTAS[interval]
        pages = received = ignored = persisted = 0
        previous_open_time: datetime | None = None
        while True:
            page = await self._client.fetch_klines(
                symbol,
                interval,
                start_time=current_start,
                end_time=end_time,
            )
            pages += 1
            received += len(page)
            if not page:
                break
            if any(
                current.open_time <= previous.open_time
                for previous, current in zip(page, page[1:], strict=False)
            ):
                raise RuntimeError("historical page is not strictly chronological")
            if previous_open_time is not None and page[-1].open_time <= previous_open_time:
                raise RuntimeError("historical page made no chronological progress")
            previous_open_time = page[-1].open_time
            eligible = [
                candle
                for candle in page
                if (include_open_candle or candle.is_closed)
                and (start_time is None or candle.open_time >= start_time)
                and (end_time is None or candle.open_time <= end_time)
            ]
            ignored += len(page) - len(eligible)
            persisted += self._repository.upsert_candles(tuple(eligible))
            if end_time is None or page[-1].open_time >= end_time or len(page) < 1000:
                break
            next_start = page[-1].open_time + _INTERVAL_DELTAS[interval]
            if current_start is not None and next_start <= current_start:
                raise RuntimeError("historical pagination did not advance")
            current_start = next_start
        return DownloadStats(pages=pages, received=received, ignored=ignored, persisted=persisted)
