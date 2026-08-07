from __future__ import annotations

from decimal import Decimal

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.execution import (
    ExecutionConfig,
    ExecutionEventType,
    ExecutionPolicy,
    ExecutionSimulator,
    LatencyProfile,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionEffect,
    QueueCancellationPolicy,
    QueueModel,
    RemainderPolicy,
)
from adaptive_trader.microstructure.models import (
    AggressiveSide,
    IntradayOrderIntent,
    MakerPreference,
    OrderUrgency,
)
from tests.execution.helpers import BASE, at, book


def submit_market(
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: str = "3",
    maximum_slippage_bps: str = "30",
) -> tuple[ExecutionSimulator, object]:
    venue = ExecutionSimulator(ExecutionConfig(latency_profile=LatencyProfile.NORMAL))
    result = venue.submit(
        client_intent_id="market",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=side,
        position_effect=(
            PositionEffect.OPEN_LONG if side is OrderSide.BUY else PositionEffect.CLOSE_LONG
        ),
        order_type=OrderType.MARKET,
        quantity=Decimal(quantity),
        decision_time=BASE,
        books=(book(10), book(30)),
        reference_price=Decimal("100.05"),
        maximum_slippage_bps=Decimal(maximum_slippage_bps),
        maker_preference=MakerPreference.TAKER,
    )
    return venue, result


def test_market_buy_walks_asks_with_multilevel_vwap_and_fees() -> None:
    venue, raw = submit_market()
    result = raw
    assert result.order.status is OrderStatus.FILLED
    assert [fill.price for fill in result.order.fills] == [Decimal("100.10"), Decimal("100.20")]
    assert result.order.vwap == Decimal("100.1666666666666666666666667")
    assert result.levels_consumed == 2
    assert result.worst_fill_price == Decimal("100.20")
    assert result.visible_depth_consumed == Decimal("3")
    assert result.slippage is not None
    assert result.slippage.depth_slippage_bps > 0
    assert len(venue.execution_ledger.fills) == 2
    assert all(fill.fee > 0 for fill in result.order.fills)


def test_market_sell_consumes_bids_and_never_invents_liquidity() -> None:
    venue = ExecutionSimulator()
    opened = venue.submit(
        client_intent_id="open",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.MARKET,
        quantity=Decimal("3"),
        decision_time=BASE,
        books=(book(),),
        reference_price=Decimal("100.10"),
        maximum_slippage_bps=Decimal("30"),
    )
    assert opened.order.filled_quantity == Decimal("3")
    result = venue.submit(
        client_intent_id="sell",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.SELL,
        position_effect=PositionEffect.CLOSE_LONG,
        order_type=OrderType.MARKET,
        quantity=Decimal("3"),
        decision_time=BASE,
        books=(book(),),
        reference_price=Decimal("100"),
        maximum_slippage_bps=Decimal("30"),
    )
    assert result.order.filled_quantity == Decimal("3")
    assert result.best_price_before == Decimal("100.00")
    assert result.order.fills[-1].price == Decimal("99.90")


def test_insufficient_depth_is_partial_by_default_and_slippage_caps_levels() -> None:
    _, partial = submit_market(quantity="10")
    assert partial.order.status is OrderStatus.PARTIALLY_FILLED
    assert partial.order.filled_quantity == Decimal("3")
    assert partial.order.remaining_quantity == Decimal("7")
    _, capped = submit_market(quantity="3", maximum_slippage_bps="6")
    assert capped.order.filled_quantity == Decimal("1")
    assert capped.order.remaining_quantity == Decimal("2")


def test_market_with_no_allowed_liquidity_rejects_and_unsynchronized_book_rejects() -> None:
    _, rejected = submit_market(quantity="1", maximum_slippage_bps="0")
    assert rejected.order.status is OrderStatus.REJECTED
    assert rejected.order.reject_reason == "NO_EXECUTABLE_LIQUIDITY_WITHIN_SLIPPAGE"
    venue = ExecutionSimulator()
    result = venue.submit(
        client_intent_id="bad-book",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        decision_time=BASE,
        books=(book(30, synchronized=False),),
        reference_price=Decimal("100"),
    )
    assert result.order.status is OrderStatus.REJECTED


