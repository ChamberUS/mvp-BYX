"""Decimal momentum and rolling structure indicators."""

from decimal import Decimal

from adaptive_trader.domain.models import Candle


def percent_return(candles: tuple[Candle, ...], period: int = 1) -> Decimal:
    if period < 1 or len(candles) <= period:
        raise ValueError("not enough candles for requested return period")
    start = candles[-period - 1].close
    if start <= 0:
        raise ValueError("starting close must be positive")
    return (candles[-1].close - start) / start * Decimal("100")


def rolling_high(candles: tuple[Candle, ...], period: int) -> Decimal:
    if period < 1 or len(candles) < period:
        raise ValueError("not enough candles for requested high period")
    return max(candle.high for candle in candles[-period:])


def rolling_low(candles: tuple[Candle, ...], period: int) -> Decimal:
    if period < 1 or len(candles) < period:
        raise ValueError("not enough candles for requested low period")
    return min(candle.low for candle in candles[-period:])


def distance_to_high_percent(candles: tuple[Candle, ...], period: int) -> Decimal:
    high = rolling_high(candles, period)
    if high <= 0:
        raise ValueError("rolling high must be positive")
    return (high - candles[-1].close) / high * Decimal("100")


def distance_to_low_percent(candles: tuple[Candle, ...], period: int) -> Decimal:
    low = rolling_low(candles, period)
    if low <= 0:
        raise ValueError("rolling low must be positive")
    return (candles[-1].close - low) / low * Decimal("100")


def higher_highs_and_lows(candles: tuple[Candle, ...], window: int = 2) -> bool:
    if window < 1 or len(candles) < window * 2:
        raise ValueError("not enough candles for structure analysis")
    previous = candles[-window * 2 : -window]
    current = candles[-window:]
    return max(candle.high for candle in current) > max(candle.high for candle in previous) and min(
        candle.low for candle in current
    ) > min(candle.low for candle in previous)


def lower_highs_and_lows(candles: tuple[Candle, ...], window: int = 2) -> bool:
    if window < 1 or len(candles) < window * 2:
        raise ValueError("not enough candles for structure analysis")
    previous = candles[-window * 2 : -window]
    current = candles[-window:]
    return max(candle.high for candle in current) < max(candle.high for candle in previous) and min(
        candle.low for candle in current
    ) < min(candle.low for candle in previous)
