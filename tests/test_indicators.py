from decimal import Decimal

import pytest

from adaptive_trader.indicators import (
    atr,
    average_volume,
    candle_ema,
    candle_sma,
    historical_volatility,
    percent_return,
    volume_ratio,
)


def test_moving_averages_and_volume_are_decimal(candle) -> None:
    candles = tuple(
        candle.__class__(
            symbol="ETHUSDT",
            timestamp=candle.timestamp.replace(minute=candle.timestamp.minute + index),
            open=Decimal(str(100 + index)),
            high=Decimal(str(101 + index)),
            low=Decimal(str(99 + index)),
            close=Decimal(str(100 + index)),
            volume=Decimal(str(10 + index)),
        )
        for index in range(5)
    )

    assert candle_sma(candles, 3) == Decimal("103")
    assert candle_ema(candles, 3) == Decimal("103")
    assert average_volume(candles, 5) == Decimal("12")
    assert volume_ratio(candles, 5) == Decimal("14") / Decimal("12")


def test_volatility_and_return_values() -> None:
    from datetime import UTC, datetime, timedelta

    from adaptive_trader.domain.models import Candle

    candles = tuple(
        Candle(
            symbol="ETHUSDT",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            open=Decimal(str(100 + index)),
            high=Decimal(str(102 + index)),
            low=Decimal(str(99 + index)),
            close=Decimal(str(100 + index)),
            volume=Decimal("10"),
        )
        for index in range(4)
    )

    assert atr(candles, 2) == Decimal("3")
    assert percent_return(candles, 2) == Decimal("2") / Decimal("101") * Decimal("100")
    assert historical_volatility(candles, 2) > 0


def test_indicators_reject_insufficient_data(candle) -> None:
    with pytest.raises(ValueError):
        candle_sma((candle,), 2)
    with pytest.raises(ValueError):
        historical_volatility((candle,), 1)


def test_zero_average_volume_is_explicitly_rejected(candle) -> None:
    zero_volume = candle.__class__(
        symbol=candle.symbol,
        timestamp=candle.timestamp,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=Decimal("0"),
    )
    with pytest.raises(ZeroDivisionError):
        volume_ratio((zero_volume,), 1)
