"""Conservative public-data queue approximation for passive orders."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from adaptive_trader.execution.models import QueueCancellationPolicy, QueueState

ZERO = Decimal("0")


class QueueModel:
    name = "CONSERVATIVE_FIFO_APPROXIMATION"

    def __init__(
        self,
        cancellation_policy: QueueCancellationPolicy = QueueCancellationPolicy.CONSERVATIVE,
    ) -> None:
        self.cancellation_policy = cancellation_policy

    def join(
        self,
        order_id: str,
        price: Decimal,
        visible_ahead: Decimal,
        own_quantity: Decimal,
    ) -> QueueState:
        if price <= ZERO or visible_ahead < ZERO or own_quantity <= ZERO:
            raise ValueError("queue inputs are invalid")
        return QueueState(
            order_id=order_id,
            price=price,
            queue_ahead_quantity=visible_ahead,
            own_quantity=own_quantity,
            initial_queue_ahead=visible_ahead,
        )
    def aggressive_trade(
        self,
        state: QueueState,
        traded_quantity: Decimal,
    ) -> tuple[QueueState, Decimal]:
        if traded_quantity < ZERO:
            raise ValueError("traded quantity must be non-negative")
        ahead_consumed = min(state.queue_ahead_quantity, traded_quantity)
        after_ahead = traded_quantity - ahead_consumed
        own_fill = min(state.own_quantity, after_ahead)
        return (
            replace(
                state,
                queue_ahead_quantity=state.queue_ahead_quantity - ahead_consumed,
                own_quantity=state.own_quantity - own_fill,
                traded_through_quantity=state.traded_through_quantity + traded_quantity,
            ),
            own_fill,
        )

    def depth_reduction(self, state: QueueState, reduced_quantity: Decimal) -> QueueState:
        if reduced_quantity < ZERO:
            raise ValueError("depth reduction must be non-negative")
        if self.cancellation_policy is QueueCancellationPolicy.CONSERVATIVE:
            return state
        total = state.queue_ahead_quantity + state.own_quantity
        if total == ZERO:
            return state
        proportional_ahead = reduced_quantity * state.queue_ahead_quantity / total
        return replace(
            state,
            queue_ahead_quantity=max(ZERO, state.queue_ahead_quantity - proportional_ahead),
        )
