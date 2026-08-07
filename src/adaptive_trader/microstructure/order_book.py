"""Deterministic local order book with explicit snapshot/diff synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.models import (
    DepthLevel,
    LiquiditySnapshot,
    MicrostructureEvent,
    MicrostructureStreamType,
    OrderBookReason,
    OrderBookStatus,
)
from adaptive_trader.microstructure.sequence import (
    DepthSequencePolicy,
    FuturesSequencePolicy,
    GapClassification,
    SequenceDecision,
    SpotSequencePolicy,
)

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


@dataclass(frozen=True, slots=True)
class OrderBookUpdateResult:
    applied: bool
    synchronized: bool
    status: OrderBookStatus
    reason: OrderBookReason
    update_id: int | None
    gap_classification: GapClassification | None = None


class LocalOrderBook:
    """Maintain one market/symbol book and fail closed on any sequence gap."""

    def __init__(
        self,
        market_type: MarketType,
        symbol: str,
        *,
        visible_levels: int = 20,
        retained_levels: int | None = None,
    ) -> None:
        if visible_levels < 20:
            raise ValueError("LocalOrderBook must expose at least 20 levels")
        normalized = symbol.strip().upper()
        if not normalized or not normalized.isalnum():
            raise ValueError("symbol must be alphanumeric")
        self.market_type = market_type
        self.symbol = normalized
        self.visible_levels = visible_levels
        if retained_levels is not None and retained_levels < visible_levels:
            raise ValueError("retained_levels must cover visible_levels")
        self.retained_levels = retained_levels
        self.status = OrderBookStatus.EMPTY
        self.update_id: int | None = None
        self.last_event_time: datetime | None = None
        self.last_receive_time: datetime | None = None
        self.last_reason: OrderBookReason | None = None
        self.last_sequence_decision: SequenceDecision | None = None
        self.sequence_gap_count = 0
        self.resync_count = 0
        self.classification_counts: dict[GapClassification, int] = {
            item: 0 for item in GapClassification
        }
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._buffer: list[MicrostructureEvent] = []
        self._seen_event_ids: set[str] = set()
        self._awaiting_first_diff = False
        self.sequence_policy: DepthSequencePolicy = (
            SpotSequencePolicy() if market_type is MarketType.SPOT else FuturesSequencePolicy()
        )

    @property
    def synchronized(self) -> bool:
        return self.status is OrderBookStatus.SYNCHRONIZED

    @property
    def buffered_update_count(self) -> int:
        return len(self._buffer)

    @property
    def best_bid(self) -> DepthLevel | None:
        if not self._bids:
            return None
        price = max(self._bids)
        return DepthLevel(price, self._bids[price])

    @property
    def best_ask(self) -> DepthLevel | None:
        if not self._asks:
            return None
        price = min(self._asks)
        return DepthLevel(price, self._asks[price])

    def top_bids(self, count: int = 20) -> tuple[DepthLevel, ...]:
        self._validate_count(count)
        return tuple(
            DepthLevel(price, self._bids[price])
            for price in sorted(self._bids, reverse=True)[:count]
        )

    def top_asks(self, count: int = 20) -> tuple[DepthLevel, ...]:
        self._validate_count(count)
        return tuple(DepthLevel(price, self._asks[price]) for price in sorted(self._asks)[:count])

    def buffer_update(self, event: MicrostructureEvent) -> OrderBookUpdateResult:
        self._validate_event(event, MicrostructureStreamType.DEPTH_UPDATE)
        if self.synchronized:
            return self.apply_update(event)
        if event.event_id in self._seen_event_ids:
            return self._result(False, OrderBookReason.DUPLICATE_UPDATE)
        self._seen_event_ids.add(event.event_id)
        self.status = OrderBookStatus.BUFFERING
        self._buffer.append(event)
        self._buffer.sort(key=self._event_order)
        return self._result(False, OrderBookReason.UPDATE_APPLIED)

    def apply_snapshot(self, event: MicrostructureEvent) -> OrderBookUpdateResult:
        self._validate_event(event, MicrostructureStreamType.SNAPSHOT)
        if event.sequence_last is None:
            raise ValueError("snapshot requires lastUpdateId")
        self._bids = {level.price: level.quantity for level in event.bids if level.quantity > ZERO}
        self._asks = {level.price: level.quantity for level in event.asks if level.quantity > ZERO}
        self._trim_retained_levels()
        self.update_id = event.sequence_last
        self.last_event_time = event.exchange_event_time
        self.last_receive_time = event.receive_wall_time
        self.status = OrderBookStatus.BUFFERING
        self._awaiting_first_diff = True
        pending = tuple(
            item
            for item in self._buffer
            if item.sequence_last is not None and item.sequence_last >= event.sequence_last
        )
        self._buffer.clear()
        for update in pending:
            result = self._apply_sequenced(
                update,
                bootstrap=self._awaiting_first_diff,
            )
            if result.status is OrderBookStatus.INVALID:
                return result
        if not self._valid_uncrossed_book():
            return self._invalidate(OrderBookReason.CROSSED_BOOK)
        self.status = OrderBookStatus.SYNCHRONIZED
        self.last_reason = OrderBookReason.SNAPSHOT_APPLIED
        return self._result(True, OrderBookReason.SNAPSHOT_APPLIED)

    def apply_update(self, event: MicrostructureEvent) -> OrderBookUpdateResult:
        self._validate_event(event, MicrostructureStreamType.DEPTH_UPDATE)
        if self.status is not OrderBookStatus.SYNCHRONIZED:
            if self.status in {OrderBookStatus.INVALID, OrderBookStatus.RESYNC_IN_PROGRESS}:
                return self._result(False, OrderBookReason.ORDER_BOOK_DESYNC)
            return self.buffer_update(event)
        if event.event_id in self._seen_event_ids:
            return self._result(False, OrderBookReason.DUPLICATE_UPDATE)
        self._seen_event_ids.add(event.event_id)
        return self._apply_sequenced(
            event,
            bootstrap=self._awaiting_first_diff,
        )

    def begin_resync(self) -> None:
        self.status = OrderBookStatus.RESYNC_IN_PROGRESS
        self.resync_count += 1
        self.update_id = None
        self._bids.clear()
        self._asks.clear()
        self._buffer.clear()
        self._seen_event_ids.clear()
        self._awaiting_first_diff = False

    def mark_stale(self) -> OrderBookUpdateResult:
        return self._invalidate(OrderBookReason.ORDER_BOOK_DESYNC)

    def liquidity_snapshot(self, now: datetime) -> LiquiditySnapshot:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            raise ValueError("liquidity snapshot requires both sides of the book")
        bids = self.top_bids(self.visible_levels)
        asks = self.top_asks(self.visible_levels)
        mid = (bid.price + ask.price) / Decimal("2")
        spread = ask.price - bid.price
        age = (
            Decimal(str((now - self.last_receive_time).total_seconds() * 1000))
            if self.last_receive_time is not None
            else Decimal("Infinity")
        )
        if not age.is_finite():
            raise ValueError("book has no receive timestamp")
        return LiquiditySnapshot(
            timestamp=now,
            market_type=self.market_type,
            symbol=self.symbol,
            best_bid=bid.price,
            best_ask=ask.price,
            bid_quantity=bid.quantity,
            ask_quantity=ask.quantity,
            mid_price=mid,
            spread=spread,
            spread_bps=spread / mid * TEN_THOUSAND,
            top_5_bid_notional=self._notional(bids, 5),
            top_5_ask_notional=self._notional(asks, 5),
            top_10_bid_notional=self._notional(bids, 10),
            top_10_ask_notional=self._notional(asks, 10),
            top_20_bid_notional=self._notional(bids, 20),
            top_20_ask_notional=self._notional(asks, 20),
            depth_imbalance_5=self._imbalance(bids, asks, 5),
            depth_imbalance_10=self._imbalance(bids, asks, 10),
            depth_imbalance_20=self._imbalance(bids, asks, 20),
            book_age_ms=age,
            synchronized=self.synchronized,
            bids=bids,
            asks=asks,
        )

    def _apply_sequenced(
        self,
        event: MicrostructureEvent,
        *,
        bootstrap: bool,
    ) -> OrderBookUpdateResult:
        if self.update_id is None or event.sequence_first is None or event.sequence_last is None:
            self.classification_counts[GapClassification.PARSER_ERROR] += 1
            return self._invalidate(
                OrderBookReason.ORDER_BOOK_DESYNC,
                GapClassification.PARSER_ERROR,
            )
        decision = (
            self.sequence_policy.bootstrap(self.update_id, event)
            if bootstrap
            else self.sequence_policy.next_event(self.update_id, event)
        )
        self.last_sequence_decision = decision
        if decision.classification is not None:
            self.classification_counts[decision.classification] += 1
        if not decision.accepted:
            if decision.classification is GapClassification.DUPLICATE_EVENT:
                return self._result(
                    False,
                    OrderBookReason.DUPLICATE_UPDATE,
                    decision.classification,
                )
            if decision.classification in {
                GapClassification.OLD_EVENT,
                GapClassification.OUT_OF_ORDER_EVENT,
            }:
                return self._result(
                    False,
                    OrderBookReason.STALE_UPDATE,
                    decision.classification,
                )
            return self._invalidate(
                OrderBookReason.ORDER_BOOK_DESYNC,
                decision.classification,
            )
        self._apply_levels(self._bids, event.bids)
        self._apply_levels(self._asks, event.asks)
        self._trim_retained_levels()
        self.update_id = event.sequence_last
        self.last_event_time = event.exchange_event_time
        self.last_receive_time = event.receive_wall_time
        self._awaiting_first_diff = False
        if not self._valid_uncrossed_book():
            return self._invalidate(OrderBookReason.CROSSED_BOOK)
        self.status = OrderBookStatus.SYNCHRONIZED
        self.last_reason = OrderBookReason.UPDATE_APPLIED
        return self._result(True, OrderBookReason.UPDATE_APPLIED)

    def _invalidate(
        self,
        reason: OrderBookReason,
        classification: GapClassification | None = None,
    ) -> OrderBookUpdateResult:
        self.status = OrderBookStatus.INVALID
        self.last_reason = reason
        if classification is GapClassification.REAL_SEQUENCE_GAP:
            self.sequence_gap_count += 1
        return self._result(False, reason, classification)

    def _result(
        self,
        applied: bool,
        reason: OrderBookReason,
        classification: GapClassification | None = None,
    ) -> OrderBookUpdateResult:
        return OrderBookUpdateResult(
            applied=applied,
            synchronized=self.synchronized,
            status=self.status,
            reason=reason,
            update_id=self.update_id,
            gap_classification=classification,
        )

    def _validate_event(
        self,
        event: MicrostructureEvent,
        expected: MicrostructureStreamType,
    ) -> None:
        if (
            event.market_type is not self.market_type
            or event.symbol != self.symbol
            or event.stream_type is not expected
        ):
            raise ValueError("event belongs to a different book or stream")

    def _valid_uncrossed_book(self) -> bool:
        bid = self.best_bid
        ask = self.best_ask
        return bid is not None and ask is not None and bid.price < ask.price

    @staticmethod
    def _apply_levels(book: dict[Decimal, Decimal], levels: tuple[DepthLevel, ...]) -> None:
        for level in levels:
            if level.quantity == ZERO:
                book.pop(level.price, None)
            else:
                book[level.price] = level.quantity

    def _trim_retained_levels(self) -> None:
        if self.retained_levels is None:
            return
        retained_bids = set(sorted(self._bids, reverse=True)[: self.retained_levels])
        retained_asks = set(sorted(self._asks)[: self.retained_levels])
        self._bids = {
            price: quantity for price, quantity in self._bids.items() if price in retained_bids
        }
        self._asks = {
            price: quantity for price, quantity in self._asks.items() if price in retained_asks
        }

    @staticmethod
    def _event_order(event: MicrostructureEvent) -> tuple[int, int, int, str]:
        maximum = 2**63 - 1
        return (
            event.sequence_first if event.sequence_first is not None else maximum,
            event.sequence_last if event.sequence_last is not None else maximum,
            event.receive_monotonic_ns,
            event.event_id,
        )

    @staticmethod
    def _notional(levels: tuple[DepthLevel, ...], count: int) -> Decimal:
        return sum((level.notional for level in levels[:count]), ZERO)

    @staticmethod
    def _imbalance(
        bids: tuple[DepthLevel, ...],
        asks: tuple[DepthLevel, ...],
        count: int,
    ) -> Decimal:
        bid_depth = LocalOrderBook._notional(bids, count)
        ask_depth = LocalOrderBook._notional(asks, count)
        total = bid_depth + ask_depth
        return (bid_depth - ask_depth) / total if total > ZERO else ZERO

    @staticmethod
    def _validate_count(count: int) -> None:
        if count <= 0:
            raise ValueError("level count must be positive")
