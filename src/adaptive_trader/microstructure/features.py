"""Point-in-time microstructure features built only from observed event prefixes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from adaptive_trader.microstructure.models import (
    AggressiveSide,
    LiquiditySnapshot,
    MicrostructureEvent,
    MicrostructureStreamType,
)

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


@dataclass(frozen=True, slots=True)
class TradeFlowWindow:
    window_ms: int
    aggressive_buy_qty: Decimal
    aggressive_sell_qty: Decimal
    aggressive_trade_imbalance: Decimal
    trade_count: int
    quote_notional: Decimal


@dataclass(frozen=True, slots=True)
class MicrostructureFeatureSnapshot:
    timestamp: datetime
    spread: Decimal
    spread_bps: Decimal
    mid_price: Decimal
    microprice: Decimal
    microprice_edge_bps: Decimal
    depth_imbalance_5: Decimal
    depth_imbalance_10: Decimal
    depth_imbalance_20: Decimal
    trade_flow_250ms: TradeFlowWindow
    trade_flow_1s: TradeFlowWindow
    trade_flow_3s: TradeFlowWindow
    trade_flow_10s: TradeFlowWindow
    ofi_250ms: Decimal
    ofi_1s: Decimal
    ofi_3s: Decimal
    momentum_250ms_bps: Decimal
    momentum_1s_bps: Decimal
    momentum_3s_bps: Decimal
    momentum_10s_bps: Decimal
    volatility_1s_bps: Decimal
    volatility_5s_bps: Decimal
    volatility_30s_bps: Decimal
    event_age_ms: Decimal
    book_age_ms: Decimal
    trade_age_ms: Decimal | None
    warmup_complete: bool


@dataclass(frozen=True, slots=True)
class _BookObservation:
    timestamp: datetime
    best_bid: Decimal
    bid_quantity: Decimal
    best_ask: Decimal
    ask_quantity: Decimal
    mid: Decimal


class MicrostructureFeatureEngine:
    """Stateful prefix calculator; snapshot queries explicitly exclude future records."""

    def __init__(self, *, retention_seconds: int = 60) -> None:
        if retention_seconds < 30:
            raise ValueError("feature retention must cover at least 30 seconds")
        self.retention = timedelta(seconds=retention_seconds)
        self._trades: list[MicrostructureEvent] = []
        self._books: list[_BookObservation] = []
        self._ofi: list[tuple[datetime, Decimal]] = []
        self._last_event_time: datetime | None = None

    def record_event(self, event: MicrostructureEvent) -> None:
        if event.stream_type is MicrostructureStreamType.AGG_TRADE:
            self._trades.append(event)
            self._trades.sort(key=self._event_order)
        if self._last_event_time is None or event.exchange_event_time > self._last_event_time:
            self._last_event_time = event.exchange_event_time

    def record_book(self, liquidity: LiquiditySnapshot) -> None:
        current = _BookObservation(
            timestamp=liquidity.timestamp,
            best_bid=liquidity.best_bid,
            bid_quantity=liquidity.bid_quantity,
            best_ask=liquidity.best_ask,
            ask_quantity=liquidity.ask_quantity,
            mid=liquidity.mid_price,
        )
        previous = self._books[-1] if self._books else None
        self._books.append(current)
        self._books.sort(key=lambda item: item.timestamp)
        if previous is not None and current.timestamp >= previous.timestamp:
            self._ofi.append((current.timestamp, self._ofi_increment(previous, current)))
        if self._last_event_time is None or liquidity.timestamp > self._last_event_time:
            self._last_event_time = liquidity.timestamp

    def snapshot(
        self,
        *,
        now: datetime,
        liquidity: LiquiditySnapshot,
    ) -> MicrostructureFeatureSnapshot:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("feature timestamp must be timezone-aware")
        if liquidity.timestamp > now:
            raise ValueError("liquidity snapshot cannot come from the future")
        books = tuple(item for item in self._books if item.timestamp <= now)
        trades = tuple(item for item in self._trades if item.exchange_event_time <= now)
        if not books:
            raise ValueError("feature engine requires an observed book")
        denominator = liquidity.bid_quantity + liquidity.ask_quantity
        microprice = (
            (
                liquidity.best_ask * liquidity.bid_quantity
                + liquidity.best_bid * liquidity.ask_quantity
            )
            / denominator
            if denominator > ZERO
            else liquidity.mid_price
        )
        last_event = max(
            (
                timestamp
                for timestamp in (
                    self._last_event_time,
                    books[-1].timestamp,
                    trades[-1].exchange_event_time if trades else None,
                )
                if timestamp is not None and timestamp <= now
            ),
            default=books[-1].timestamp,
        )
        flows = {
            window: self._trade_flow(trades, now, window)
            for window in (250, 1000, 3000, 10000)
        }
        momentums = {
            window: self._momentum(books, now, window)
            for window in (250, 1000, 3000, 10000)
        }
        volatility = {
            window: self._volatility(books, now, window)
            for window in (1000, 5000, 30000)
        }
        return MicrostructureFeatureSnapshot(
            timestamp=now,
            spread=liquidity.spread,
            spread_bps=liquidity.spread_bps,
            mid_price=liquidity.mid_price,
            microprice=microprice,
            microprice_edge_bps=(microprice - liquidity.mid_price)
            / liquidity.mid_price
            * TEN_THOUSAND,
            depth_imbalance_5=liquidity.depth_imbalance_5,
            depth_imbalance_10=liquidity.depth_imbalance_10,
            depth_imbalance_20=liquidity.depth_imbalance_20,
            trade_flow_250ms=flows[250],
            trade_flow_1s=flows[1000],
            trade_flow_3s=flows[3000],
            trade_flow_10s=flows[10000],
            ofi_250ms=self._ofi_sum(now, 250),
            ofi_1s=self._ofi_sum(now, 1000),
            ofi_3s=self._ofi_sum(now, 3000),
            momentum_250ms_bps=momentums[250],
            momentum_1s_bps=momentums[1000],
            momentum_3s_bps=momentums[3000],
            momentum_10s_bps=momentums[10000],
            volatility_1s_bps=volatility[1000],
            volatility_5s_bps=volatility[5000],
            volatility_30s_bps=volatility[30000],
            event_age_ms=self._age_ms(now, last_event),
            book_age_ms=liquidity.book_age_ms,
            trade_age_ms=(
                self._age_ms(now, trades[-1].exchange_event_time) if trades else None
            ),
            warmup_complete=(
                len(books) >= 2
                and bool(trades)
                and books[0].timestamp <= now - timedelta(milliseconds=250)
            ),
        )

    @staticmethod
    def _trade_flow(
        trades: tuple[MicrostructureEvent, ...],
        now: datetime,
        window_ms: int,
    ) -> TradeFlowWindow:
        cutoff = now - timedelta(milliseconds=window_ms)
        selected = tuple(
            trade for trade in trades if cutoff <= trade.exchange_event_time <= now
        )
        buy = sum(
            (
                trade.quantity
                for trade in selected
                if trade.aggressive_side is AggressiveSide.BUY and trade.quantity is not None
            ),
            ZERO,
        )
        sell = sum(
            (
                trade.quantity
                for trade in selected
                if trade.aggressive_side is AggressiveSide.SELL and trade.quantity is not None
            ),
            ZERO,
        )
        total = buy + sell
        notional = sum(
            (
                trade.price * trade.quantity
                for trade in selected
                if trade.price is not None and trade.quantity is not None
            ),
            ZERO,
        )
        return TradeFlowWindow(
            window_ms=window_ms,
            aggressive_buy_qty=buy,
            aggressive_sell_qty=sell,
            aggressive_trade_imbalance=(buy - sell) / total if total > ZERO else ZERO,
            trade_count=len(selected),
            quote_notional=notional,
        )

    def _ofi_sum(self, now: datetime, window_ms: int) -> Decimal:
        cutoff = now - timedelta(milliseconds=window_ms)
        return sum(
            (value for timestamp, value in self._ofi if cutoff <= timestamp <= now),
            ZERO,
        )

    @staticmethod
    def _ofi_increment(previous: _BookObservation, current: _BookObservation) -> Decimal:
        """Cont-style best-level OFI: bid contribution minus ask contribution."""

        bid = ZERO
        if current.best_bid >= previous.best_bid:
            bid += current.bid_quantity
        if current.best_bid <= previous.best_bid:
            bid -= previous.bid_quantity
        ask = ZERO
        if current.best_ask <= previous.best_ask:
            ask -= current.ask_quantity
        if current.best_ask >= previous.best_ask:
            ask += previous.ask_quantity
        return bid + ask

    @staticmethod
    def _momentum(
        books: tuple[_BookObservation, ...],
        now: datetime,
        window_ms: int,
    ) -> Decimal:
        cutoff = now - timedelta(milliseconds=window_ms)
        prior = tuple(item for item in books if item.timestamp <= cutoff)
        if not prior:
            return ZERO
        current = books[-1].mid
        baseline = prior[-1].mid
        return (current / baseline - Decimal("1")) * TEN_THOUSAND

    @staticmethod
    def _volatility(
        books: tuple[_BookObservation, ...],
        now: datetime,
        window_ms: int,
    ) -> Decimal:
        cutoff = now - timedelta(milliseconds=window_ms)
        selected = tuple(item for item in books if cutoff <= item.timestamp <= now)
        if len(selected) < 2:
            return ZERO
        returns = tuple(
            (selected[index].mid / selected[index - 1].mid - Decimal("1"))
            * TEN_THOUSAND
            for index in range(1, len(selected))
        )
        return sum((value * value for value in returns), ZERO).sqrt()

    @staticmethod
    def _age_ms(now: datetime, timestamp: datetime) -> Decimal:
        return max(ZERO, Decimal(str((now - timestamp).total_seconds() * 1000)))

    @staticmethod
    def _event_order(event: MicrostructureEvent) -> tuple[datetime, int, str]:
        return (event.exchange_event_time, event.receive_monotonic_ns, event.event_id)
