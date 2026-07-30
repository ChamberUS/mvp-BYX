"""Deterministic EMA, volume and ATR strategy."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from adaptive_trader.domain.models import MarketContext, MarketRegime, MarketSignal, SignalDirection
from adaptive_trader.indicators import candle_ema
from adaptive_trader.strategy.regime import DeterministicRegimeClassifier, SpotRegimeMode


class DeterministicAnalyzer:
    def __init__(
        self,
        *,
        short_period: int = 20,
        long_period: int = 50,
        minimum_volume_ratio: Decimal = Decimal("1"),
        maximum_atr_relative: Decimal = Decimal("0.05"),
        stop_atr_multiple: Decimal = Decimal("2"),
        target_r_multiple: Decimal = Decimal("2"),
        regime_mode: SpotRegimeMode = SpotRegimeMode.STRICT_TRENDING_UP,
    ) -> None:
        self._minimum_volume_ratio = minimum_volume_ratio
        self._maximum_atr_relative = maximum_atr_relative
        self._stop_atr_multiple = stop_atr_multiple
        self._target_r_multiple = target_r_multiple
        self._short_period = short_period
        self._long_period = long_period
        self._regime_mode = regime_mode
        self._transition_cached_length = 0
        self._transition_first_open_time: datetime | None = None
        self._transition_last_open_time: datetime | None = None
        self._transition_short: Decimal | None = None
        self._transition_long: Decimal | None = None
        self._classifier = DeterministicRegimeClassifier(
            short_period=short_period,
            long_period=long_period,
            maximum_atr_relative=maximum_atr_relative,
        )

    def analyze(self, context: MarketContext) -> MarketSignal:
        indicators = context.indicators
        required = ("ema_short", "ema_long", "volume_ratio", "atr")
        if any(name not in indicators for name in required):
            return self._hold_signal(
                context,
                "insufficient indicators for deterministic analysis",
                "INSUFFICIENT_DATA",
            )
        regime_result = self._classifier.classify(context.candles)
        short = indicators["ema_short"]
        long = indicators["ema_long"]
        volume = indicators["volume_ratio"]
        atr_value = indicators["atr"]
        close = context.latest_candle.close
        atr_relative = atr_value / close
        if not self._regime_allows_entry(context, regime_result.regime, short, long):
            return self._hold_signal(
                context,
                f"regime={regime_result.regime}; mode={self._regime_mode}; "
                f"{regime_result.rationale}",
                "REGIME_NOT_UP",
                regime_result.regime,
            )
        if short <= long:
            return self._hold_signal(
                context,
                "EMA relationship is not bullish",
                "EMA_NOT_CONFIRMED",
                regime_result.regime,
            )
        if (
            self._regime_mode
            in {SpotRegimeMode.UP_OR_TRANSITION, SpotRegimeMode.EMA_TREND_ONLY}
            and close <= long
        ):
            return self._hold_signal(
                context,
                "price is not above the long EMA",
                "PRICE_NOT_ABOVE_LONG_EMA",
                regime_result.regime,
            )
        if volume < self._minimum_volume_ratio:
            return self._hold_signal(
                context,
                "relative volume is below configured minimum",
                "VOLUME_TOO_LOW",
                regime_result.regime,
            )
        if atr_relative > self._maximum_atr_relative:
            return self._hold_signal(
                context,
                "relative ATR is above configured extreme threshold",
                "VOLATILITY_TOO_HIGH",
                regime_result.regime,
            )
        stop_loss = close - atr_value * self._stop_atr_multiple
        risk_per_unit = close - stop_loss
        take_profit = close + risk_per_unit * self._target_r_multiple
        if stop_loss <= 0 or risk_per_unit <= 0:
            return self._hold_signal(
                context, "calculated stop is invalid", "INVALID_STOP", regime_result.regime
            )
        rationale = (
            f"regime={regime_result.regime}; ema_short={short}; ema_long={long}; "
            f"regime_mode={self._regime_mode}; "
            f"volume_ratio={volume}; atr={atr_value}; stop={stop_loss}; "
            f"target={take_profit}; risk_reward={self._target_r_multiple}"
        )
        return MarketSignal(
            signal_id=f"{context.symbol}-{context.latest_candle.timestamp.isoformat()}-BUY",
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=SignalDirection.BUY,
            regime=regime_result.regime,
            confidence=Decimal("0.75"),
            entry_price=close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            suggested_quantity=indicators.get("suggested_quantity", Decimal("0")),
            rationale=rationale,
            analyzer_name="deterministic-ema-atr-volume",
            reason_code="BUY_APPROVED",
        )

    def _regime_allows_entry(
        self,
        context: MarketContext,
        regime: MarketRegime,
        short: Decimal,
        long: Decimal,
    ) -> bool:
        if self._regime_mode is SpotRegimeMode.STRICT_TRENDING_UP:
            return regime is MarketRegime.TRENDING_UP
        if self._regime_mode is SpotRegimeMode.NO_REGIME_FILTER_DIAGNOSTIC:
            return True
        if self._regime_mode is SpotRegimeMode.EMA_TREND_ONLY:
            return short > long and context.latest_candle.close > long
        previous_values = self._previous_trend_values(context, short, long)
        if regime is MarketRegime.TRENDING_UP:
            return True
        if previous_values is None:
            return False
        previous_short, previous_long, previous_close = previous_values
        return (
            previous_short <= previous_long
            and previous_close <= previous_long
            and short > long
            and context.latest_candle.close > long
        )

    def _previous_trend_values(
        self,
        context: MarketContext,
        short: Decimal,
        long: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal] | None:
        candles = context.candles
        if len(candles) <= self._long_period:
            return None
        sequential = (
            self._transition_cached_length == len(candles) - 1
            and self._transition_first_open_time == candles[0].open_time
            and self._transition_last_open_time == candles[-2].open_time
            and self._transition_short is not None
            and self._transition_long is not None
        )
        if sequential:
            assert self._transition_short is not None
            assert self._transition_long is not None
            previous_short = self._transition_short
            previous_long = self._transition_long
        else:
            previous = tuple(candles[:-1])
            previous_short = candle_ema(previous, self._short_period)
            previous_long = candle_ema(previous, self._long_period)
        self._transition_cached_length = len(candles)
        self._transition_first_open_time = candles[0].open_time
        self._transition_last_open_time = candles[-1].open_time
        self._transition_short = short
        self._transition_long = long
        return previous_short, previous_long, candles[-2].close

    def _hold_signal(
        self,
        context: MarketContext,
        rationale: str,
        reason_code: str,
        regime: MarketRegime = MarketRegime.UNKNOWN,
    ) -> MarketSignal:
        return MarketSignal(
            signal_id=f"{context.symbol}-{context.latest_candle.timestamp.isoformat()}-HOLD",
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=SignalDirection.HOLD,
            regime=regime,
            confidence=Decimal("0"),
            entry_price=context.latest_candle.close,
            stop_loss=Decimal("0"),
            take_profit=Decimal("0"),
            suggested_quantity=Decimal("0"),
            rationale=rationale,
            analyzer_name="deterministic-ema-atr-volume",
            reason_code=reason_code,
        )