def test_arrival_cannot_see_pre_arrival_book() -> None:
    venue = ExecutionSimulator()
    result = venue.submit(
        client_intent_id="look-ahead-guard",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        decision_time=BASE,
        books=(book(10),),
        reference_price=Decimal("100"),
    )
    assert result.order.status is OrderStatus.REJECTED
    assert result.order.filled_quantity == 0


def test_latency_slippage_is_separate_from_spread_and_depth() -> None:
    venue = ExecutionSimulator()
    decision_book = book(0, asks=(("100.05", "2"), ("100.15", "2")))
    arrival_book = book(30)
    result = venue.submit(
        client_intent_id="latency-slippage",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.MARKET,
        quantity=Decimal("2"),
        decision_time=BASE,
        books=(decision_book, arrival_book),
        reference_price=Decimal("100.05"),
        maximum_slippage_bps=Decimal("30"),
    )
    assert result.slippage is not None
    assert result.slippage.spread_crossing_bps == 0
    assert result.slippage.latency_slippage_bps > 0
    assert result.slippage.depth_slippage_bps > 0


def test_maker_first_waits_cancels_and_uses_fixed_taker_fallback() -> None:
    venue = ExecutionSimulator(
        ExecutionConfig(
            policy=ExecutionPolicy.MAKER_FIRST_V0,
            maker_wait_ms=250,
        )
    )
    intent = IntradayOrderIntent(
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        reference_price=Decimal("100.05"),
        limit_price=Decimal("100"),
        urgency=OrderUrgency.NORMAL,
        maker_preference=MakerPreference.MAKER,
        maximum_slippage_bps=Decimal("30"),
        expiry_ms=1000,
        reason="MECHANICAL_FIXTURE",
    )
    primary, fallback = venue.execute_policy(
        intent,
        client_intent_id="maker-first",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        effect=PositionEffect.OPEN_LONG,
        decision_time=BASE,
        books=(book(30), book(300, sequence=2)),
    )
    assert primary.order.status is OrderStatus.CANCELED
    assert fallback.order.status is OrderStatus.FILLED
    assert fallback.order.order_type is OrderType.MARKETABLE_LIMIT
    assert all(fill.liquidity_role.value == "TAKER" for fill in fallback.order.fills)


def test_taker_only_planner_builds_protected_limit_without_user_limit() -> None:
    venue = ExecutionSimulator(ExecutionConfig(policy=ExecutionPolicy.TAKER_ONLY))
    intent = IntradayOrderIntent(
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        reference_price=Decimal("100.05"),
        limit_price=None,
        urgency=OrderUrgency.URGENT,
        maker_preference=MakerPreference.TAKER,
        maximum_slippage_bps=Decimal("30"),
        expiry_ms=1000,
        reason="TAKER_BASELINE",
    )
    (result,) = venue.execute_policy(
        intent,
        client_intent_id="taker-only",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        effect=PositionEffect.OPEN_LONG,
        decision_time=BASE,
        books=(book(),),
    )
    assert result.order.status is OrderStatus.FILLED
    assert result.order.limit_price is not None


def test_marketable_limit_protects_price_and_remainder_works_as_maker() -> None:
    venue = ExecutionSimulator()
    result = venue.submit(
        client_intent_id="marketable-limit",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.MARKETABLE_LIMIT,
        quantity=Decimal("3"),
        decision_time=BASE,
        books=(book(),),
        reference_price=Decimal("100.05"),
        limit_price=Decimal("100.10"),
        maximum_slippage_bps=Decimal("30"),
    )
    assert result.order.status is OrderStatus.WORKING
    assert result.order.filled_quantity == Decimal("1")
    assert result.queue_state is not None
    assert all(fill.price <= Decimal("100.10") for fill in result.order.fills)


