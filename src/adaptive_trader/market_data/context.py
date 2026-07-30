"""Stateless construction of analysis contexts without future candles."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import overload

from adaptive_trader.domain.models import Candle, MarketContext
from adaptive_trader.indicators import atr, candle_ema, historical_volatility, volume_ratio


class CandleHistoryView(Sequence[Candle]):
    def __init__(
        self, candles: tuple[Candle, ...], start: int = 0, stop: int | None = None
    ) -> None:
        self._candles = candles
        self._start = start
        self._stop = len(candles) if stop is None else stop

    def __len__(self) -> int:
        return self._stop - self._start

    @overload
    def __getitem__(self, index: int) -> Candle: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Candle]: ...

    def __getitem__(self, index: int | slice) -> Candle | Sequence[Candle]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step == 1:
                return CandleHistoryView(
                    self._candles,
                    self._start + start,
                    self._start + stop,
                )
            return tuple(self[position] for position in range(start, stop, step))
        position = index if index >= 0 else len(self) + index
        if position < 0 or position >= len(self):
            raise IndexError("candle history index out of range")
        return self._candles[self._start + position]


class MarketContextBuilder:
    def __init__(
        self,
        minimum_candles: int = 1,
        *,
        short_ema_period: int = 20,
        long_ema_period: int = 50,
        atr_period: int = 14,
        volume_period: int = 20,
        cache_sequential: bool = False,
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
        self._cache_sequential = cache_sequential
        self._cached_length = 0
        self._cached_first_open_time: datetime | None = None
        self._cached_last_open_time: datetime | None = None
        self._cached_ema: dict[int, Decimal] = {}

    def build(
        self,
        candles: Iterable[Candle],
        *,
        symbol: str,
        interval: str,
        analysis_time: datetime,
        suggested_quantity: Decimal = Decimal("0"),
        validate: bool = True,
    ) -> MarketContext:
        if analysis_time.tzinfo is None or analysis_time.utcoffset() is None:
            raise ValueError("analysis_time must be timezone-aware")
        ordered: Sequence[Candle] = candles if isinstance(candles, Sequence) else tuple(candles)
        if len(ordered) < self._minimum_candles:
            raise ValueError("not enough candles to build context")
        if validate:
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
        ema_values = self._ema_values(ordered)
        if self._short_ema_period in ema_values:
            indicators["ema_short"] = ema_values[self._short_ema_period]
        if self._long_ema_period in ema_values:
            indicators["ema_long"] = ema_values[self._long_ema_period]
        if len(ordered) >= self._volume_period:
            indicators["volume_ratio"] = volume_ratio(
                tuple(ordered[-self._volume_period :]), self._volume_period
            )
        if len(ordered) >= self._atr_period:
            indicators["atr"] = atr(
                tuple(ordered[-(self._atr_period + 1) :]), self._atr_period
            )
        if len(ordered) >= self._atr_period + 1:
            indicators["historical_volatility"] = historical_volatility(
                tuple(ordered[-(self._atr_period + 1) :]), self._atr_period
            )
        return MarketContext(
            symbol=symbol,
            created_at=analysis_time,
            candles=ordered,
            latest_candle=ordered[-1],
            indicators=indicators,
            interval=interval,
        )

    def _ema_values(self, candles: Sequence[Candle]) -> dict[int, Decimal]:
        periods = (self._short_ema_period, self._long_ema_period)
        sequential = (
            self._cache_sequential
            and self._cached_length == len(candles) - 1
            and self._cached_first_open_time == candles[0].open_time
            and self._cached_last_open_time == candles[-2].open_time
        )
        if not sequential:
            self._cached_ema = {}
            for period in periods:
                if len(candles) >= period:
                    self._cached_ema[period] = candle_ema(tuple(candles), period)
        else:
            multiplier = {
                period: Decimal("2") / Decimal(period + 1) for period in periods
            }
            for period in periods:
                if len(candles) == period:
                    self._cached_ema[period] = candle_ema(tuple(candles), period)
                elif len(candles) > period and period in self._cached_ema:
                    previous = self._cached_ema[period]
                    close = candles[-1].close
                    self._cached_ema[period] = (
                        close - previous
                    ) * multiplier[period] + previous
        if self._cache_sequential:
            self._cached_length = len(candles)
            self._cached_first_open_time = candles[0].open_time
            self._cached_last_open_time = candles[-1].open_time
        return dict(self._cached_ema)
