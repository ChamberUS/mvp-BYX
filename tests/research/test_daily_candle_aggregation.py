from datetime import UTC, datetime
from decimal import Decimal

from adaptive_trader.research.daily_aggregation import DailyCandleAggregator
from tests.research.test_daily_aggregation import _spot_hours


def test_daily_candle_aggregation_uses_first_open_last_close_and_daily_sums() -> None:
    start = datetime(2023, 1, 2, tzinfo=UTC)
    source = _spot_hours(start)

    result = DailyCandleAggregator().aggregate_spot(source)

    assert len(result.candles) == 1
    daily = result.candles[0]
    assert daily.open_time == start
    assert daily.close_time == source[-1].close_time
    assert daily.open == source[0].open
    assert daily.high == max(item.high for item in source)
    assert daily.low == min(item.low for item in source)
    assert daily.close == source[-1].close
    assert daily.volume == Decimal("300")
    assert daily.quote_volume == Decimal("3000")
    assert daily.trades_count == 300
    assert daily.interval == "1d"
    assert daily.is_closed
