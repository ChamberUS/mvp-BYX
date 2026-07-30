"""Reproducible market regime classification."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from adaptive_trader.domain.models import Candle, MarketRegime
from adaptive_trader.indicators import candle_ema, higher_highs_and_lows, lower_highs_and_lows


@dataclass(frozen=True, slots=True)
class RegimeResult:
    regime: MarketRegime
    rationale: str


class SpotRegimeMode(StrEnum):
    STRICT_TRENDING_UP = "STRICT_TRENDING_UP"
    UP_OR_TRANSITION = "UP_OR_TRANSITION"
    EMA_TREND_ONLY = "EMA_TREND_ONLY"
    NO_REGIME_FILTER_DIAGNOSTIC = "NO_REGIME_FILTER_DIAGNOSTIC"

    @property
    def diagnostic_only(self) -> bool:
        return self is SpotRegimeMode.NO_REGIME_FILTER_DIAGNOSTIC


class DeterministicRegimeClassifier:
    def __init__(
        self,
        short_period: int = 20,
        long_period: int = 50,
        slope_lookback: int = 5,
        maximum_atr_relative: Decimal = Decimal("0.05"),
    ) -> None:
        if short_period < 1 or long_period <= short_period or slope_lookback < 1:
            raise ValueError("invalid regime classifier periods")
        self._short_period = short_period
        self._long_period = long_period
        self._slope_lookback = slope_lookback
        self._maximum_atr_relative = maximum_atr_relative
        self._cached_length = 0
        self._cached_first_open_time: datetime | None = None
        self._cached_last_open_time: datetime | None = None
        self._cached_short: Decimal | None = None
        self._cached_long: Decimal | None = None
        self._short_history: dict[int, Decimal] = {}

    def classify(self, candles: Sequence[Candle]) -> RegimeResult:
        minimum = self._long_period + self._slope_lookback
        if len(candles) < minimum:
            return RegimeResult(
                MarketRegime.UNKNOWN, "insufficient candles for regime classification"
            )
        short, long, previous_short = self._ema_values(candles)
        distance = abs(short - long) / candles[-1].close
        rising = short > previous_short
        falling = short < previous_short
        if distance <= self._maximum_atr_relative / Decimal("2"):
            return RegimeResult(MarketRegime.RANGING, "EMA distance is narrow")
        structure = tuple(candles[-self._slope_lookback * 2 :])
        if short > long and rising and higher_highs_and_lows(structure, self._slope_lookback):
            return RegimeResult(
                MarketRegime.TRENDING_UP, "short EMA leads, rises, and structure is ascending"
            )
        if short < long and falling and lower_highs_and_lows(structure, self._slope_lookback):
            return RegimeResult(
                MarketRegime.TRENDING_DOWN, "short EMA lags, falls, and structure is descending"
            )
        return RegimeResult(MarketRegime.RANGING, "trend criteria are not jointly confirmed")

    def _ema_values(self, candles: Sequence[Candle]) -> tuple[Decimal, Decimal, Decimal]:
        sequential = (
            self._cached_length == len(candles) - 1
            and self._cached_first_open_time == candles[0].open_time
            and self._cached_last_open_time == candles[-2].open_time
            and self._cached_short is not None
            and self._cached_long is not None
        )
        if not sequential:
            materialized = tuple(candles)
            short = candle_ema(materialized, self._short_period)
            long = candle_ema(materialized, self._long_period)
            previous_length = len(candles) - self._slope_lookback
            previous_short = candle_ema(
                tuple(candles[:previous_length]), self._short_period
            )
            self._short_history = {}
            for length in range(previous_length, len(candles) + 1):
                self._short_history[length] = candle_ema(
                    tuple(candles[:length]), self._short_period
                )
        else:
            short_multiplier = Decimal("2") / Decimal(self._short_period + 1)
            long_multiplier = Decimal("2") / Decimal(self._long_period + 1)
            assert self._cached_short is not None
            assert self._cached_long is not None
            short = (candles[-1].close - self._cached_short) * short_multiplier + self._cached_short
            long = (candles[-1].close - self._cached_long) * long_multiplier + self._cached_long
            self._short_history[len(candles)] = short
            previous_short = self._short_history[len(candles) - self._slope_lookback]
        self._cached_length = len(candles)
        self._cached_first_open_time = candles[0].open_time
        self._cached_last_open_time = candles[-1].open_time
        self._cached_short = short
        self._cached_long = long
        return short, long, previous_short
