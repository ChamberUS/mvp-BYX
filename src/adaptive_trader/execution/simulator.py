"""Paper executor that accepts approved intents and never reaches an exchange."""

from adaptive_trader.domain.models import OrderIntent, OrderStatus, SimulatedOrder


class SimulatedOrderExecutor:
    def execute(self, intent: OrderIntent) -> SimulatedOrder:
        return SimulatedOrder(
            order_id=f"{intent.intent_id}-ORDER",
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            direction=intent.direction,
            quantity=intent.quantity,
            price=intent.price,
            status=OrderStatus.FILLED,
            created_at=intent.created_at,
        )
