"""Decimal volume indicators."""

from decimal import Decimal

from adaptive_trader.domain.models import Candle
from adaptive_trader.indicators.moving_average import sma


def average_volume(candles: tuple[Candle, ...], period: int) -> Decimal:
    return sma(tuple(candle.volume for candle in candles), period)


def volume_ratio(candles: tuple[Candle, ...], period: int) -> Decimal:
    average = average_volume(candles, period)
    if average == 0:
        raise ZeroDivisionError("average volume is zero")
    return candles[-1].volume / average
