from decimal import Decimal

import pytest

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.research.trend_following_risk import (
    PositionSizingCap,
    PositionSizingReasonCode,
    PositionSizingRequest,
    size_position,
)


def _futures_request(
    *,
    available_balance: Decimal = Decimal("10000"),
    leverage: Decimal = Decimal("1"),
) -> PositionSizingRequest:
    return PositionSizingRequest(
        market=MarketType.USD_M_FUTURES,
        side=PositionSide.SHORT,
        equity=Decimal("10000"),
        available_balance=available_balance,
        reference_price=Decimal("100"),
        initial_stop=Decimal("110"),
        risk_percent=Decimal("1"),
        maximum_position_percent=Decimal("100"),
        taker_fee_bps=Decimal("4"),
        margin_buffer_percent=Decimal("1"),
        leverage=leverage,
        quantity_precision=4,
    )


def test_futures_short_sizing_accounts_for_margin_fee_and_precision() -> None:
    result = size_position(_futures_request())

    assert result.reason_code is PositionSizingReasonCode.POSITION_SIZE_APPROVED
    assert result.quantity == Decimal("10.0000")
    assert result.required_margin == Decimal("1000.0000")
    assert result.entry_fee == Decimal("0.40000000")
    assert result.required_cash == Decimal("1010.40000000")


def test_futures_margin_is_a_hard_cap_and_leverage_is_locked_to_one_x() -> None:
    capped = size_position(
        _futures_request(available_balance=Decimal("500"))
    )

    assert capped.approved is True
    assert PositionSizingCap.MARGIN in capped.caps_applied
    assert capped.required_cash <= Decimal("500")
    with pytest.raises(ValueError, match="1x"):
        _futures_request(leverage=Decimal("2"))
