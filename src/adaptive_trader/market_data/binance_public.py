"""Async client for Binance public Spot klines only."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from random import SystemRandom
from typing import Any

import httpx

from adaptive_trader.domain.models import Candle
from adaptive_trader.market_data.exceptions import (
    InvalidMarketDataError,
    MarketDataRateLimitError,
    MarketDataResponseError,
    MarketDataTimeoutError,
)

SUPPORTED_INTERVALS = frozenset({"1m", "5m", "15m", "1h", "4h", "1d"})
MAX_KLINES_LIMIT = 1000
USER_AGENT = "AdaptiveTrader/0.2 (research-only; no-auth)"
_RANDOM = SystemRandom()


def validate_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized or not normalized.isalnum():
        raise ValueError("symbol must be uppercase alphanumeric")
    return normalized


def validate_interval(interval: str) -> str:
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"unsupported interval: {interval}")
    return interval


def _milliseconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return int(value.astimezone(UTC).timestamp() * 1000)


class BinancePublicClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        base_url: str = "https://api.binance.com",
        timeout_seconds: float = 10,
        maximum_retries: int = 4,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[int], float] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout_seconds <= 0 or maximum_retries < 0:
            raise ValueError("timeout must be positive and retries must not be negative")
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        self._client.headers["User-Agent"] = USER_AGENT
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._maximum_retries = maximum_retries
        self._sleep = sleep
        self._jitter = jitter or (lambda attempt: _RANDOM.uniform(0.0, min(0.25, attempt * 0.05)))
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    async def __aenter__(self) -> BinancePublicClient:
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_klines(
        self,
        symbol: str = "ETHUSDT",
        interval: str = "1m",
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = MAX_KLINES_LIMIT,
    ) -> tuple[Candle, ...]:
        normalized_symbol = validate_symbol(symbol)
        normalized_interval = validate_interval(interval)
        if not 1 <= limit <= MAX_KLINES_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_KLINES_LIMIT}")
        if start_time is not None and end_time is not None and end_time < start_time:
            raise ValueError("end_time must not precede start_time")
        params: dict[str, str | int] = {
            "symbol": normalized_symbol,
            "interval": normalized_interval,
            "limit": limit,
        }
        start_ms = _milliseconds(start_time)
        end_ms = _milliseconds(end_time)
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        payload = await self._request("/api/v3/klines", params)
        if not isinstance(payload, list):
            raise MarketDataResponseError("klines response must be a JSON array")
        return tuple(
            self._parse_kline(item, normalized_symbol, normalized_interval) for item in payload
        )

    async def _request(self, path: str, params: dict[str, str | int]) -> Any:
        for attempt in range(self._maximum_retries + 1):
            try:
                response = await self._client.get(
                    path, params=params, timeout=self._timeout_seconds
                )
            except httpx.TimeoutException as exc:
                if attempt >= self._maximum_retries:
                    raise MarketDataTimeoutError("Binance public request timed out") from exc
                await self._backoff(attempt)
                continue
            except httpx.RequestError as exc:
                if attempt >= self._maximum_retries:
                    raise MarketDataResponseError("Binance public request failed") from exc
                await self._backoff(attempt)
                continue
            if response.status_code in {418, 429}:
                if attempt >= self._maximum_retries:
                    raise MarketDataRateLimitError(
                        f"Binance rate limit response: HTTP {response.status_code}"
                    )
                await self._backoff(attempt)
                continue
            if response.status_code >= 500 and attempt < self._maximum_retries:
                await self._backoff(attempt)
                continue
            if response.status_code >= 400:
                raise MarketDataResponseError(
                    f"Binance public response: HTTP {response.status_code}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise MarketDataResponseError("Binance response was not valid JSON") from exc
        raise MarketDataResponseError("Binance request exhausted retries")

    async def _backoff(self, attempt: int) -> None:
        delay = (2**attempt) * 0.1 + self._jitter(attempt + 1)
        await self._sleep(delay)

    def _parse_kline(self, payload: object, symbol: str, interval: str) -> Candle:
        if not isinstance(payload, list) or len(payload) < 12:
            raise InvalidMarketDataError("kline must contain at least 12 fields")
        try:
            open_time = datetime.fromtimestamp(int(payload[0]) / 1000, tz=UTC)
            close_time = datetime.fromtimestamp(int(payload[6]) / 1000, tz=UTC)
            values = [Decimal(str(payload[index])) for index in (1, 2, 3, 4, 5, 7, 9, 10, 11)]
            trades_count = int(payload[8])
        except (TypeError, ValueError, InvalidOperation, OverflowError) as exc:
            raise InvalidMarketDataError("kline contains invalid numeric data") from exc
        collected_at = self._clock().astimezone(UTC)
        return Candle(
            symbol=symbol,
            timestamp=open_time,
            open=values[0],
            high=values[1],
            low=values[2],
            close=values[3],
            volume=values[4],
            exchange="BINANCE",
            interval=interval,
            close_time=close_time,
            quote_volume=values[5],
            trades_count=trades_count,
            taker_buy_base_volume=values[6],
            taker_buy_quote_volume=values[7],
            is_closed=close_time < collected_at,
            collected_at=collected_at,
        )
