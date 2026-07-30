from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import ContractType, MarketType
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FuturesBacktestConfig,
    FuturesCandle,
    MarkPriceCandle,
)


@pytest.fixture
def start_time() -> datetime:
    return datetime(2025, 1, 1, tzinfo=UTC)


def make_candles(
    start: datetime,
    closes: tuple[str, ...] = ("100", "101", "102", "110", "112", "115"),
    *,
    lows: tuple[str, ...] | None = None,
    highs: tuple[str, ...] | None = None,
) -> tuple[FuturesCandle, ...]:
    items: list[FuturesCandle] = []
    previous = Decimal(closes[0])
    for index, close_text in enumerate(closes):
        close = Decimal(close_text)
        low = Decimal(lows[index]) if lows else min(previous, close) - Decimal("1")
        high = Decimal(highs[index]) if highs else max(previous, close) + Decimal("1")
        open_time = start + timedelta(hours=index)
        items.append(
            FuturesCandle(
                exchange="BINANCE",
                market_type=MarketType.USD_M_FUTURES,
                contract_type=ContractType.PERPETUAL,
                symbol="ETHUSDT",
                interval="1h",
                open_time=open_time,
                close_time=open_time + timedelta(hours=1) - timedelta(milliseconds=1),
                open=previous,
                high=high,
                low=low,
                close=close,
                volume=Decimal("10"),
                quote_volume=Decimal("1000"),
                trade_count=10,
                is_closed=True,
                collected_at=start + timedelta(days=2),
            )
        )
        previous = close
    return tuple(items)


def make_marks(
    candles: tuple[FuturesCandle, ...],
    *,
    lows: tuple[str, ...] | None = None,
    highs: tuple[str, ...] | None = None,
    closes: tuple[str, ...] | None = None,
) -> tuple[MarkPriceCandle, ...]:
    items: list[MarkPriceCandle] = []
    for index, candle in enumerate(candles):
        close = Decimal(closes[index]) if closes else candle.close
        low = Decimal(lows[index]) if lows else min(candle.open, close) - Decimal("1")
        high = Decimal(highs[index]) if highs else max(candle.open, close) + Decimal("1")
        items.append(
            MarkPriceCandle(
                symbol=candle.symbol,
                interval=candle.interval,
                open_time=candle.open_time,
                close_time=candle.close_time,
                open=candle.open,
                high=high,
                low=low,
                close=close,
                is_closed=True,
                collected_at=candle.collected_at,
            )
        )
    return tuple(items)


@pytest.fixture
def futures_candles(start_time: datetime) -> tuple[FuturesCandle, ...]:
    return make_candles(start_time)


@pytest.fixture
def mark_prices(
    futures_candles: tuple[FuturesCandle, ...],
) -> tuple[MarkPriceCandle, ...]:
    return make_marks(futures_candles)


@pytest.fixture
def futures_config() -> FuturesBacktestConfig:
    return FuturesBacktestConfig(
        initial_balance=Decimal("10000"),
        funding_enabled=False,
        funding_missing_policy=FundingMissingPolicy.DISABLE_EXPLICITLY,
        warmup_candles=1,
        short_ema_period=1,
        long_ema_period=2,
        atr_period=1,
        volume_period=1,
        minimum_volume_ratio=Decimal("0"),
        maximum_position_notional_percent=Decimal("25"),
    )


def config_with(
    config: FuturesBacktestConfig,
    **changes: object,
) -> FuturesBacktestConfig:
    return replace(config, **changes)
