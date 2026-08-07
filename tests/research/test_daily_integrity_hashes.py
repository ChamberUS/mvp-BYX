from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.market import ContractType, MarketType
from adaptive_trader.domain.models import Candle
from adaptive_trader.futures.models import FuturesCandle
from adaptive_trader.research.daily_aggregation import (
    DailyAggregationConfig,
    DailyCandleAggregator,
    IncompleteDayPolicy,
)


def _spot_hours() -> tuple[Candle, ...]:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(
            symbol="ETHUSDT",
            interval="1h",
            timestamp=start + timedelta(hours=index),
            close_time=start + timedelta(hours=index + 1) - timedelta(milliseconds=1),
            open=Decimal("100"),
            high=Decimal("110") if index == 23 else Decimal("105"),
            low=Decimal("90") if index == 23 else Decimal("95"),
            close=Decimal("101"),
            volume=Decimal("1"),
            quote_volume=Decimal("100"),
            trades_count=1,
        )
        for index in range(24)
    )


def _futures_hours() -> tuple[FuturesCandle, ...]:
    return tuple(
        FuturesCandle(
            exchange=item.exchange,
            market_type=MarketType.USD_M_FUTURES,
            contract_type=ContractType.PERPETUAL,
            symbol=item.symbol,
            interval=item.interval,
            open_time=item.open_time,
            close_time=item.close_time or item.open_time + timedelta(hours=1),
            open=item.open,
            high=item.high,
            low=item.low,
            close=item.close,
            volume=item.volume,
            quote_volume=item.quote_volume or Decimal("0"),
            trade_count=item.trades_count or 0,
            is_closed=item.is_closed,
        )
        for item in _spot_hours()
    )


def test_hash_is_order_independent_but_integrity_records_input_order() -> None:
    source = _spot_hours()

    ordered = DailyCandleAggregator().aggregate_spot(source)
    reversed_result = DailyCandleAggregator().aggregate_spot(tuple(reversed(source)))

    assert ordered.source_hourly_hash == reversed_result.source_hourly_hash
    assert ordered.daily_rows_hash == reversed_result.daily_rows_hash
    assert ordered.daily_candle_hash == reversed_result.daily_candle_hash
    assert ordered.integrity.input_strictly_increasing
    assert not reversed_result.integrity.input_strictly_increasing


def test_combined_daily_hash_changes_when_an_internal_hour_changes() -> None:
    source = _spot_hours()
    changed = (
        *source[:5],
        replace(source[5], high=Decimal("106")),
        *source[6:],
    )

    baseline = DailyCandleAggregator().aggregate_spot(source)
    modified = DailyCandleAggregator().aggregate_spot(changed)

    assert baseline.candles == modified.candles
    assert baseline.daily_rows_hash == modified.daily_rows_hash
    assert baseline.source_hourly_hash != modified.source_hourly_hash
    assert baseline.daily_candle_hash != modified.daily_candle_hash


def test_configuration_hash_is_separate_and_part_of_complete_hash() -> None:
    source = _spot_hours()
    warned = DailyCandleAggregator().aggregate_spot(source)
    strict = DailyCandleAggregator(
        DailyAggregationConfig(incomplete_day_policy=IncompleteDayPolicy.FAIL)
    ).aggregate_spot(source)

    assert warned.source_hourly_hash == strict.source_hourly_hash
    assert warned.daily_rows_hash == strict.daily_rows_hash
    assert warned.aggregation_config_hash != strict.aggregation_config_hash
    assert warned.daily_candle_hash != strict.daily_candle_hash


def test_spot_and_futures_have_separate_hash_namespaces() -> None:
    spot = DailyCandleAggregator().aggregate_spot(_spot_hours())
    futures = DailyCandleAggregator().aggregate_futures(_futures_hours())

    assert spot.market_type is MarketType.SPOT
    assert futures.market_type is MarketType.USD_M_FUTURES
    assert spot.source_hourly_hash != futures.source_hourly_hash
    assert spot.daily_rows_hash != futures.daily_rows_hash
    assert spot.daily_candle_hash != futures.daily_candle_hash
