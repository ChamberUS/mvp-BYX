from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.models import Candle
from adaptive_trader.indicators.trend_following import (
    donchian_channel,
    previous_high,
    previous_low,
)


def _candles(count: int) -> tuple[Candle, ...]:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(
            symbol="ETHUSDT",
            interval="1d",
            timestamp=start + timedelta(days=index),
            close_time=start + timedelta(days=index + 1) - timedelta(milliseconds=1),
            open=Decimal(100 + index),
            high=Decimal(110 + index),
            low=Decimal(90 + index),
            close=Decimal(100 + index),
            volume=Decimal("1"),
        )
        for index in range(count)
    )


def test_donchian_20_excludes_the_current_candle() -> None:
    source = _candles(21)
    current_extreme = replace(
        source[-1],
        high=Decimal("999"),
        low=Decimal("1"),
    )
    prefix = (*source[:-1], current_extreme)

    channel = donchian_channel(prefix, 20)

    assert channel is not None
    assert channel.high == Decimal("129")
    assert channel.low == Decimal("90")
    assert previous_high(prefix, 20) == Decimal("129")
    assert previous_low(prefix, 20) == Decimal("90")


def test_current_candle_enters_the_channel_only_at_the_next_point_in_time() -> None:
    source = _candles(21)
    current_extreme = replace(source[-1], high=Decimal("999"), low=Decimal("1"))
    current_prefix = (*source[:-1], current_extreme)
    next_candle = replace(
        source[-1],
        timestamp=source[-1].timestamp + timedelta(days=1),
        close_time=(source[-1].close_time or source[-1].timestamp) + timedelta(days=1),
    )

    at_t = donchian_channel(current_prefix, 20)
    at_t_plus_one = donchian_channel((*current_prefix, next_candle), 20)

    assert at_t is not None
    assert at_t_plus_one is not None
    assert at_t.high == Decimal("129")
    assert at_t.low == Decimal("90")
    assert at_t_plus_one.high == Decimal("999")
    assert at_t_plus_one.low == Decimal("1")


def test_donchian_returns_none_until_current_plus_full_history_exists() -> None:
    assert donchian_channel(_candles(20), 20) is None
    assert donchian_channel(_candles(11), 10) is not None
