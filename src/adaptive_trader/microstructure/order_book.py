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

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


@dataclass(frozen=True, slots=True)
class OrderBookUpdateResult:
    applied: bool
    synchronized: bool
    status: OrderBookStatus
    reason: OrderBookReason
    update_id: int | None


class LocalOrderBook:
    """Maintain one market/symbol book and fail closed on any sequence gap."""

    def __init__(self, market_type: MarketType, symbol: str, *, visible_levels: int = 20) -> None:
        if visible_levels < 20:
            raise ValueError("LocalOrderBook must expose at least 20 levels")
        normalized = symbol.strip().upper()
        if not normalized or not normalized.isalnum():
            raise ValueError("symbol must be alphanumeric")
        self.market_type = market_type
        self.symbol = normalized
        self.visible_levels = visible_levels
        self.status = OrderBookStatus.EMPTY
        self.update_id: int | None = None
        self.last_event_time: datetime | None = None
        self.last_receive_time: datetime | None = None
        self.last_reason: OrderBookReason | None = None
        self.sequence_gap_count = 0
        self.resync_count = 0
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._buffer: list[MicrostructureEvent] = []
        self._seen_event_ids: set[str] = set()

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
        return tuple(
            DepthLevel(price, self._asks[price])
            for price in sorted(self._asks)[:count]
        )

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
        self.update_id = event.sequence_last
        self.last_event_time = event.exchange_event_time
        self.last_receive_time = event.receive_wall_time
        self.status = OrderBookStatus.BUFFERING
        pending = tuple(
            item
            for item in self._buffer
            if item.sequence_last is not None and item.sequence_last > event.sequence_last
        )
        self._buffer.clear()
        for update in pending:
            result = self._apply_sequenced(update, bootstrap=True)
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
        return self._apply_sequenced(event, bootstrap=False)

    def begin_resync(self) -> None:
        self.status = OrderBookStatus.RESYNC_IN_PROGRESS
        self.resync_count += 1
        self.update_id = None
        self._bids.clear()
        self._asks.clear()
        self._buffer.clear()
        self._seen_event_ids.clear()

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
            return self._invalidate(OrderBookReason.ORDER_BOOK_DESYNC)
        if event.sequence_last <= self.update_id:
            reason = (
                OrderBookReason.DUPLICATE_UPDATE
                if event.sequence_last == self.update_id
                else OrderBookReason.STALE_UPDATE
            )
            return self._result(False, reason)
        expected = self.update_id + 1
        futures_link_broken = (
            self.market_type is MarketType.USD_M_FUTURES
            and event.sequence_previous is not None
            and event.sequence_previous != self.update_id
        )
        if futures_link_broken or not event.sequence_first <= expected <= event.sequence_last:
            return self._invalidate(OrderBookReason.ORDER_BOOK_DESYNC)
        self._apply_levels(self._bids, event.bids)
        self._apply_levels(self._asks, event.asks)
        self.update_id = event.sequence_last
        self.last_event_time = event.exchange_event_time
        self.last_receive_time = event.receive_wall_time
        if not self._valid_uncrossed_book():
            return self._invalidate(OrderBookReason.CROSSED_BOOK)
        self.status = (
            OrderBookStatus.BUFFERING if bootstrap else OrderBookStatus.SYNCHRONIZED
        )
        self.last_reason = OrderBookReason.UPDATE_APPLIED
        return self._result(True, OrderBookReason.UPDATE_APPLIED)

    def _invalidate(self, reason: OrderBookReason) -> OrderBookUpdateResult:
        self.status = OrderBookStatus.INVALID
        self.last_reason = reason
        if reason is OrderBookReason.ORDER_BOOK_DESYNC:
            self.sequence_gap_count += 1
        return self._result(False, reason)

    def _result(self, applied: bool, reason: OrderBookReason) -> OrderBookUpdateResult:
        return OrderBookUpdateResult(
            applied=applied,
            synchronized=self.synchronized,
            status=self.status,
            reason=reason,
            update_id=self.update_id,
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
