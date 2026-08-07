"""Immutable domain models for deterministic intraday execution research."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.microstructure.models import DepthLevel, MakerPreference

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _decimal(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"{name} must be a finite Decimal")
    if positive and value <= ZERO:
        raise ValueError(f"{name} must be positive")


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class PositionEffect(StrEnum):
    OPEN_LONG = "OPEN_LONG"
    CLOSE_LONG = "CLOSE_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE_SHORT = "CLOSE_SHORT"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    MARKETABLE_LIMIT = "MARKETABLE_LIMIT"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    IN_TRANSIT = "IN_TRANSIT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class LiquidityRole(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"


class RemainderPolicy(StrEnum):
    PARTIAL_FILL = "PARTIAL_FILL"
    REJECT_REMAINDER = "REJECT_REMAINDER"


class QueueCancellationPolicy(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    PRO_RATA_DIAGNOSTIC = "PRO_RATA_DIAGNOSTIC"


class ExecutionPolicy(StrEnum):
    MAKER_FIRST_V0 = "MAKER_FIRST_V0"
    TAKER_ONLY = "TAKER_ONLY"


class ExecutionEventType(StrEnum):
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_SENT = "ORDER_SENT"
    ORDER_ACK = "ORDER_ACK"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_WORKING = "ORDER_WORKING"
    ORDER_PARTIAL_FILL = "ORDER_PARTIAL_FILL"
    ORDER_FILL = "ORDER_FILL"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_ACK = "CANCEL_ACK"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_REDUCED = "POSITION_REDUCED"
    POSITION_CLOSED = "POSITION_CLOSED"
    RISK_REJECTED = "RISK_REJECTED"
    KILL_SWITCH = "KILL_SWITCH"


TERMINAL_STATUSES = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED}
)


@dataclass(frozen=True, slots=True)
class BookState:
    timestamp: datetime
    market: MarketType
    symbol: str
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    synchronized: bool = True
    sequence: int = 0

    def __post_init__(self) -> None:
        _aware(self.timestamp, "timestamp")
        if not self.symbol or self.symbol != self.symbol.upper():
            raise ValueError("symbol must be uppercase")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if any(
            left.price <= right.price
            for left, right in zip(self.bids, self.bids[1:], strict=False)
        ):
            raise ValueError("bids must be strictly descending")
        if any(
            left.price >= right.price
            for left, right in zip(self.asks, self.asks[1:], strict=False)
        ):
            raise ValueError("asks must be strictly ascending")
        if self.bids and self.asks and self.bids[0].price >= self.asks[0].price:
            raise ValueError("order book must not be crossed")

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    fill_id: str
    order_id: str
    timestamp: datetime
    price: Decimal
    quantity: Decimal
    liquidity_role: LiquidityRole
    fee: Decimal
    fee_asset: str
    book_before: tuple[DepthLevel, ...]
    latency_ms: Decimal
    sequence: int

    def __post_init__(self) -> None:
        _aware(self.timestamp, "timestamp")
        _decimal(self.price, "price", positive=True)
        _decimal(self.quantity, "quantity", positive=True)
        _decimal(self.fee, "fee")
        _decimal(self.latency_ms, "latency_ms")
        if self.fee < ZERO or self.latency_ms < ZERO:
            raise ValueError("fee and latency must be non-negative")
        if not self.fill_id or not self.order_id or not self.fee_asset:
            raise ValueError("fill identifiers and fee asset are required")
        if self.sequence < 0:
            raise ValueError("fill sequence must be non-negative")


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    order_id: str
    client_intent_id: str
    market: MarketType
    symbol: str
    side: OrderSide
    position_effect: PositionEffect
    order_type: OrderType
    quantity: Decimal
    remaining_quantity: Decimal
    limit_price: Decimal | None
    creation_time: datetime
    exchange_arrival_time: datetime
    status: OrderStatus
    maker_preference: MakerPreference
    maximum_slippage_bps: Decimal
    expiry_time: datetime
    fills: tuple[SimulatedFill, ...] = ()
    reject_reason: str | None = None
    cancel_effective_time: datetime | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.creation_time, "creation_time"),
            (self.exchange_arrival_time, "exchange_arrival_time"),
            (self.expiry_time, "expiry_time"),
        ):
            _aware(value, name)
        _decimal(self.quantity, "quantity", positive=True)
        _decimal(self.remaining_quantity, "remaining_quantity")
        _decimal(self.maximum_slippage_bps, "maximum_slippage_bps")
        if self.limit_price is not None:
            _decimal(self.limit_price, "limit_price", positive=True)
        if not self.order_id or not self.client_intent_id or not self.symbol:
            raise ValueError("order identifiers and symbol are required")
        if self.symbol != self.symbol.upper():
            raise ValueError("symbol must be uppercase")
        if self.market is MarketType.SPOT and self.position_effect is PositionEffect.OPEN_SHORT:
            raise ValueError("Spot cannot open a short position")
        if self.remaining_quantity < ZERO or self.remaining_quantity > self.quantity:
            raise ValueError("remaining quantity is inconsistent")
        if self.maximum_slippage_bps < ZERO:
            raise ValueError("maximum slippage must be non-negative")
        if self.exchange_arrival_time < self.creation_time or self.expiry_time < self.creation_time:
            raise ValueError("order timestamps are inconsistent")
        if self.cancel_effective_time is not None:
            _aware(self.cancel_effective_time, "cancel_effective_time")
        fill_quantity = sum((fill.quantity for fill in self.fills), ZERO)
        if fill_quantity + self.remaining_quantity != self.quantity:
            raise ValueError("fills and remaining quantity do not reconcile")
        if len({fill.fill_id for fill in self.fills}) != len(self.fills):
            raise ValueError("duplicate fill identifier")
        if self.status is OrderStatus.FILLED and self.remaining_quantity != ZERO:
            raise ValueError("filled order must have zero remaining quantity")
        if self.status is OrderStatus.REJECTED and not self.reject_reason:
            raise ValueError("rejected order requires a reason")

    @property
    def filled_quantity(self) -> Decimal:
        return self.quantity - self.remaining_quantity

    @property
    def vwap(self) -> Decimal | None:
        if not self.fills:
            return None
        return sum((fill.price * fill.quantity for fill in self.fills), ZERO) / self.filled_quantity

    @property
    def total_fee(self) -> Decimal:
        return sum((fill.fee for fill in self.fills), ZERO)

    def transition(
        self,
        status: OrderStatus,
        *,
        reject_reason: str | None = None,
        cancel_effective_time: datetime | None = None,
    ) -> SimulatedOrder:
        if self.status in TERMINAL_STATUSES:
            raise ValueError("terminal order state is immutable")
        allowed = {
            OrderStatus.CREATED: {OrderStatus.IN_TRANSIT, OrderStatus.REJECTED},
            OrderStatus.IN_TRANSIT: {OrderStatus.ACKNOWLEDGED, OrderStatus.REJECTED},
            OrderStatus.ACKNOWLEDGED: {
                OrderStatus.WORKING,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.REJECTED,
            },
            OrderStatus.WORKING: {
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.CANCEL_PENDING,
            },
            OrderStatus.PARTIALLY_FILLED: {
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.CANCEL_PENDING,
            },
            OrderStatus.CANCEL_PENDING: {
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.CANCELED,
                OrderStatus.EXPIRED,
            },
        }
        if status not in allowed.get(self.status, set()):
            raise ValueError(f"invalid order transition {self.status} -> {status}")
        return replace(
            self,
            status=status,
            reject_reason=reject_reason if reject_reason is not None else self.reject_reason,
            cancel_effective_time=(
                cancel_effective_time
                if cancel_effective_time is not None
                else self.cancel_effective_time
            ),
        )

    def with_fill(self, fill: SimulatedFill) -> SimulatedOrder:
        if self.status in TERMINAL_STATUSES:
            raise ValueError("terminal order state is immutable")
        if fill.order_id != self.order_id or fill.quantity > self.remaining_quantity:
            raise ValueError("fill does not belong to order or exceeds remaining quantity")
        remaining = self.remaining_quantity - fill.quantity
        status = OrderStatus.FILLED if remaining == ZERO else OrderStatus.PARTIALLY_FILLED
        return replace(self, fills=(*self.fills, fill), remaining_quantity=remaining, status=status)


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    event_id: str
    event_type: ExecutionEventType
    timestamp: datetime
    order_id: str | None
    reason_code: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None

    def __post_init__(self) -> None:
        _aware(self.timestamp, "timestamp")
        if not self.event_id:
            raise ValueError("event_id is required")
        for value, name in ((self.quantity, "quantity"), (self.price, "price")):
            if value is not None:
                _decimal(value, name)


@dataclass(frozen=True, slots=True)
class SlippageBreakdown:
    spread_crossing_bps: Decimal
    depth_slippage_bps: Decimal
    latency_slippage_bps: Decimal
    residual_slippage_bps: Decimal = ZERO

    @property
    def total_execution_slippage_bps(self) -> Decimal:
        return (
            self.spread_crossing_bps
            + self.depth_slippage_bps
            + self.latency_slippage_bps
            + self.residual_slippage_bps
        )


@dataclass(frozen=True, slots=True)
class QueueState:
    order_id: str
    price: Decimal
    queue_ahead_quantity: Decimal
    own_quantity: Decimal
    initial_queue_ahead: Decimal
    traded_through_quantity: Decimal = ZERO
    queue_model: str = "CONSERVATIVE_FIFO_APPROXIMATION"

    @property
    def queue_progress(self) -> Decimal:
        if self.initial_queue_ahead == ZERO:
            return Decimal("1")
        return min(
            Decimal("1"),
            (self.initial_queue_ahead - self.queue_ahead_quantity) / self.initial_queue_ahead,
        )

    @property
    def fill_confidence(self) -> str:
        if self.queue_ahead_quantity > ZERO:
            return "LOW"
        return "MEDIUM_APPROXIMATION"


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    market: MarketType
    symbol: str
    side: PositionSide | None
    quantity: Decimal
    average_entry: Decimal | None
    realized_pnl: Decimal
    unrealized_mark_pnl: Decimal
    unrealized_executable_pnl: Decimal | None
    fees: Decimal
    funding: Decimal
    entry_time: datetime | None
    holding_time_ms: int
