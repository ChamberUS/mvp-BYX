from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import MarketType
from adaptive_trader.execution import (
    BookState,
    LatencyConfig,
    LatencyModel,
    LatencyProfile,
    LiquidityRole,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionEffect,
    SimulatedFill,
    SimulatedOrder,
)
from adaptive_trader.microstructure.models import MakerPreference
from tests.execution.helpers import BASE, at, book


def order(**changes: object) -> SimulatedOrder:
    base = SimulatedOrder(
        order_id="order-1",
        client_intent_id="intent-1",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2"),
        remaining_quantity=Decimal("2"),
        limit_price=Decimal("100"),
        creation_time=BASE,
        exchange_arrival_time=at(10),
        status=OrderStatus.CREATED,
        maker_preference=MakerPreference.MAKER,
        maximum_slippage_bps=Decimal("10"),
        expiry_time=at(1000),
    )
    return replace(base, **changes)


def fill(identifier: str = "fill-1", quantity: str = "1") -> SimulatedFill:
    return SimulatedFill(
        fill_id=identifier,
        order_id="order-1",
        timestamp=at(30),
        price=Decimal("100"),
        quantity=Decimal(quantity),
        liquidity_role=LiquidityRole.MAKER,
        fee=Decimal("0.01"),
        fee_asset="USDT",
        book_before=book().bids,
        latency_ms=Decimal("3"),
        sequence=1,
    )


def test_order_lifecycle_partial_fill_and_terminal_immutability() -> None:
    current = order().transition(OrderStatus.IN_TRANSIT)
    current = current.transition(OrderStatus.ACKNOWLEDGED)
    current = current.transition(OrderStatus.WORKING)
    current = current.with_fill(fill())
    assert current.status is OrderStatus.PARTIALLY_FILLED
    assert current.remaining_quantity == Decimal("1")
    assert current.vwap == Decimal("100")
    current = current.with_fill(fill("fill-2"))
    assert current.status is OrderStatus.FILLED
    assert current.total_fee == Decimal("0.02")
    with pytest.raises(ValueError, match="immutable"):
        current.transition(OrderStatus.CANCELED)
    with pytest.raises(ValueError, match="immutable"):
        current.with_fill(fill("fill-3"))


def test_order_model_rejects_spot_short_duplicates_and_bad_reconciliation() -> None:
    with pytest.raises(ValueError, match="Spot"):
        order(position_effect=PositionEffect.OPEN_SHORT)
    with pytest.raises(ValueError, match="reconcile"):
        order(remaining_quantity=Decimal("1"))
    with pytest.raises(ValueError, match="duplicate"):
        order(remaining_quantity=Decimal("0"), fills=(fill(), fill()))
    rejected = order().transition(OrderStatus.REJECTED, reject_reason="RISK_REJECTED")
    assert rejected.reject_reason == "RISK_REJECTED"


def test_order_transition_graph_rejects_skips() -> None:
    with pytest.raises(ValueError, match="invalid order transition"):
        order().transition(OrderStatus.FILLED)
    with pytest.raises(ValueError, match="requires a reason"):
        order(status=OrderStatus.REJECTED)


@pytest.mark.parametrize(
    ("profile", "arrival_ms"),
    [
        (LatencyProfile.IDEALIZED, 0),
        (LatencyProfile.FAST, 5),
        (LatencyProfile.NORMAL, 20),
        (LatencyProfile.STRESSED, 105),
    ],
)
def test_explicit_latency_profiles(profile: LatencyProfile, arrival_ms: int) -> None:
    model = LatencyModel(profile, seed=7)
    assert model.exchange_arrival(BASE) == at(arrival_ms)
    assert model.fill_notification_time(BASE) >= BASE
    assert model.acknowledgement_time(BASE) >= BASE
    assert model.cancel_effective_time(BASE) >= BASE


def test_custom_latency_validation_and_determinism() -> None:
    custom = LatencyConfig(LatencyProfile.NORMAL, 1, 2, 3, 4, 5, 6, 7)
    first = LatencyModel(config=custom, seed=9)
    second = LatencyModel(config=custom, seed=9)
    assert first.exchange_arrival(BASE) == second.exchange_arrival(BASE) == at(3)
    assert first.acknowledgement_time(BASE) == BASE + timedelta(milliseconds=7)
    assert first.cancel_effective_time(BASE) == BASE + timedelta(milliseconds=11)
    with pytest.raises(ValueError, match="non-negative"):
        LatencyModel(seed=-1)
    with pytest.raises(ValueError, match="non-negative"):
        LatencyConfig(LatencyProfile.NORMAL, -1, 0, 0, 0, 0, 0, 0)


def test_book_rejects_crossed_unsorted_and_naive_time() -> None:
    with pytest.raises(ValueError, match="crossed"):
        book(bids=(("101", "1"),), asks=(("100", "1"),))
    with pytest.raises(ValueError, match="descending"):
        book(bids=(("99", "1"), ("100", "1")))
    with pytest.raises(ValueError, match="timezone-aware"):
        BookState(
            timestamp=BASE.replace(tzinfo=None),
            market=MarketType.SPOT,
            symbol="ETHUSDT",
            bids=(),
            asks=(),
        )
