"""Order and position accounting ledgers for research-only simulation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.execution.models import (
    BookState,
    PositionEffect,
    PositionSnapshot,
    SimulatedFill,
    SimulatedOrder,
)
from adaptive_trader.microstructure.models import DepthLevel

ZERO = Decimal("0")


@dataclass(slots=True)
class _MutablePosition:
    market: MarketType
    symbol: str
    side: PositionSide | None = None
    quantity: Decimal = ZERO
    average_entry: Decimal | None = None
    realized_pnl: Decimal = ZERO
    fees: Decimal = ZERO
    funding: Decimal = ZERO
    entry_time: datetime | None = None


class ExecutionLedger:
    def __init__(self) -> None:
        self._orders: dict[str, SimulatedOrder] = {}
        self._fills: dict[str, SimulatedFill] = {}

    @property
    def orders(self) -> tuple[SimulatedOrder, ...]:
        return tuple(self._orders.values())

    @property
    def fills(self) -> tuple[SimulatedFill, ...]:
        return tuple(self._fills.values())

    def record_order(self, order: SimulatedOrder) -> None:
        previous = self._orders.get(order.order_id)
        if previous is not None and previous.status.value in {
            "FILLED",
            "CANCELED",
            "EXPIRED",
            "REJECTED",
        }:
            raise ValueError("terminal order state is immutable")
        self._orders[order.order_id] = order

    def record_fill(self, fill: SimulatedFill) -> None:
        if fill.fill_id in self._fills:
            raise ValueError("duplicate fill identifier")
        if fill.order_id not in self._orders:
            raise ValueError("fill order is not registered")
        self._fills[fill.fill_id] = fill

    def order(self, order_id: str) -> SimulatedOrder:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise ValueError(f"unknown order: {order_id}") from exc


class PositionLedger:
    """One net position per market/symbol; simultaneous hedging is intentionally absent."""

    def __init__(self, *, initial_cash: Decimal = Decimal("1000000")) -> None:
        if initial_cash < ZERO or not initial_cash.is_finite():
            raise ValueError("initial cash must be non-negative and finite")
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self._positions: dict[tuple[MarketType, str], _MutablePosition] = {}

    def apply_fill(
        self,
        order: SimulatedOrder,
        fill: SimulatedFill,
    ) -> PositionSnapshot:
        key = (order.market, order.symbol)
        position = self._positions.setdefault(
            key,
            _MutablePosition(market=order.market, symbol=order.symbol),
        )
        effect = order.position_effect
        if order.market is MarketType.SPOT and effect is PositionEffect.OPEN_SHORT:
            raise ValueError("Spot short is forbidden")
        if effect in {PositionEffect.OPEN_LONG, PositionEffect.OPEN_SHORT}:
            expected = (
                PositionSide.LONG
                if effect is PositionEffect.OPEN_LONG
                else PositionSide.SHORT
            )
            if position.side not in {None, expected}:
                raise ValueError("simultaneous long/short hedge is unsupported")
            previous_notional = (position.average_entry or ZERO) * position.quantity
            new_quantity = position.quantity + fill.quantity
            position.average_entry = (
                previous_notional + fill.price * fill.quantity
            ) / new_quantity
            position.quantity = new_quantity
            position.side = expected
            position.entry_time = position.entry_time or fill.timestamp
            if order.market is MarketType.SPOT:
                cost = fill.price * fill.quantity + fill.fee
                if cost > self.cash:
                    raise ValueError("Spot cash cannot become negative")
                self.cash -= cost
        else:
            expected = (
                PositionSide.LONG
                if effect is PositionEffect.CLOSE_LONG
                else PositionSide.SHORT
            )
            if position.side is not expected or fill.quantity > position.quantity:
                raise ValueError("closing fill exceeds or conflicts with position")
            if position.average_entry is None:
                raise ValueError("open position has no average entry")
            gross = (
                (fill.price - position.average_entry) * fill.quantity
                if expected is PositionSide.LONG
                else (position.average_entry - fill.price) * fill.quantity
            )
            position.realized_pnl += gross
            position.quantity -= fill.quantity
            if order.market is MarketType.SPOT:
                self.cash += fill.price * fill.quantity - fill.fee
            if position.quantity == ZERO:
                position.side = None
                position.average_entry = None
                position.entry_time = None
        position.fees += fill.fee
        return self.snapshot(order.market, order.symbol, fill.timestamp)

    def apply_funding(
        self,
        market: MarketType,
        symbol: str,
        amount: Decimal,
    ) -> None:
        if market is MarketType.SPOT:
            raise ValueError("Spot does not have funding")
        if not amount.is_finite():
            raise ValueError("funding must be finite")
        self._position(market, symbol).funding += amount

    def snapshot(
        self,
        market: MarketType,
        symbol: str,
        timestamp: datetime,
        *,
        mark_price: Decimal | None = None,
        book: BookState | None = None,
    ) -> PositionSnapshot:
        position = self._positions.get(
            (market, symbol),
            _MutablePosition(market=market, symbol=symbol),
        )
        mark_pnl = self._unrealized(position, mark_price)
        executable_price: Decimal | None = None
        if book is not None and position.quantity > ZERO:
            levels = book.bids if position.side is PositionSide.LONG else book.asks
            executable_price = self._vwap(levels, position.quantity)
        executable_pnl = self._unrealized(position, executable_price)
        holding = (
            max(0, int((timestamp - position.entry_time).total_seconds() * 1000))
            if position.entry_time is not None
            else 0
        )
        return PositionSnapshot(
            market=market,
            symbol=symbol,
            side=position.side,
            quantity=position.quantity,
            average_entry=position.average_entry,
            realized_pnl=position.realized_pnl,
            unrealized_mark_pnl=mark_pnl,
            unrealized_executable_pnl=executable_pnl,
            fees=position.fees,
            funding=position.funding,
            entry_time=position.entry_time,
            holding_time_ms=holding,
        )

    def _position(self, market: MarketType, symbol: str) -> _MutablePosition:
        return self._positions.setdefault(
            (market, symbol),
            _MutablePosition(market=market, symbol=symbol),
        )

    @staticmethod
    def _unrealized(position: _MutablePosition, price: Decimal | None) -> Decimal:
        if price is None or position.average_entry is None or position.side is None:
            return ZERO
        difference = (
            price - position.average_entry
            if position.side is PositionSide.LONG
            else position.average_entry - price
        )
        return difference * position.quantity

    @staticmethod
    def _vwap(levels: tuple[DepthLevel, ...], quantity: Decimal) -> Decimal | None:
        remaining = quantity
        notional = ZERO
        for item in levels:
            price = item.price
            available = item.quantity
            consumed = min(remaining, available)
            notional += consumed * price
            remaining -= consumed
            if remaining == ZERO:
                return notional / quantity
        return None
