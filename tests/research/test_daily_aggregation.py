from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.market import ContractType, MarketType
from adaptive_trader.domain.models import Candle
from adaptive_trader.futures.models import FuturesCandle
from adaptive_trader.research.daily_aggregation import DailyCandleAggregator


def _spot_hours(start: datetime) -> tuple[Candle, ...]:
    collected_at = start + timedelta(days=2)
    return tuple(
        Candle(
            exchange="BINANCE",
            symbol="ETHUSDT",
            interval="1h",
            timestamp=start + timedelta(hours=index),
            close_time=start + timedelta(hours=index + 1) - timedelta(milliseconds=1),
            open=Decimal(100 + index),
            high=Decimal(102 + index),
            low=Decimal(98 + index),
            close=Decimal(101 + index),
            volume=Decimal(index + 1),
            quote_volume=Decimal((index + 1) * 10),
            trades_count=index + 1,
            taker_buy_base_volume=Decimal(index + 1) / Decimal("2"),
            taker_buy_quote_volume=Decimal(index + 1) * Decimal("5"),
            is_closed=True,
            collected_at=collected_at,
        )
        for index in range(24)
    )


def _futures_hours(start: datetime) -> tuple[FuturesCandle, ...]:
    collected_at = start + timedelta(days=2)
    return tuple(
        FuturesCandle(
            exchange="BINANCE",
            market_type=MarketType.USD_M_FUTURES,
            contract_type=ContractType.PERPETUAL,
            symbol="ETHUSDT",
            interval="1h",
            open_time=start + timedelta(hours=index),
            close_time=start + timedelta(hours=index + 1) - timedelta(milliseconds=1),
            open=Decimal(100 + index),
            high=Decimal(102 + index),
            low=Decimal(98 + index),
            close=Decimal(101 + index),
            volume=Decimal(index + 1),
            quote_volume=Decimal((index + 1) * 10),
            trade_count=index + 1,
            is_closed=True,
            collected_at=collected_at,
        )
        for index in range(24)
    )


def test_aggregates_spot_ohlcv_and_metadata_in_utc() -> None:
    start = datetime(2023, 1, 2, tzinfo=UTC)
    source = _spot_hours(start)

    result = DailyCandleAggregator().aggregate_spot(tuple(reversed(source)))

    assert len(result.candles) == 1
    daily = result.candles[0]
    assert daily.open_time == start
    assert daily.close_time == source[-1].close_time
    assert daily.interval == "1d"
    assert daily.open == Decimal("100")
    assert daily.high == Decimal("125")
    assert daily.low == Decimal("98")
    assert daily.close == Decimal("124")
    assert daily.volume == Decimal("300")
    assert daily.quote_volume == Decimal("3000")
    assert daily.trades_count == 300
    assert daily.taker_buy_base_volume == Decimal("150")
    assert daily.taker_buy_quote_volume == Decimal("1500")
    assert daily.is_closed
    assert not result.integrity.input_strictly_increasing
    assert result.audits[0].complete


def test_aggregates_futures_without_converting_market_identity() -> None:
    start = datetime(2024, 2, 29, tzinfo=UTC)
    source = _futures_hours(start)

    result = DailyCandleAggregator().aggregate(source)

    assert result.market_type is MarketType.USD_M_FUTURES
    assert len(result.candles) == 1
    daily = result.candles[0]
    assert isinstance(daily, FuturesCandle)
    assert daily.market_type is MarketType.USD_M_FUTURES
    assert daily.contract_type is ContractType.PERPETUAL
    assert daily.interval == "1d"
    assert daily.open_time == start
    assert daily.close_time == source[-1].close_time
    assert daily.open == Decimal("100")
    assert daily.high == Decimal("125")
    assert daily.low == Decimal("98")
    assert daily.close == Decimal("124")
    assert daily.volume == Decimal("300")
    assert daily.quote_volume == Decimal("3000")
    assert daily.trade_count == 300
    assert daily.is_closed
    assert result.integrity.complete_day_count == 1


def test_optional_spot_aggregate_fields_remain_unknown_instead_of_becoming_zero() -> None:
    start = datetime(2023, 6, 1, tzinfo=UTC)
    source = tuple(
        Candle(
            symbol="ETHUSDT",
            interval="1h",
            timestamp=start + timedelta(hours=index),
            close_time=start + timedelta(hours=index + 1),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1"),
        )
        for index in range(24)
    )

    daily = DailyCandleAggregator().aggregate_spot(source).candles[0]

    assert daily.quote_volume is None
    assert daily.trades_count is None
    assert daily.taker_buy_base_volume is None
    assert daily.taker_buy_quote_volume is None
