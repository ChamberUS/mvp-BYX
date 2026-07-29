"""Conservative local execution model for chronological backtests."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from adaptive_trader.domain.models import OrderIntent, OrderStatus, SignalDirection, SimulatedOrder


class ExecutionError(RuntimeError):
    """Raised when a local simulated order cannot be filled safely."""


@dataclass(frozen=True, slots=True)
class BacktestExecutionConfig:
    maker_fee_bps: Decimal = Decimal("10")
    taker_fee_bps: Decimal = Decimal("20")
    slippage_bps: Decimal = Decimal("5")
    spread_bps: Decimal = Decimal("2")
    minimum_order_quantity: Decimal = Decimal("0")
    price_precision: int = 8
    quantity_precision: int = 8

    def __post_init__(self) -> None:
        for name in ("maker_fee_bps", "taker_fee_bps", "slippage_bps", "spread_bps"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or value < 0:
                raise ValueError(f"{name} must be a non-negative Decimal")
        if self.minimum_order_quantity < 0:
            raise ValueError("minimum_order_quantity must not be negative")
        if self.price_precision < 0 or self.quantity_precision < 0:
            raise ValueError("precisions must not be negative")


def _quantize(value: Decimal, precision: int, rounding: str) -> Decimal:
    quantum = Decimal("1").scaleb(-precision)
    return value.quantize(quantum, rounding=rounding)


class BacktestOrderExecutor:
    """Executor with no network capability and an explicitly injected reference price."""

    def __init__(self, config: BacktestExecutionConfig | None = None) -> None:
        self._config = config or BacktestExecutionConfig()
        self._reference_price: Decimal | None = None

    def set_reference_price(self, reference_price: Decimal) -> None:
        if reference_price <= 0:
            raise ValueError("reference price must be positive")
        self._reference_price = reference_price

    def estimate_buy_cost(self, intent: OrderIntent, reference_price: Decimal) -> Decimal:
        if intent.direction is not SignalDirection.BUY:
            raise ExecutionError("buy cost estimation requires a BUY intent")
        quantity = _quantize(intent.quantity, self._config.quantity_precision, ROUND_DOWN)
        if quantity <= 0 or quantity < self._config.minimum_order_quantity:
            raise ExecutionError("quantity is below the configured minimum")
        effective = self._effective_price(reference_price, SignalDirection.BUY)
        fee = effective * quantity * self._config.taker_fee_bps / Decimal("10000")
        return effective * quantity + fee

    def execute(self, intent: OrderIntent) -> SimulatedOrder:
        if self._reference_price is None:
            raise ExecutionError("reference price must be set before execution")
        quantity = _quantize(intent.quantity, self._config.quantity_precision, ROUND_DOWN)
        if quantity <= 0 or quantity < self._config.minimum_order_quantity:
            raise ExecutionError("quantity is below the configured minimum")
        reference = self._reference_price
        effective = self._effective_price(reference, intent.direction)
        spread_cost = reference * self._config.spread_bps / Decimal("10000") * quantity
        slippage_cost = reference * self._config.slippage_bps / Decimal("10000") * quantity
        fee = effective * quantity * self._config.taker_fee_bps / Decimal("10000")
        return SimulatedOrder(
            order_id=f"{intent.intent_id}-ORDER",
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            direction=intent.direction,
            quantity=quantity,
            price=effective,
            status=OrderStatus.FILLED,
            created_at=intent.created_at,
            reference_price=reference,
            fee=fee,
            slippage_cost=slippage_cost,
            spread_cost=spread_cost,
        )

    def _effective_price(self, reference: Decimal, direction: SignalDirection) -> Decimal:
        cost_bps = self._config.spread_bps + self._config.slippage_bps
        cost = reference * cost_bps / Decimal("10000")
        if direction is SignalDirection.BUY:
            effective = reference + cost
        elif direction is SignalDirection.SELL:
            effective = reference - cost
        else:
            raise ExecutionError("HOLD cannot be executed")
        return _quantize(effective, self._config.price_precision, ROUND_HALF_UP)
