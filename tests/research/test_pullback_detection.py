from decimal import Decimal

from adaptive_trader.domain.models import MarketRegime
from adaptive_trader.strategy.pullback import (
    PullbackContinuationCore,
    PullbackReasonCode,
)
from tests.research.pullback_helpers import (
    evaluate_long,
    parameters,
    seed_long_trend,
)


def test_trend_requires_registered_persistence() -> None:
    core = PullbackContinuationCore(parameters(trend_persistence_candles=3))

    first = evaluate_long(core, 0, "106", "105")
    second = evaluate_long(core, 1, "106", "106")
    third = evaluate_long(core, 2, "106", "106")

    assert first.trace.reason_code is PullbackReasonCode.TREND_PERSISTENCE_TOO_SHORT
    assert second.trace.trend_persistence_count == 2
    assert third.trace.trend_confirmed is True
    assert third.trace.reason_code is PullbackReasonCode.NO_PULLBACK


def test_shallow_deep_expired_and_long_ema_cross_are_explicit() -> None:
    shallow_core = PullbackContinuationCore(parameters())
    index = seed_long_trend(shallow_core)
    shallow = evaluate_long(shallow_core, index, "104.5", "106")
    assert shallow.trace.reason_code is PullbackReasonCode.PULLBACK_TOO_SHALLOW

    deep_core = PullbackContinuationCore(parameters())
    index = seed_long_trend(
        deep_core,
        short_ema="130",
        long_ema="100",
        close="131",
        atr_value="10",
    )
    deep = evaluate_long(
        deep_core,
        index,
        "119",
        "131",
        short_ema="130",
        long_ema="100",
    )
    assert deep.trace.reason_code is PullbackReasonCode.PULLBACK_TOO_DEEP

    expired_core = PullbackContinuationCore(
        parameters(pullback_max_candles=2)
    )
    index = seed_long_trend(expired_core)
    evaluate_long(expired_core, index, "104", "106")
    evaluate_long(expired_core, index + 1, "104", "104")
    expired = evaluate_long(expired_core, index + 2, "104", "104")
    assert expired.trace.reason_code is PullbackReasonCode.PULLBACK_TOO_OLD

    crossed_core = PullbackContinuationCore(parameters())
    index = seed_long_trend(crossed_core)
    crossed = evaluate_long(crossed_core, index, "99", "106")
    assert crossed.trace.reason_code is PullbackReasonCode.PRICE_CROSSED_LONG_EMA


def test_pullback_decision_uses_only_current_and_previous_closed_candle() -> None:
    core = PullbackContinuationCore(parameters())
    index = seed_long_trend(core)
    decision = evaluate_long(
        core,
        index,
        "104",
        "106",
        regime=MarketRegime.TRENDING_UP,
    )

    assert decision.trace.timestamp.hour == index
    assert decision.trace.pullback_depth_atr == Decimal("0.1")
    assert decision.trace.reason_code is PullbackReasonCode.RESUMPTION_NOT_CONFIRMED
