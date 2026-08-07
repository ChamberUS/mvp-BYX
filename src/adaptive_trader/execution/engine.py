"""Event-time order-book execution engine with partial fills and cancel races."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.execution.fees import FeeConfig, FeeModel
from adaptive_trader.execution.latency import LatencyModel, LatencyProfile
from adaptive_trader.execution.ledger import ExecutionLedger, PositionLedger
from adaptive_trader.execution.models import (
    TERMINAL_STATUSES,
    BookState,
    ExecutionEvent,
    ExecutionEventType,
    ExecutionPolicy,
    LiquidityRole,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionEffect,
    QueueState,
    RemainderPolicy,
    SimulatedFill,
    SimulatedOrder,
    SlippageBreakdown,
)
from adaptive_trader.execution.queue import QueueModel
from adaptive_trader.microstructure.models import (
    AggressiveSide,
    IntradayOrderIntent,
    MakerPreference,
    OrderUrgency,
)

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    policy: ExecutionPolicy = ExecutionPolicy.MAKER_FIRST_V0
    latency_profile: LatencyProfile = LatencyProfile.NORMAL
    remainder_policy: RemainderPolicy = RemainderPolicy.PARTIAL_FILL
    maker_wait_ms: int = 250
    residual_slippage_bps: Decimal = ZERO
    fee_config: FeeConfig = FeeConfig()
    seed: int = 42
    research_only: bool = True
    leverage: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.maker_wait_ms < 0 or self.seed < 0:
            raise ValueError("maker wait and seed must be non-negative")
        if self.residual_slippage_bps < ZERO:
            raise ValueError("residual slippage must be non-negative")
        if self.leverage != Decimal("1"):
            raise ValueError("execution simulation is locked to leverage 1x")
        if not self.research_only:
            raise ValueError("execution simulation must remain research-only")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    order: SimulatedOrder
    slippage: SlippageBreakdown | None
    levels_consumed: int
    best_price_before: Decimal | None
    worst_fill_price: Decimal | None
    visible_depth_consumed: Decimal
    queue_state: QueueState | None


@dataclass(frozen=True, slots=True)
class TakerExecutionPreview:
    """Non-mutating depth walk for large offline label campaigns."""

    requested_quantity: Decimal
    filled_quantity: Decimal
    vwap: Decimal | None
    fee: Decimal
    spread_crossing_bps: Decimal
    depth_slippage_bps: Decimal
    levels_consumed: int
    worst_fill_price: Decimal | None


class ExecutionPlanner:
    def __init__(self, policy: ExecutionPolicy) -> None:
        self.policy = policy

    def plan(
        self,
        intent: IntradayOrderIntent,
        effect: PositionEffect,
    ) -> tuple[OrderType, Decimal | None]:
        if self.policy is ExecutionPolicy.TAKER_ONLY:
            direction = (
                Decimal("1")
                if effect
                in {
                    PositionEffect.OPEN_LONG,
                    PositionEffect.CLOSE_SHORT,
                }
                else Decimal("-1")
            )
            limit = intent.reference_price * (
                Decimal("1") + direction * intent.maximum_slippage_bps / TEN_THOUSAND
            )
            return OrderType.MARKETABLE_LIMIT, limit
        if intent.limit_price is None:
            return OrderType.LIMIT, intent.reference_price
        return OrderType.LIMIT, intent.limit_price


class SimulatedOrderBookVenue:
    """No-auth local venue that consumes only explicitly supplied public depth."""

    def __init__(self, config: ExecutionConfig | None = None) -> None:
        self.config = config or ExecutionConfig()
        self.latency = LatencyModel(self.config.latency_profile, seed=self.config.seed)
        self.fees = FeeModel(self.config.fee_config)
        self.queue = QueueModel()
        self.execution_ledger = ExecutionLedger()
        self.position_ledger = PositionLedger()
        self.events: list[ExecutionEvent] = []
        self.queue_states: dict[str, QueueState] = {}
        self._orders: dict[str, SimulatedOrder] = {}
        self._expiry_cancel: set[str] = set()
        self._counter = 0

    @property
    def orders(self) -> tuple[SimulatedOrder, ...]:
        return tuple(self._orders.values())

    @property
    def execution_hash(self) -> str:
        payload = [
            {
                "type": item.event_type.value,
                "time": item.timestamp.isoformat(),
                "order": item.order_id,
                "reason": item.reason_code,
                "quantity": str(item.quantity) if item.quantity is not None else None,
                "price": str(item.price) if item.price is not None else None,
            }
            for item in self.events
        ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def preview_taker(
        self,
        *,
        book: BookState,
        side: OrderSide,
        position_effect: PositionEffect,
        quantity: Decimal,
        reference_price: Decimal,
        maximum_slippage_bps: Decimal = Decimal("1000"),
    ) -> TakerExecutionPreview:
        """Apply the venue's taker depth/fee semantics without allocating order ledgers."""

        if quantity <= ZERO or reference_price <= ZERO:
            raise ValueError("preview quantity and reference must be positive")
        expected_side = (
            OrderSide.BUY
            if position_effect in {PositionEffect.OPEN_LONG, PositionEffect.CLOSE_SHORT}
            else OrderSide.SELL
        )
        if side is not expected_side:
            raise ValueError("preview side conflicts with position effect")
        levels = book.asks if side is OrderSide.BUY else book.bids
        remaining = quantity
        notional = ZERO
        fee = ZERO
        consumed = 0
        worst: Decimal | None = None
        direction = Decimal("1") if side is OrderSide.BUY else Decimal("-1")
        maximum = maximum_slippage_bps / TEN_THOUSAND
        for level in levels:
            allowed = (
                level.price <= reference_price * (Decimal("1") + maximum)
                if side is OrderSide.BUY
                else level.price >= reference_price * (Decimal("1") - maximum)
            )
            if not allowed:
                break
            filled = min(remaining, level.quantity)
            if filled <= ZERO:
                continue
            notional += filled * level.price
            fee += self.fees.calculate(book.market, LiquidityRole.TAKER, level.price, filled)
            remaining -= filled
            consumed += 1
            worst = level.price
            if remaining == ZERO:
                break
        filled_quantity = quantity - remaining
        if filled_quantity == ZERO:
            return TakerExecutionPreview(quantity, ZERO, None, ZERO, ZERO, ZERO, 0, None)
        vwap = notional / filled_quantity
        best = levels[0].price
        return TakerExecutionPreview(
            requested_quantity=quantity,
            filled_quantity=filled_quantity,
            vwap=vwap,
            fee=fee,
            spread_crossing_bps=direction
            * (best - reference_price)
            / reference_price
            * TEN_THOUSAND,
            depth_slippage_bps=direction * (vwap - best) / reference_price * TEN_THOUSAND,
            levels_consumed=consumed,
            worst_fill_price=worst,
        )

    def submit(
        self,
        *,
        client_intent_id: str,
        market: MarketType,
        symbol: str,
        side: OrderSide,
        position_effect: PositionEffect,
        order_type: OrderType,
        quantity: Decimal,
        decision_time: datetime,
        books: tuple[BookState, ...],
        reference_price: Decimal,
        limit_price: Decimal | None = None,
        maker_preference: MakerPreference = MakerPreference.NONE,
        maximum_slippage_bps: Decimal = Decimal("10"),
        expiry_ms: int = 1000,
    ) -> ExecutionResult:
        if quantity <= ZERO or reference_price <= ZERO or expiry_ms <= 0:
            raise ValueError("order quantity, reference and expiry must be positive")
        if order_type in {OrderType.LIMIT, OrderType.MARKETABLE_LIMIT} and limit_price is None:
            raise ValueError("limit orders require a limit price")
        arrival = self.latency.exchange_arrival(decision_time)
        order = SimulatedOrder(
            order_id=self._identifier("order"),
            client_intent_id=client_intent_id,
            market=market,
            symbol=symbol.upper(),
            side=side,
            position_effect=position_effect,
            order_type=order_type,
            quantity=quantity,
            remaining_quantity=quantity,
            limit_price=limit_price,
            creation_time=decision_time,
            exchange_arrival_time=arrival,
            status=OrderStatus.CREATED,
            maker_preference=maker_preference,
            maximum_slippage_bps=maximum_slippage_bps,
            expiry_time=decision_time + timedelta(milliseconds=expiry_ms),
        )
        self._store(order)
        self._event(ExecutionEventType.ORDER_CREATED, decision_time, order)
        order = order.transition(OrderStatus.IN_TRANSIT)
        self._store(order)
        self._event(ExecutionEventType.ORDER_SENT, decision_time, order)
        book = self._arrival_book(books, order)
        if book is None or not book.synchronized:
            reason = "NO_SYNCHRONIZED_BOOK_AT_OR_AFTER_ARRIVAL"
            order = order.transition(OrderStatus.REJECTED, reject_reason=reason)
            self._store(order)
            self._event(ExecutionEventType.ORDER_REJECTED, arrival, order, reason=reason)
            return ExecutionResult(order, None, 0, None, None, ZERO, None)
        ack_time = max(book.timestamp, self.latency.acknowledgement_time(arrival))
        order = order.transition(OrderStatus.ACKNOWLEDGED)
        self._store(order)
        self._event(ExecutionEventType.ORDER_ACK, ack_time, order)
        if self._is_marketable(order, book):
            decision_best = self._decision_best(books, order)
            return self._consume(order, book, reference_price, decision_best)
        order = order.transition(OrderStatus.WORKING)
        self._store(order)
        self._event(ExecutionEventType.ORDER_WORKING, ack_time, order)
        queue_state = self._join_queue(order, book)
        self.queue_states[order.order_id] = queue_state
        return ExecutionResult(order, None, 0, None, None, ZERO, queue_state)

    def submit_intent(
        self,
        intent: IntradayOrderIntent,
        *,
        client_intent_id: str,
        market: MarketType,
        symbol: str,
        effect: PositionEffect,
        decision_time: datetime,
        books: tuple[BookState, ...],
    ) -> ExecutionResult:
        order_type, limit_price = ExecutionPlanner(self.config.policy).plan(intent, effect)
        side = (
            OrderSide.BUY
            if effect
            in {
                PositionEffect.OPEN_LONG,
                PositionEffect.CLOSE_SHORT,
            }
            else OrderSide.SELL
        )
        return self.submit(
            client_intent_id=client_intent_id,
            market=market,
            symbol=symbol,
            side=side,
            position_effect=effect,
            order_type=order_type,
            quantity=intent.quantity,
            decision_time=decision_time,
            books=books,
            reference_price=intent.reference_price,
            limit_price=limit_price,
            maker_preference=intent.maker_preference,
            maximum_slippage_bps=intent.maximum_slippage_bps,
            expiry_ms=intent.expiry_ms,
        )

    def execute_policy(
        self,
        intent: IntradayOrderIntent,
        *,
        client_intent_id: str,
        market: MarketType,
        symbol: str,
        effect: PositionEffect,
        decision_time: datetime,
        books: tuple[BookState, ...],
        alpha_still_valid: bool = True,
    ) -> tuple[ExecutionResult, ...]:
        """Run the fixed maker-first wait/cancel/fallback policy without PnL selection."""

        primary = self.submit_intent(
            intent,
            client_intent_id=client_intent_id,
            market=market,
            symbol=symbol,
            effect=effect,
            decision_time=decision_time,
            books=books,
        )
        if self.config.policy is ExecutionPolicy.TAKER_ONLY:
            return (primary,)
        latest = self._order(primary.order.order_id)
        if latest.status in TERMINAL_STATUSES:
            return (replace(primary, order=latest),)
        cancel_at = min(
            latest.expiry_time,
            decision_time + timedelta(milliseconds=self.config.maker_wait_ms),
        )
        pending = self.request_cancel(
            latest.order_id,
            cancel_at,
            expired=cancel_at >= latest.expiry_time,
        )
        if pending.cancel_effective_time is None:
            raise RuntimeError("maker-first cancel lost its effective time")
        self.advance(pending.cancel_effective_time)
        latest = self._order(primary.order.order_id)
        completed_primary = replace(primary, order=latest, queue_state=None)
        if (
            latest.remaining_quantity == ZERO
            or not alpha_still_valid
            or intent.urgency is OrderUrgency.PASSIVE
        ):
            return (completed_primary,)
        direction = Decimal("1") if latest.side is OrderSide.BUY else Decimal("-1")
        fallback_limit = intent.reference_price * (
            Decimal("1") + direction * intent.maximum_slippage_bps / TEN_THOUSAND
        )
        fallback = self.submit(
            client_intent_id=f"{client_intent_id}-taker-fallback",
            market=market,
            symbol=symbol,
            side=latest.side,
            position_effect=effect,
            order_type=OrderType.MARKETABLE_LIMIT,
            quantity=latest.remaining_quantity,
            decision_time=pending.cancel_effective_time,
            books=books,
            reference_price=intent.reference_price,
            limit_price=fallback_limit,
            maker_preference=MakerPreference.TAKER,
            maximum_slippage_bps=intent.maximum_slippage_bps,
            expiry_ms=intent.expiry_ms,
        )
        return completed_primary, fallback

    def process_aggressive_trade(
        self,
        order_id: str,
        *,
        timestamp: datetime,
        price: Decimal,
        quantity: Decimal,
        aggressive_side: AggressiveSide,
        book_before: BookState,
    ) -> SimulatedOrder:
        order = self._order(order_id)
        if order.status in TERMINAL_STATUSES:
            return order
        queue_state = self.queue_states.get(order_id)
        if queue_state is None or price != queue_state.price:
            return order
        correct_side = (
            aggressive_side is AggressiveSide.SELL
            if order.side is OrderSide.BUY
            else aggressive_side is AggressiveSide.BUY
        )
        if not correct_side:
            return order
        state, fill_quantity = self.queue.aggressive_trade(queue_state, quantity)
        self.queue_states[order_id] = state
        if fill_quantity == ZERO:
            return order
        cancel_was_pending = order.status is OrderStatus.CANCEL_PENDING
        fill = self._fill(order, timestamp, price, fill_quantity, LiquidityRole.MAKER, book_before)
        order = order.with_fill(fill)
        if cancel_was_pending and order.status is OrderStatus.PARTIALLY_FILLED:
            order = replace(order, status=OrderStatus.CANCEL_PENDING)
        self._record_filled_order(order, fill)
        return order

    def request_cancel(
        self,
        order_id: str,
        request_time: datetime,
        *,
        expired: bool = False,
    ) -> SimulatedOrder:
        order = self._order(order_id)
        if order.status in TERMINAL_STATUSES or order.status is OrderStatus.CANCEL_PENDING:
            return order
        effective = self.latency.cancel_effective_time(request_time)
        order = order.transition(OrderStatus.CANCEL_PENDING, cancel_effective_time=effective)
        if expired:
            self._expiry_cancel.add(order_id)
        self._store(order)
        self._event(ExecutionEventType.CANCEL_REQUESTED, request_time, order)
        return order

    def advance(self, timestamp: datetime) -> tuple[SimulatedOrder, ...]:
        changed: list[SimulatedOrder] = []
        for order in tuple(self._orders.values()):
            if (
                order.status in {OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED}
                and timestamp >= order.expiry_time
            ):
                order = self.request_cancel(order.order_id, order.expiry_time, expired=True)
            if (
                order.status is OrderStatus.CANCEL_PENDING
                and order.cancel_effective_time is not None
                and timestamp >= order.cancel_effective_time
            ):
                expired = order.order_id in self._expiry_cancel
                status = OrderStatus.EXPIRED if expired else OrderStatus.CANCELED
                order = order.transition(status)
                self._store(order)
                event_type = (
                    ExecutionEventType.ORDER_EXPIRED if expired else ExecutionEventType.CANCEL_ACK
                )
                effective_time = order.cancel_effective_time
                if effective_time is None:
                    raise RuntimeError("cancel-pending order lost effective timestamp")
                self._event(event_type, effective_time, order)
                self.queue_states.pop(order.order_id, None)
                changed.append(order)
        return tuple(changed)

    def _consume(
        self,
        order: SimulatedOrder,
        book: BookState,
        reference_price: Decimal,
        decision_best: Decimal | None,
    ) -> ExecutionResult:
        levels = book.asks if order.side is OrderSide.BUY else book.bids
        best = levels[0].price if levels else None
        remaining = order.remaining_quantity
        visible_consumed = ZERO
        fills: list[SimulatedFill] = []
        worst: Decimal | None = None
        for level in levels:
            if remaining == ZERO or not self._price_allowed(order, level.price, reference_price):
                break
            quantity = min(remaining, level.quantity)
            if quantity == ZERO:
                continue
            fill = self._fill(
                order,
                book.timestamp,
                level.price,
                quantity,
                LiquidityRole.TAKER,
                book,
            )
            fills.append(fill)
            order = order.with_fill(fill)
            self._record_filled_order(order, fill)
            remaining = order.remaining_quantity
            visible_consumed += quantity
            worst = level.price
        if not fills:
            if order.order_type is OrderType.MARKET:
                reason = "NO_EXECUTABLE_LIQUIDITY_WITHIN_SLIPPAGE"
                order = order.transition(OrderStatus.REJECTED, reject_reason=reason)
                self._store(order)
                self._event(ExecutionEventType.ORDER_REJECTED, book.timestamp, order, reason=reason)
            else:
                order = order.transition(OrderStatus.WORKING)
                self._store(order)
                self._event(ExecutionEventType.ORDER_WORKING, book.timestamp, order)
                state = self._join_queue(order, book)
                self.queue_states[order.order_id] = state
                return ExecutionResult(order, None, 0, best, None, ZERO, state)
            return ExecutionResult(order, None, 0, best, None, ZERO, None)
        if order.remaining_quantity > ZERO:
            if (
                order.order_type is OrderType.MARKETABLE_LIMIT
                and self.config.remainder_policy is RemainderPolicy.PARTIAL_FILL
            ):
                order = replace(order, status=OrderStatus.WORKING)
                self._store(order)
                self._event(ExecutionEventType.ORDER_WORKING, book.timestamp, order)
                state = self._join_queue(order, book)
                self.queue_states[order.order_id] = state
            elif self.config.remainder_policy is RemainderPolicy.REJECT_REMAINDER:
                order = self.request_cancel(order.order_id, book.timestamp)
        vwap = order.vwap
        slippage = self._slippage(
            order.side,
            reference_price,
            best,
            vwap,
            decision_best,
        )
        return ExecutionResult(
            order,
            slippage,
            len(fills),
            best,
            worst,
            visible_consumed,
            self.queue_states.get(order.order_id),
        )

    def _fill(
        self,
        order: SimulatedOrder,
        timestamp: datetime,
        price: Decimal,
        quantity: Decimal,
        role: LiquidityRole,
        book: BookState,
    ) -> SimulatedFill:
        return SimulatedFill(
            fill_id=self._identifier("fill"),
            order_id=order.order_id,
            timestamp=timestamp,
            price=price,
            quantity=quantity,
            liquidity_role=role,
            fee=self.fees.calculate(order.market, role, price, quantity),
            fee_asset=self.fees.config.fee_asset,
            book_before=book.asks if order.side is OrderSide.BUY else book.bids,
            latency_ms=Decimal(self.latency.config.fill_notification_latency_ms),
            sequence=book.sequence,
        )

    def _record_filled_order(self, order: SimulatedOrder, fill: SimulatedFill) -> None:
        before = self.position_ledger.snapshot(order.market, order.symbol, fill.timestamp)
        self.execution_ledger.record_fill(fill)
        self._store(order)
        after = self.position_ledger.apply_fill(order, fill)
        event_type = (
            ExecutionEventType.ORDER_FILL
            if order.status is OrderStatus.FILLED
            else ExecutionEventType.ORDER_PARTIAL_FILL
        )
        self._event(event_type, fill.timestamp, order, quantity=fill.quantity, price=fill.price)
        if before.quantity == ZERO and after.quantity > ZERO:
            self._event(ExecutionEventType.POSITION_OPENED, fill.timestamp, order)
        elif before.quantity > ZERO and after.quantity == ZERO:
            self._event(ExecutionEventType.POSITION_CLOSED, fill.timestamp, order)
        elif order.position_effect in {PositionEffect.CLOSE_LONG, PositionEffect.CLOSE_SHORT}:
            self._event(ExecutionEventType.POSITION_REDUCED, fill.timestamp, order)
        if order.status is OrderStatus.FILLED:
            self.queue_states.pop(order.order_id, None)

    def _store(self, order: SimulatedOrder) -> None:
        self.execution_ledger.record_order(order)
        self._orders[order.order_id] = order

    def _event(
        self,
        event_type: ExecutionEventType,
        timestamp: datetime,
        order: SimulatedOrder,
        *,
        reason: str | None = None,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
    ) -> None:
        self.events.append(
            ExecutionEvent(
                event_id=self._identifier("event"),
                event_type=event_type,
                timestamp=timestamp,
                order_id=order.order_id,
                reason_code=reason,
                quantity=quantity,
                price=price,
            )
        )

    def _identifier(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self.config.seed:04d}-{self._counter:08d}"

    def _order(self, order_id: str) -> SimulatedOrder:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise ValueError(f"unknown order: {order_id}") from exc

    @staticmethod
    def _arrival_book(books: tuple[BookState, ...], order: SimulatedOrder) -> BookState | None:
        valid = (
            book
            for book in sorted(books, key=lambda item: (item.timestamp, item.sequence))
            if book.market is order.market
            and book.symbol == order.symbol
            and book.timestamp >= order.exchange_arrival_time
        )
        return next(valid, None)

    @staticmethod
    def _decision_best(
        books: tuple[BookState, ...],
        order: SimulatedOrder,
    ) -> Decimal | None:
        eligible = [
            book
            for book in books
            if book.market is order.market
            and book.symbol == order.symbol
            and book.timestamp <= order.creation_time
        ]
        if not eligible:
            return None
        decision_book = max(eligible, key=lambda item: (item.timestamp, item.sequence))
        return decision_book.best_ask if order.side is OrderSide.BUY else decision_book.best_bid

    @staticmethod
    def _is_marketable(order: SimulatedOrder, book: BookState) -> bool:
        if order.order_type is OrderType.MARKET:
            return True
        if order.limit_price is None:
            return False
        if order.side is OrderSide.BUY:
            return book.best_ask is not None and order.limit_price >= book.best_ask
        return book.best_bid is not None and order.limit_price <= book.best_bid

    @staticmethod
    def _price_allowed(
        order: SimulatedOrder,
        price: Decimal,
        reference: Decimal,
    ) -> bool:
        if order.limit_price is not None:
            within_limit = (
                price <= order.limit_price
                if order.side is OrderSide.BUY
                else price >= order.limit_price
            )
            if not within_limit:
                return False
        maximum = order.maximum_slippage_bps / TEN_THOUSAND
        return (
            price <= reference * (Decimal("1") + maximum)
            if order.side is OrderSide.BUY
            else price >= reference * (Decimal("1") - maximum)
        )

    def _join_queue(self, order: SimulatedOrder, book: BookState) -> QueueState:
        if order.limit_price is None:
            raise ValueError("working passive order requires a limit")
        levels = book.bids if order.side is OrderSide.BUY else book.asks
        visible = next(
            (level.quantity for level in levels if level.price == order.limit_price),
            ZERO,
        )
        return self.queue.join(order.order_id, order.limit_price, visible, order.remaining_quantity)

    def _slippage(
        self,
        side: OrderSide,
        reference: Decimal,
        best: Decimal | None,
        vwap: Decimal | None,
        decision_best: Decimal | None,
    ) -> SlippageBreakdown | None:
        if best is None or vwap is None:
            return None
        direction = Decimal("1") if side is OrderSide.BUY else Decimal("-1")
        starting_best = decision_best or best
        spread = direction * (starting_best - reference) / reference * TEN_THOUSAND
        latency = direction * (best - starting_best) / reference * TEN_THOUSAND
        depth = direction * (vwap - best) / reference * TEN_THOUSAND
        return SlippageBreakdown(
            spread_crossing_bps=spread,
            depth_slippage_bps=depth,
            latency_slippage_bps=latency,
            residual_slippage_bps=self.config.residual_slippage_bps,
        )


class ExecutionSimulator(SimulatedOrderBookVenue):
    """Public name for the deterministic venue simulation."""


class SimulatedExchange(SimulatedOrderBookVenue):
    """Alias emphasizing that no external exchange connection exists."""


ExecutionEngine = ExecutionSimulator


def effect_for_side(side: PositionSide, *, opening: bool) -> PositionEffect:
    if side is PositionSide.LONG:
        return PositionEffect.OPEN_LONG if opening else PositionEffect.CLOSE_LONG
    return PositionEffect.OPEN_SHORT if opening else PositionEffect.CLOSE_SHORT
