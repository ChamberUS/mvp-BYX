from dataclasses import replace
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.research.trend_following_risk import (
    PositionSizingCap,
    PositionSizingReasonCode,
    PositionSizingRequest,
    size_position,
)


def _request(**changes: object) -> PositionSizingRequest:
    baseline = PositionSizingRequest(
        market=MarketType.SPOT,
        side=PositionSide.LONG,
        equity=Decimal("10000"),
        available_balance=Decimal("10000"),
        reference_price=Decimal("100"),
        initial_stop=Decimal("90"),
        risk_percent=Decimal("1"),
        maximum_position_percent=Decimal("100"),
    )
    return replace(baseline, **changes)


def test_structural_stop_sizes_one_percent_and_half_percent() -> None:
    normal = size_position(_request())
    defensive = size_position(_request(risk_percent=Decimal("0.5")))

    assert normal.approved is True
    assert normal.quantity == Decimal("10.00000000")
    assert normal.risk_budget == Decimal("100")
    assert defensive.quantity == Decimal("5.00000000")
    assert defensive.risk_budget == Decimal("50.0")


def test_position_size_applies_notional_cash_cost_and_precision_caps() -> None:
    notional = size_position(_request(maximum_position_percent=Decimal("5")))
    cash = size_position(
        _request(
            available_balance=Decimal("250"),
            maximum_position_percent=Decimal("100"),
        )
    )
    precise = size_position(
        _request(initial_stop=Decimal("70"), quantity_precision=2)
    )
    costly = size_position(
        _request(
            spread_bps=Decimal("50"),
            slippage_bps=Decimal("50"),
            taker_fee_bps=Decimal("100"),
        )
    )

    assert notional.quantity == Decimal("5.00000000")
    assert notional.caps_applied == (PositionSizingCap.NOTIONAL,)
    assert cash.quantity == Decimal("2.50000000")
    assert cash.caps_applied == (PositionSizingCap.CASH,)
    assert precise.quantity == Decimal("3.33")
    assert costly.estimated_entry_price == Decimal("101.00")
    assert costly.entry_fee > 0
    assert costly.required_cash <= costly.position_notional + costly.entry_fee


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {"initial_stop": Decimal("101")},
            PositionSizingReasonCode.INVALID_INITIAL_STOP,
        ),
        (
            {"initial_stop": Decimal("0")},
            PositionSizingReasonCode.INVALID_INITIAL_STOP,
        ),
        (
            {"initial_stop": Decimal("100")},
            PositionSizingReasonCode.ZERO_RISK_DISTANCE,
        ),
        (
            {
                "available_balance": Decimal("250"),
                "minimum_quantity": Decimal("3"),
            },
            PositionSizingReasonCode.CASH_INSUFFICIENT,
        ),
        (
            {
                "maximum_notional": Decimal("50"),
                "minimum_quantity": Decimal("1"),
            },
            PositionSizingReasonCode.NOTIONAL_LIMIT,
        ),
    ],
)
def test_invalid_or_unusable_sizes_are_rejected(
    changes: dict[str, object],
    reason: PositionSizingReasonCode,
) -> None:
    decision = size_position(_request(**changes))

    assert decision.approved is False
    assert decision.reason_code is reason
    assert decision.quantity == 0


def test_futures_margin_cap_and_one_x_guard() -> None:
    rejected = size_position(
        _request(
            market=MarketType.USD_M_FUTURES,
            available_balance=Decimal("50"),
            minimum_quantity=Decimal("1"),
        )
    )

    assert rejected.reason_code is PositionSizingReasonCode.MARGIN_INSUFFICIENT
    with pytest.raises(ValueError, match="1x"):
        _request(
            market=MarketType.USD_M_FUTURES,
            leverage=Decimal("2"),
        )
