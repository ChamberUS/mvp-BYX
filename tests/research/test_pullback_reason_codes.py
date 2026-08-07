from adaptive_trader.strategy.pullback import PullbackReasonCode


def test_all_required_pullback_reason_codes_are_stable() -> None:
    required = {
        "TREND_NOT_ESTABLISHED",
        "TREND_PERSISTENCE_TOO_SHORT",
        "NO_PULLBACK",
        "PULLBACK_TOO_SHALLOW",
        "PULLBACK_TOO_DEEP",
        "PULLBACK_TOO_OLD",
        "PRICE_CROSSED_LONG_EMA",
        "RESUMPTION_NOT_CONFIRMED",
        "PRICE_OVEREXTENDED",
        "VOLUME_REJECTED",
        "VOLATILITY_REJECTED",
        "ENTER_LONG_APPROVED",
        "ENTER_SHORT_APPROVED",
    }

    assert required <= {item.value for item in PullbackReasonCode}
