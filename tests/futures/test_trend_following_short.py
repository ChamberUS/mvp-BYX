from datetime import timedelta
from decimal import Decimal

from adaptive_trader.strategy.trend_following import (
    TrendFollowingDecisionEngine,
    TrendFollowingDirection,
    TrendFollowingParameters,
    TrendFollowingReasonCode,
)
from tests.trend_following_engine_helpers import PriceSpec, daily_series


def test_futures_short_is_an_explicit_next_day_order() -> None:
    candles = daily_series(
        {200: PriceSpec(Decimal("80"), Decimal("101"), Decimal("79"))},
        total_days=201,
    )
    engine = TrendFollowingDecisionEngine(
        TrendFollowingParameters(allow_short=True)
    )

    decision = engine.evaluate(candles)

    assert decision.direction is TrendFollowingDirection.ENTER_SHORT
    assert decision.reason_code is TrendFollowingReasonCode.ENTER_SHORT_APPROVED
    assert decision.sma is not None
    assert decision.previous_entry_low is not None
    assert decision.close < decision.sma
    assert decision.close < decision.previous_entry_low
    assert decision.initial_stop == Decimal("101")
    assert decision.execute_at == candles[-1].open_time + timedelta(days=1)


def test_same_short_breakout_is_forbidden_in_long_only_mode() -> None:
    candles = daily_series(
        {200: PriceSpec(Decimal("80"), Decimal("101"), Decimal("79"))},
        total_days=201,
    )

    decision = TrendFollowingDecisionEngine().evaluate(candles)

    assert decision.direction is TrendFollowingDirection.HOLD
    assert decision.reason_code is TrendFollowingReasonCode.SHORT_NOT_ALLOWED
    assert decision.execute_at is None
