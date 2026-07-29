"""Decimal-only moving average indicators."""

from decimal import Decimal

from adaptive_trader.domain.models import Candle


def _validate_period(values: tuple[Decimal, ...], period: int) -> None:
    if period < 1:
        raise ValueError("period must be positive")
    if len(values) < period:
        raise ValueError("not enough values for requested period")


def sma(values: tuple[Decimal, ...], period: int) -> Decimal:
    _validate_period(values, period)
    return sum(values[-period:], Decimal("0")) / Decimal(period)


def ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    _validate_period(values, period)
    multiplier = Decimal("2") / Decimal(period + 1)
    result = sma(values[:period], period)
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def candle_sma(candles: tuple[Candle, ...], period: int) -> Decimal:
    return sma(tuple(candle.close for candle in candles), period)


def candle_ema(candles: tuple[Candle, ...], period: int) -> Decimal:
    return ema(tuple(candle.close for candle in candles), period)
