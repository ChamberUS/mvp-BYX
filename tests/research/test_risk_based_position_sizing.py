from dataclasses import replace
from decimal import Decimal

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.research.trend_following_risk import (
    PositionSizingReasonCode,
    PositionSizingRequest,
    size_position,
)


def _spot_request() -> PositionSizingRequest:
    return PositionSizingRequest(
        market=MarketType.SPOT,
        side=PositionSide.LONG,
        equity=Decimal("10000"),
        available_balance=Decimal("10000"),
        reference_price=Decimal("100"),
        initial_stop=Decimal("90"),
        risk_percent=Decimal("1"),
        maximum_position_percent=Decimal("100"),
    )


def test_spot_size_is_risk_budget_divided_by_structural_distance() -> None:
    normal = size_position(_spot_request())
    defensive = size_position(
        replace(_spot_request(), risk_percent=Decimal("0.5"))
    )

    assert normal.reason_code is PositionSizingReasonCode.POSITION_SIZE_APPROVED
    assert normal.risk_budget == Decimal("100")
    assert normal.risk_per_unit == Decimal("10")
    assert normal.quantity == Decimal("10.00000000")
    assert defensive.risk_budget == Decimal("50.0")
    assert defensive.quantity == Decimal("5.00000000")


def test_spot_size_rejects_stop_on_wrong_side() -> None:
    result = size_position(
        replace(_spot_request(), initial_stop=Decimal("110"))
    )

    assert result.approved is False
    assert result.reason_code is PositionSizingReasonCode.INVALID_INITIAL_STOP
    assert result.quantity == 0
