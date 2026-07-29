"""Deterministic EMA, volume and ATR strategy."""

from __future__ import annotations

from decimal import Decimal

from adaptive_trader.domain.models import MarketContext, MarketRegime, MarketSignal, SignalDirection
from adaptive_trader.strategy.regime import DeterministicRegimeClassifier


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
    ) -> None:
        self._minimum_volume_ratio = minimum_volume_ratio
        self._maximum_atr_relative = maximum_atr_relative
        self._stop_atr_multiple = stop_atr_multiple
        self._target_r_multiple = target_r_multiple
        self._short_period = short_period
        self._long_period = long_period
        self._classifier = DeterministicRegimeClassifier(
            short_period=short_period,
            long_period=long_period,
            maximum_atr_relative=maximum_atr_relative,
        )

    def analyze(self, context: MarketContext) -> MarketSignal:
        indicators = context.indicators
        required = ("ema_short", "ema_long", "volume_ratio", "atr")
        if any(name not in indicators for name in required):
            return self._hold_signal(context, "insufficient indicators for deterministic analysis")
        regime_result = self._classifier.classify(context.candles)
        short = indicators["ema_short"]
        long = indicators["ema_long"]
        volume = indicators["volume_ratio"]
        atr_value = indicators["atr"]
        close = context.latest_candle.close
        atr_relative = atr_value / close
        if regime_result.regime is not MarketRegime.TRENDING_UP:
            return self._hold_signal(
                context, f"regime={regime_result.regime}; {regime_result.rationale}"
            )
        if short <= long:
            return self._hold_signal(context, "EMA relationship is not bullish")
        if volume < self._minimum_volume_ratio:
            return self._hold_signal(context, "relative volume is below configured minimum")
        if atr_relative > self._maximum_atr_relative:
            return self._hold_signal(context, "relative ATR is above configured extreme threshold")
        stop_loss = close - atr_value * self._stop_atr_multiple
        risk_per_unit = close - stop_loss
        take_profit = close + risk_per_unit * self._target_r_multiple
        if stop_loss <= 0 or risk_per_unit <= 0:
            return self._hold_signal(context, "calculated stop is invalid")
        rationale = (
            f"regime={regime_result.regime}; ema_short={short}; ema_long={long}; "
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
        )

    def _hold_signal(self, context: MarketContext, rationale: str) -> MarketSignal:
        return MarketSignal(
            signal_id=f"{context.symbol}-{context.latest_candle.timestamp.isoformat()}-HOLD",
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=SignalDirection.HOLD,
            regime=MarketRegime.UNKNOWN,
            confidence=Decimal("0"),
            entry_price=context.latest_candle.close,
            stop_loss=Decimal("0"),
            take_profit=Decimal("0"),
            suggested_quantity=Decimal("0"),
            rationale=rationale,
            analyzer_name="deterministic-ema-atr-volume",
        )
