"""Async no-auth client for Binance USD-M Futures public market data."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from adaptive_trader.domain.market import ContractType, MarketType
from adaptive_trader.futures.models import FundingRate, FuturesCandle, MarkPriceCandle
from adaptive_trader.market_data.binance_public import (
    USER_AGENT,
    validate_interval,
    validate_symbol,
)
from adaptive_trader.market_data.exceptions import (
    InvalidMarketDataError,
    MarketDataRateLimitError,
    MarketDataResponseError,
    MarketDataTimeoutError,
)

MAX_FUTURES_KLINES_LIMIT = 1500
MAX_FUNDING_LIMIT = 1000


def _milliseconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return int(value.astimezone(UTC).timestamp() * 1000)


class BinanceFuturesPublicClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        base_url: str = "https://fapi.binance.com",
        timeout_seconds: float = 10,
        maximum_retries: int = 4,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
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
        self._timeout_seconds = timeout_seconds
        self._maximum_retries = maximum_retries
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._request_count = 0
        self._retry_count = 0

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def retry_count(self) -> int:
        return self._retry_count

    async def __aenter__(self) -> BinanceFuturesPublicClient:
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = MAX_FUTURES_KLINES_LIMIT,
    ) -> tuple[FuturesCandle, ...]:
        symbol, interval, params = self._range_params(
            symbol, interval, start_time, end_time, limit, MAX_FUTURES_KLINES_LIMIT
        )
        payload = await self._request("/fapi/v1/klines", params)
        if not isinstance(payload, list):
            raise MarketDataResponseError("futures klines response must be an array")
        return tuple(self._parse_futures_kline(item, symbol, interval) for item in payload)

    async def fetch_mark_price_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = MAX_FUTURES_KLINES_LIMIT,
    ) -> tuple[MarkPriceCandle, ...]:
        symbol, interval, params = self._range_params(
            symbol, interval, start_time, end_time, limit, MAX_FUTURES_KLINES_LIMIT
        )
        payload = await self._request("/fapi/v1/markPriceKlines", params)
        if not isinstance(payload, list):
            raise MarketDataResponseError("mark price klines response must be an array")
        return tuple(self._parse_mark_kline(item, symbol, interval) for item in payload)

    async def fetch_funding_rates(
        self,
        symbol: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = MAX_FUNDING_LIMIT,
    ) -> tuple[FundingRate, ...]:
        symbol = validate_symbol(symbol)
        if not 1 <= limit <= MAX_FUNDING_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_FUNDING_LIMIT}")
        self._validate_range(start_time, end_time)
        params: dict[str, str | int] = {"symbol": symbol, "limit": limit}
        start_ms = _milliseconds(start_time)
        end_ms = _milliseconds(end_time)
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        payload = await self._request("/fapi/v1/fundingRate", params)
        if not isinstance(payload, list):
            raise MarketDataResponseError("funding response must be an array")
        return tuple(self._parse_funding(item, symbol) for item in payload)

    def _range_params(
        self,
        symbol: str,
        interval: str,
        start_time: datetime | None,
        end_time: datetime | None,
        limit: int,
        maximum_limit: int,
    ) -> tuple[str, str, dict[str, str | int]]:
        symbol = validate_symbol(symbol)
        interval = validate_interval(interval)
        if not 1 <= limit <= maximum_limit:
            raise ValueError(f"limit must be between 1 and {maximum_limit}")
        self._validate_range(start_time, end_time)
        params: dict[str, str | int] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        start_ms = _milliseconds(start_time)
        end_ms = _milliseconds(end_time)
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        return symbol, interval, params

    @staticmethod
    def _validate_range(
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> None:
        _milliseconds(start_time)
        _milliseconds(end_time)
        if start_time is not None and end_time is not None and end_time < start_time:
            raise ValueError("end_time must not precede start_time")

    async def _request(self, path: str, params: dict[str, str | int]) -> Any:
        for attempt in range(self._maximum_retries + 1):
            self._request_count += 1
            if attempt:
                self._retry_count += 1
            try:
                response = await self._client.get(
                    path,
                    params=params,
                    timeout=self._timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                if attempt >= self._maximum_retries:
                    raise MarketDataTimeoutError("Binance Futures request timed out") from exc
                await self._sleep((2**attempt) * 0.1)
                continue
            except httpx.RequestError as exc:
                if attempt >= self._maximum_retries:
                    raise MarketDataResponseError("Binance Futures request failed") from exc
                await self._sleep((2**attempt) * 0.1)
                continue
            if response.status_code in {418, 429}:
                if attempt >= self._maximum_retries:
                    raise MarketDataRateLimitError(
                        f"Binance Futures rate limit: HTTP {response.status_code}"
                    )
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after is not None else (2**attempt) * 0.1
                await self._sleep(delay)
                continue
            if response.status_code >= 500 and attempt < self._maximum_retries:
                await self._sleep((2**attempt) * 0.1)
                continue
            if response.status_code >= 400:
                raise MarketDataResponseError(
                    f"Binance Futures public response: HTTP {response.status_code}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise MarketDataResponseError("Binance Futures response was not JSON") from exc
        raise MarketDataResponseError("Binance Futures request exhausted retries")

    def _parse_futures_kline(
        self,
        payload: object,
        symbol: str,
        interval: str,
    ) -> FuturesCandle:
        if not isinstance(payload, list) or len(payload) < 11:
            raise InvalidMarketDataError("futures kline must contain at least 11 fields")
        try:
            open_time = datetime.fromtimestamp(int(payload[0]) / 1000, tz=UTC)
            close_time = datetime.fromtimestamp(int(payload[6]) / 1000, tz=UTC)
            prices = tuple(Decimal(str(payload[index])) for index in (1, 2, 3, 4, 5, 7))
            trade_count = int(payload[8])
        except (TypeError, ValueError, InvalidOperation, OverflowError) as exc:
            raise InvalidMarketDataError("futures kline contains invalid data") from exc
        collected_at = self._clock().astimezone(UTC)
        return FuturesCandle(
            exchange="BINANCE",
            market_type=MarketType.USD_M_FUTURES,
            contract_type=ContractType.PERPETUAL,
            symbol=symbol,
            interval=interval,
            open_time=open_time,
            close_time=close_time,
            open=prices[0],
            high=prices[1],
            low=prices[2],
            close=prices[3],
            volume=prices[4],
            quote_volume=prices[5],
            trade_count=trade_count,
            is_closed=close_time < collected_at,
            collected_at=collected_at,
        )

    def _parse_mark_kline(
        self,
        payload: object,
        symbol: str,
        interval: str,
    ) -> MarkPriceCandle:
        if not isinstance(payload, list) or len(payload) < 7:
            raise InvalidMarketDataError("mark price kline must contain at least 7 fields")
        try:
            open_time = datetime.fromtimestamp(int(payload[0]) / 1000, tz=UTC)
            close_time = datetime.fromtimestamp(int(payload[6]) / 1000, tz=UTC)
            prices = tuple(Decimal(str(payload[index])) for index in (1, 2, 3, 4))
        except (TypeError, ValueError, InvalidOperation, OverflowError) as exc:
            raise InvalidMarketDataError("mark price kline contains invalid data") from exc
        collected_at = self._clock().astimezone(UTC)
        return MarkPriceCandle(
            symbol=symbol,
            interval=interval,
            open_time=open_time,
            close_time=close_time,
            open=prices[0],
            high=prices[1],
            low=prices[2],
            close=prices[3],
            is_closed=close_time < collected_at,
            collected_at=collected_at,
        )

    @staticmethod
    def _parse_funding(payload: object, symbol: str) -> FundingRate:
        if not isinstance(payload, dict):
            raise InvalidMarketDataError("funding event must be an object")
        try:
            event_symbol = validate_symbol(str(payload.get("symbol", symbol)))
            funding_time = datetime.fromtimestamp(int(payload["fundingTime"]) / 1000, tz=UTC)
            rate = Decimal(str(payload["fundingRate"]))
            mark_value = payload.get("markPrice")
            mark_price = (
                Decimal(str(mark_value))
                if mark_value not in {None, ""}
                else None
            )
        except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError) as exc:
            raise InvalidMarketDataError("funding event contains invalid data") from exc
        if event_symbol != symbol:
            raise InvalidMarketDataError("funding response symbol mismatch")
        return FundingRate(
            symbol=symbol,
            funding_time=funding_time,
            funding_rate=rate,
            mark_price=mark_price,
        )
