"""Small Decimal-only indicator functions."""

from decimal import Decimal

from adaptive_trader.domain.models import Candle
from adaptive_trader.indicators.moving_average import sma


def simple_moving_average(candles: tuple[Candle, ...], period: int) -> Decimal:
    return sma(tuple(candle.close for candle in candles), period)
