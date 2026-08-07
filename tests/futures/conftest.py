from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.market import ContractType, MarketType
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FundingRate,
    FuturesBacktestConfig,
    FuturesCandle,
    MarkPriceCandle,
)
from adaptive_trader.futures.real_validation import (
    FuturesRealValidationService,
    RealValidationBundle,
    RealValidationPeriods,
)
from adaptive_trader.storage.sqlite import DatabaseRepository


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


@pytest.fixture(scope="session")
def real_fixture_periods() -> RealValidationPeriods:
    return RealValidationPeriods(
        development_start=datetime(2023, 1, 1, tzinfo=UTC),
        development_end=datetime(2024, 3, 31, 23, tzinfo=UTC),
        validation_start=datetime(2024, 4, 1, tzinfo=UTC),
        validation_end=datetime(2024, 6, 29, 23, tzinfo=UTC),
        consumed_test_start=datetime(2026, 1, 1, tzinfo=UTC),
        consumed_test_end=datetime(2026, 7, 1, tzinfo=UTC),
    )


@pytest.fixture(scope="session")
def real_fixture_bundle(
    tmp_path_factory: pytest.TempPathFactory,
    real_fixture_periods: RealValidationPeriods,
) -> RealValidationBundle:
    database_path = tmp_path_factory.mktemp("futures-real") / "fixture.sqlite3"
    total_hours = int(
        (
            real_fixture_periods.validation_end
            - real_fixture_periods.development_start
        )
        / timedelta(hours=1)
    ) + 1
    candles: list[FuturesCandle] = []
    marks: list[MarkPriceCandle] = []
    previous = Decimal("1000")
    collected_at = datetime(2025, 1, 1, tzinfo=UTC)
    for index in range(total_hours):
        phase = index % 480
        close = (
            Decimal("1000") + Decimal(phase) * Decimal("0.5")
            if phase < 240
            else Decimal("1120") - Decimal(phase - 240) * Decimal("0.5")
        )
        open_time = real_fixture_periods.development_start + timedelta(hours=index)
        close_time = open_time + timedelta(hours=1) - timedelta(milliseconds=1)
        high = max(previous, close) + Decimal("2")
        low = min(previous, close) - Decimal("2")
        candle = FuturesCandle(
            exchange="BINANCE",
            market_type=MarketType.USD_M_FUTURES,
            contract_type=ContractType.PERPETUAL,
            symbol="ETHUSDT",
            interval="1h",
            open_time=open_time,
            close_time=close_time,
            open=previous,
            high=high,
            low=low,
            close=close,
            volume=Decimal("100"),
            quote_volume=Decimal("100000"),
            trade_count=100,
            is_closed=True,
            collected_at=collected_at,
        )
        candles.append(candle)
        marks.append(
            MarkPriceCandle(
                symbol="ETHUSDT",
                interval="1h",
                open_time=open_time,
                close_time=close_time,
                open=previous,
                high=high,
                low=low,
                close=close,
                is_closed=True,
                collected_at=collected_at,
            )
        )
        previous = close
    funding = tuple(
        FundingRate(
            symbol="ETHUSDT",
            funding_time=real_fixture_periods.development_start
            + timedelta(hours=index),
            funding_rate=(
                Decimal("0.0001")
                if (index // 8) % 2 == 0
                else Decimal("-0.00005")
            ),
            mark_price=marks[index].close,
        )
        for index in range(0, total_hours, 8)
    )
    repository = DatabaseRepository(database_path)
    try:
        repository.upsert_futures_candles(tuple(candles))
        repository.upsert_mark_prices(tuple(marks))
        repository.upsert_funding_rates(funding)
        return FuturesRealValidationService(
            repository,
            TradingConfig(database_path=database_path, interval="1h"),
        ).run(
            symbol="ETHUSDT",
            interval="1h",
            periods=real_fixture_periods,
            leverage=Decimal("1"),
        )
    finally:
        repository.close()
