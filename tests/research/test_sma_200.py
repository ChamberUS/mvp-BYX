from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.models import Candle
from adaptive_trader.indicators.trend_following import sma_200


def _daily_candles(count: int) -> tuple[Candle, ...]:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(
            symbol="ETHUSDT",
            interval="1d",
            timestamp=start + timedelta(days=index),
            close_time=start + timedelta(days=index + 1) - timedelta(milliseconds=1),
            open=Decimal(index + 1),
            high=Decimal(index + 2),
            low=Decimal(index + 1),
            close=Decimal(index + 1),
            volume=Decimal("1"),
        )
        for index in range(count)
    )


def test_sma_200_is_unavailable_for_199_days_and_includes_day_200() -> None:
    assert sma_200(_daily_candles(199)) is None
    assert sma_200(_daily_candles(200)) == Decimal("100.5")
