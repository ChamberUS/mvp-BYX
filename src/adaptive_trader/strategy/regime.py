"""Reproducible market regime classification."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from adaptive_trader.domain.models import Candle, MarketRegime
from adaptive_trader.indicators import candle_ema, higher_highs_and_lows, lower_highs_and_lows


@dataclass(frozen=True, slots=True)
class RegimeResult:
    regime: MarketRegime
    rationale: str


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

    def classify(self, candles: tuple[Candle, ...]) -> RegimeResult:
        minimum = self._long_period + self._slope_lookback
        if len(candles) < minimum:
            return RegimeResult(
                MarketRegime.UNKNOWN, "insufficient candles for regime classification"
            )
        short = candle_ema(candles, self._short_period)
        long = candle_ema(candles, self._long_period)
        previous_short = candle_ema(candles[: -self._slope_lookback], self._short_period)
        distance = abs(short - long) / candles[-1].close
        rising = short > previous_short
        falling = short < previous_short
        if distance <= self._maximum_atr_relative / Decimal("2"):
            return RegimeResult(MarketRegime.RANGING, "EMA distance is narrow")
        if short > long and rising and higher_highs_and_lows(candles, self._slope_lookback):
            return RegimeResult(
                MarketRegime.TRENDING_UP, "short EMA leads, rises, and structure is ascending"
            )
        if short < long and falling and lower_highs_and_lows(candles, self._slope_lookback):
            return RegimeResult(
                MarketRegime.TRENDING_DOWN, "short EMA lags, falls, and structure is descending"
            )
        return RegimeResult(MarketRegime.RANGING, "trend criteria are not jointly confirmed")
