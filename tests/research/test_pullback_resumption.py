from decimal import Decimal

from adaptive_trader.strategy.pullback import (
    PullbackContinuationCore,
    PullbackReasonCode,
)
from tests.research.pullback_helpers import (
    evaluate_long,
    parameters,
    seed_long_trend,
)


def test_resumption_requires_close_above_short_ema_and_previous_close() -> None:
    core = PullbackContinuationCore(
        parameters(maximum_atr_relative=Decimal("0.2"))
    )
    index = seed_long_trend(core)
    evaluate_long(core, index, "104", "106")

    not_resumed = evaluate_long(core, index + 1, "104.5", "104")
    resumed = evaluate_long(core, index + 2, "106", "104.5")

    assert not_resumed.trace.reason_code is PullbackReasonCode.RESUMPTION_NOT_CONFIRMED
    assert resumed.trace.resumed is True
    assert resumed.trace.long_eligible is True
    assert resumed.trace.reason_code is PullbackReasonCode.ENTER_LONG_APPROVED


def test_resumption_rejects_excessive_long_ema_extension() -> None:
    core = PullbackContinuationCore(
        parameters(maximum_entry_extension_atr=Decimal("1"))
    )
    index = seed_long_trend(
        core,
        short_ema="105",
        long_ema="100",
        close="106",
        atr_value="2",
    )
    evaluate_long(
        core,
        index,
        "104",
        "106",
        atr_value="2",
    )
    overextended = evaluate_long(
        core,
        index + 1,
        "106",
        "104",
        atr_value="2",
    )

    assert overextended.trace.overextended is True
    assert overextended.trace.reason_code is PullbackReasonCode.PRICE_OVEREXTENDED
