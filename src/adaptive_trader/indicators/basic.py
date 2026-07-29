"""Small Decimal-only indicator functions."""

from decimal import Decimal

from adaptive_trader.domain.models import Candle


def simple_moving_average(candles: tuple[Candle, ...], period: int) -> Decimal:
    if period < 1:
        raise ValueError("period must be positive")
    if len(candles) < period:
        raise ValueError("not enough candles for moving average")
    closes = tuple(candle.close for candle in candles[-period:])
    return sum(closes, Decimal("0")) / Decimal(period)
