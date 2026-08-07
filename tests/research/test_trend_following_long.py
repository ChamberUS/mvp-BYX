from datetime import timedelta
from decimal import Decimal

from adaptive_trader.strategy.trend_following import (
    TrendFollowingDecisionEngine,
    TrendFollowingDirection,
    TrendFollowingReasonCode,
)
from tests.trend_following_engine_helpers import PriceSpec, daily_series


def test_daily_long_requires_close_above_sma_and_prior_donchian_high() -> None:
    candles = daily_series(
        {200: PriceSpec(Decimal("120"), Decimal("121"), Decimal("99"))},
        total_days=201,
    )

    decision = TrendFollowingDecisionEngine().evaluate(candles)

    assert decision.direction is TrendFollowingDirection.ENTER_LONG
    assert decision.reason_code is TrendFollowingReasonCode.ENTER_LONG_APPROVED
    assert decision.sma is not None
    assert decision.previous_entry_high is not None
    assert decision.close > decision.sma
    assert decision.close > decision.previous_entry_high
    assert decision.initial_stop == Decimal("99")
    assert decision.execute_at == candles[-1].open_time + timedelta(days=1)


def test_intraday_high_does_not_replace_close_confirmation() -> None:
    candles = daily_series(
        {200: PriceSpec(Decimal("100"), Decimal("130"), Decimal("99"))},
        total_days=201,
    )

    decision = TrendFollowingDecisionEngine().evaluate(candles)

    assert decision.direction is TrendFollowingDirection.HOLD
    assert decision.execute_at is None
    assert decision.breakout_long is False
