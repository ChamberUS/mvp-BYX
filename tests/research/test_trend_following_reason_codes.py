from adaptive_trader.strategy.trend_following import TrendFollowingReasonCode


def test_reason_code_contract_is_explicit_and_stable() -> None:
    expected = {
        "WARMUP_INCOMPLETE",
        "MACRO_FILTER_LONG_REJECTED",
        "DONCHIAN_LONG_BREAKOUT_NOT_CONFIRMED",
        "POSITION_ALREADY_OPEN",
        "RISK_REJECTED",
        "ENTER_LONG_APPROVED",
        "MACRO_FILTER_SHORT_REJECTED",
        "DONCHIAN_SHORT_BREAKOUT_NOT_CONFIRMED",
        "SHORT_NOT_ALLOWED",
        "ENTER_SHORT_APPROVED",
        "MACRO_FILTER_EXIT",
        "DONCHIAN_EXIT_10",
        "DONCHIAN_EXIT_20",
        "FORCED_END",
        "LIQUIDATION",
        "ADMINISTRATIVE_EXIT",
    }

    assert {reason.value for reason in TrendFollowingReasonCode} == expected
    assert len(TrendFollowingReasonCode) == len(expected)
