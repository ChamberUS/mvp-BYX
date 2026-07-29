"""Deterministic moving-average analyzer for the first research version."""

from decimal import Decimal

from adaptive_trader.domain.models import MarketContext, MarketRegime, MarketSignal, SignalDirection
from adaptive_trader.indicators.basic import simple_moving_average


class DeterministicAnalyzer:
    def __init__(self, fast_period: int = 3, slow_period: int = 5) -> None:
        if fast_period < 1 or slow_period <= fast_period:
            raise ValueError("slow_period must be greater than fast_period")
        self._fast_period = fast_period
        self._slow_period = slow_period

    def analyze(self, context: MarketContext) -> MarketSignal:
        close = context.latest_candle.close
        if len(context.candles) < self._slow_period:
            return self._hold_signal(context, "insufficient candles for deterministic analysis")

        fast = simple_moving_average(context.candles, self._fast_period)
        slow = simple_moving_average(context.candles, self._slow_period)
        if close > fast > slow:
            return MarketSignal(
                signal_id=f"{context.symbol}-{context.latest_candle.timestamp.isoformat()}-BUY",
                symbol=context.symbol,
                generated_at=context.created_at,
                direction=SignalDirection.BUY,
                regime=MarketRegime.TRENDING_UP,
                confidence=Decimal("0.75"),
                entry_price=close,
                stop_loss=close * Decimal("0.98"),
                take_profit=close * Decimal("1.04"),
                suggested_quantity=context.indicators.get("suggested_quantity", Decimal("0")),
                rationale="close is above fast and slow moving averages",
                analyzer_name="deterministic-moving-average",
            )
        return self._hold_signal(context, "moving-average conditions are not actionable")

    def _hold_signal(self, context: MarketContext, rationale: str) -> MarketSignal:
        return MarketSignal(
            signal_id=f"{context.symbol}-{context.latest_candle.timestamp.isoformat()}-HOLD",
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=SignalDirection.HOLD,
            regime=MarketRegime.RANGING,
            confidence=Decimal("0"),
            entry_price=context.latest_candle.close,
            stop_loss=Decimal("0"),
            take_profit=Decimal("0"),
            suggested_quantity=Decimal("0"),
            rationale=rationale,
            analyzer_name="deterministic-moving-average",
        )
