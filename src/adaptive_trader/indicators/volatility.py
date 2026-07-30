"""Volatility indicators implemented with Decimal arithmetic."""

from decimal import Decimal

from adaptive_trader.domain.models import Candle


def true_ranges(candles: tuple[Candle, ...]) -> tuple[Decimal, ...]:
    if not candles:
        raise ValueError("at least one candle is required")
    ranges: list[Decimal] = []
    previous_close: Decimal | None = None
    for candle in candles:
        if previous_close is None:
            ranges.append(candle.high - candle.low)
        else:
            ranges.append(
                max(
                    candle.high - candle.low,
                    abs(candle.high - previous_close),
                    abs(candle.low - previous_close),
                )
            )
        previous_close = candle.close
    return tuple(ranges)


def true_range(candles: tuple[Candle, ...]) -> Decimal:
    return true_ranges(candles)[-1]


def atr(candles: tuple[Candle, ...], period: int) -> Decimal:
    if period < 1 or len(candles) < period:
        raise ValueError("not enough candles for requested ATR period")
    start = len(candles) - period
    previous_close = candles[start - 1].close if start else None
    ranges: list[Decimal] = []
    for candle in candles[start:]:
        if previous_close is None:
            ranges.append(candle.high - candle.low)
        else:
            ranges.append(
                max(
                    candle.high - candle.low,
                    abs(candle.high - previous_close),
                    abs(candle.low - previous_close),
                )
            )
        previous_close = candle.close
    return sum(ranges, Decimal("0")) / Decimal(period)


def historical_volatility(candles: tuple[Candle, ...], period: int) -> Decimal:
    if period < 1:
        raise ValueError("period must be positive")
    if len(candles) < period + 1:
        raise ValueError("not enough candles for historical volatility")
    returns = tuple(
        (current.close - previous.close) / previous.close
        for previous, current in zip(candles[-period - 1 : -1], candles[-period:], strict=True)
    )
    average = sum(returns, Decimal("0")) / Decimal(period)
    variance = sum((value - average) ** 2 for value in returns) / Decimal(period)
    return variance.sqrt()


def relative_atr(candles: tuple[Candle, ...], period: int) -> Decimal:
    latest = candles[-1].close
    if latest <= 0:
        raise ValueError("latest close must be positive")
    return atr(candles, period) / latest