def test_passive_limit_does_not_fill_on_touch_and_queue_progresses_after_trade() -> None:
    venue = ExecutionSimulator()
    result = venue.submit(
        client_intent_id="passive",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2"),
        decision_time=BASE,
        books=(book(),),
        reference_price=Decimal("100.05"),
        limit_price=Decimal("100.00"),
    )
    assert result.order.status is OrderStatus.WORKING
    assert result.order.fills == ()
    unchanged = venue.process_aggressive_trade(
        result.order.order_id,
        timestamp=at(40),
        price=Decimal("100"),
        quantity=Decimal("2"),
        aggressive_side=AggressiveSide.SELL,
        book_before=book(),
    )
    assert unchanged.fills == ()
    partial = venue.process_aggressive_trade(
        result.order.order_id,
        timestamp=at(45),
        price=Decimal("100"),
        quantity=Decimal("1"),
        aggressive_side=AggressiveSide.SELL,
        book_before=book(),
    )
    assert partial.filled_quantity == Decimal("1")
    assert partial.fills[0].liquidity_role.value == "MAKER"


def test_queue_cancellation_policies_are_explicit_approximations() -> None:
    conservative = QueueModel()
    state = conservative.join("o", Decimal("100"), Decimal("8"), Decimal("2"))
    assert conservative.depth_reduction(state, Decimal("5")) == state
    diagnostic = QueueModel(QueueCancellationPolicy.PRO_RATA_DIAGNOSTIC)
    improved = diagnostic.depth_reduction(state, Decimal("5"))
    assert improved.queue_ahead_quantity == Decimal("4")
    assert improved.fill_confidence == "LOW"
    progressed, own_fill = diagnostic.aggressive_trade(improved, Decimal("5"))
    assert own_fill == Decimal("1")
    assert progressed.queue_progress == Decimal("1")
    assert progressed.fill_confidence == "MEDIUM_APPROXIMATION"


def test_cancel_fill_race_partial_then_cancel_and_full_fill_wins() -> None:
    venue = ExecutionSimulator()
    result = venue.submit(
        client_intent_id="cancel-race",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2"),
        decision_time=BASE,
        books=(book(),),
        reference_price=Decimal("100.05"),
        limit_price=Decimal("100"),
    )
    pending = venue.request_cancel(result.order.order_id, at(40))
    assert pending.status is OrderStatus.CANCEL_PENDING
    partial = venue.process_aggressive_trade(
        pending.order_id,
        timestamp=at(45),
        price=Decimal("100"),
        quantity=Decimal("3"),
        aggressive_side=AggressiveSide.SELL,
        book_before=book(),
    )
    assert partial.status is OrderStatus.CANCEL_PENDING
    canceled = venue.advance(at(60))[0]
    assert canceled.status is OrderStatus.CANCELED
    event_types = [event.event_type for event in venue.events]
    assert event_types.count(ExecutionEventType.CANCEL_ACK) == 1

    second = ExecutionSimulator()
    active = second.submit(
        client_intent_id="full-race",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        decision_time=BASE,
        books=(book(),),
        reference_price=Decimal("100"),
        limit_price=Decimal("100"),
    ).order
    second.request_cancel(active.order_id, at(40))
    filled = second.process_aggressive_trade(
        active.order_id,
        timestamp=at(45),
        price=Decimal("100"),
        quantity=Decimal("3"),
        aggressive_side=AggressiveSide.SELL,
        book_before=book(),
    )
    assert filled.status is OrderStatus.FILLED
    assert second.advance(at(60)) == ()
    assert all(event.event_type is not ExecutionEventType.CANCEL_ACK for event in second.events)


def test_expiry_requests_delayed_cancel_and_remainder_reject_policy() -> None:
    venue = ExecutionSimulator()
    venue.submit(
        client_intent_id="expiry",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        decision_time=BASE,
        books=(book(),),
        reference_price=Decimal("100"),
        limit_price=Decimal("100"),
        expiry_ms=40,
    )
    assert venue.advance(at(40)) == ()
    assert venue.orders[0].status is OrderStatus.CANCEL_PENDING
    assert venue.advance(at(60))[0].status is OrderStatus.EXPIRED

    reject_remainder = ExecutionSimulator(
        ExecutionConfig(remainder_policy=RemainderPolicy.REJECT_REMAINDER)
    )
    partial = reject_remainder.submit(
        client_intent_id="reject-rest",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        decision_time=BASE,
        books=(book(),),
        reference_price=Decimal("100.10"),
        maximum_slippage_bps=Decimal("30"),
    )
    assert partial.order.status is OrderStatus.CANCEL_PENDING
