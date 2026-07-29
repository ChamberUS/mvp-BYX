"""Stateless construction of analysis contexts without future candles."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from adaptive_trader.domain.models import Candle, MarketContext
from adaptive_trader.indicators import atr, candle_ema, historical_volatility, volume_ratio


class MarketContextBuilder:
    def __init__(
        self,
        minimum_candles: int = 1,
        *,
        short_ema_period: int = 20,
        long_ema_period: int = 50,
        atr_period: int = 14,
        volume_period: int = 20,
    ) -> None:
        if minimum_candles < 1:
            raise ValueError("minimum_candles must be positive")
        if short_ema_period < 1 or long_ema_period <= short_ema_period:
            raise ValueError("EMA periods are invalid")
        self._minimum_candles = minimum_candles
        self._short_ema_period = short_ema_period
        self._long_ema_period = long_ema_period
        self._atr_period = atr_period
        self._volume_period = volume_period

    def build(
        self,
        candles: Iterable[Candle],
        *,
        symbol: str,
        interval: str,
        analysis_time: datetime,
        suggested_quantity: Decimal = Decimal("0"),
    ) -> MarketContext:
        if analysis_time.tzinfo is None or analysis_time.utcoffset() is None:
            raise ValueError("analysis_time must be timezone-aware")
        ordered = tuple(candles)
        if len(ordered) < self._minimum_candles:
            raise ValueError("not enough candles to build context")
        previous: Candle | None = None
        for candle in ordered:
            if candle.symbol != symbol:
                raise ValueError("context contains a different symbol")
            if candle.interval != interval:
                raise ValueError("context contains a different interval")
            if not candle.is_closed:
                raise ValueError("context accepts closed candles only")
            if candle.open_time > analysis_time:
                raise ValueError("context contains a future candle")
            if previous is not None and candle.open_time <= previous.open_time:
                raise ValueError("context candles must be strictly chronological")
            previous = candle
        indicators: dict[str, Decimal] = {"suggested_quantity": suggested_quantity}
        if len(ordered) >= self._short_ema_period:
            indicators["ema_short"] = candle_ema(ordered, self._short_ema_period)
        if len(ordered) >= self._long_ema_period:
            indicators["ema_long"] = candle_ema(ordered, self._long_ema_period)
        if len(ordered) >= self._volume_period:
            indicators["volume_ratio"] = volume_ratio(ordered, self._volume_period)
        if len(ordered) >= self._atr_period:
            indicators["atr"] = atr(ordered, self._atr_period)
        if len(ordered) >= self._atr_period + 1:
            indicators["historical_volatility"] = historical_volatility(ordered, self._atr_period)
        return MarketContext(
            symbol=symbol,
            created_at=analysis_time,
            candles=ordered,
            latest_candle=ordered[-1],
            indicators=indicators,
            interval=interval,
        )
