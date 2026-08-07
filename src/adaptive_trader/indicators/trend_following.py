"""Point-in-time SMA and Donchian indicators for daily trend following."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from adaptive_trader.domain.models import Candle
from adaptive_trader.futures.models import FuturesCandle

type PriceCandle = Candle | FuturesCandle


@dataclass(frozen=True, slots=True)
class DonchianChannel:
    high: Decimal
    low: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.high, Decimal) or not isinstance(self.low, Decimal):
            raise TypeError("Donchian channel values must be Decimal")
        if not self.high.is_finite() or not self.low.is_finite():
            raise ValueError("Donchian channel values must be finite")
        if self.low > self.high:
            raise ValueError("Donchian channel low must not exceed high")


@dataclass(frozen=True, slots=True)
class TrendFollowingIndicators:
    sma: Decimal | None
    entry_channel: DonchianChannel | None
    exit_channel: DonchianChannel | None


def sma_close(
    candles: Sequence[PriceCandle],
    period: int = 200,
) -> Decimal | None:
    """Return the close SMA ending at t, including the current candle t."""

    _validate_period(period)
    if len(candles) < period:
        return None
    closes = (item.close for item in candles[-period:])
    return sum(closes, Decimal("0")) / Decimal(period)


def sma_200(candles: Sequence[PriceCandle]) -> Decimal | None:
    """Return the pre-registered 200-day close SMA ending at t."""

    return sma_close(candles, 200)


def donchian_channel(
    candles: Sequence[PriceCandle],
    period: int,
) -> DonchianChannel | None:
    """Return the channel from the previous ``period`` candles, excluding t."""

    _validate_period(period)
    if len(candles) <= period:
        return None
    previous = candles[-period - 1 : -1]
    return DonchianChannel(
        high=max(item.high for item in previous),
        low=min(item.low for item in previous),
    )


def previous_high(
    candles: Sequence[PriceCandle],
    period: int,
) -> Decimal | None:
    channel = donchian_channel(candles, period)
    return channel.high if channel is not None else None


def previous_low(
    candles: Sequence[PriceCandle],
    period: int,
) -> Decimal | None:
    channel = donchian_channel(candles, period)
    return channel.low if channel is not None else None


def trend_following_indicators(
    candles: Sequence[PriceCandle],
    *,
    sma_period: int = 200,
    entry_period: int = 20,
    exit_period: int = 10,
) -> TrendFollowingIndicators:
    """Calculate all values at t without accessing any candle after t."""

    _validate_period(sma_period)
    _validate_period(entry_period)
    _validate_period(exit_period)
    return TrendFollowingIndicators(
        sma=sma_close(candles, sma_period),
        entry_channel=donchian_channel(candles, entry_period),
        exit_channel=donchian_channel(candles, exit_period),
    )


def _validate_period(period: int) -> None:
    if period < 1:
        raise ValueError("indicator period must be positive")
