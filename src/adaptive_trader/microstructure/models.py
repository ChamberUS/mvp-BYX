"""Immutable domain models for public intraday microstructure research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from adaptive_trader.domain.market import MarketType, PositionSide

ZERO = Decimal("0")
HUNDRED = Decimal("100")
TEN_THOUSAND = Decimal("10000")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _finite(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"{name} must be a finite Decimal")
    if positive and value <= ZERO:
        raise ValueError(f"{name} must be positive")


class MicrostructureStreamType(StrEnum):
    AGG_TRADE = "AGG_TRADE"
    BOOK_TICKER = "BOOK_TICKER"
    DEPTH_UPDATE = "DEPTH_UPDATE"
    MARK_PRICE = "MARK_PRICE"
    SNAPSHOT = "SNAPSHOT"
    CONNECTION_STATE = "CONNECTION_STATE"


class AggressiveSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderBookStatus(StrEnum):
    EMPTY = "EMPTY"
    BUFFERING = "BUFFERING"
    SYNCHRONIZED = "SYNCHRONIZED"
    INVALID = "INVALID"
    RESYNC_IN_PROGRESS = "RESYNC_IN_PROGRESS"


class OrderBookReason(StrEnum):
    SNAPSHOT_APPLIED = "SNAPSHOT_APPLIED"
    UPDATE_APPLIED = "UPDATE_APPLIED"
    STALE_UPDATE = "STALE_UPDATE"
    DUPLICATE_UPDATE = "DUPLICATE_UPDATE"
    ORDER_BOOK_DESYNC = "ORDER_BOOK_DESYNC"
    CROSSED_BOOK = "CROSSED_BOOK"


class LiquidityState(StrEnum):
    LIQUIDITY_OK = "LIQUIDITY_OK"
    LIQUIDITY_THIN = "LIQUIDITY_THIN"
    LIQUIDITY_UNSAFE = "LIQUIDITY_UNSAFE"


class AlphaDecisionStatus(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"
    NO_TRADE = "NO_TRADE"


class AlphaModelName(StrEnum):
    LONG_MICROSTRUCTURE_V0 = "LONG_MICROSTRUCTURE_V0"
    SHORT_MICROSTRUCTURE_V0 = "SHORT_MICROSTRUCTURE_V0"
    COORDINATOR = "COORDINATOR"


class NoTradeReason(StrEnum):
    BOOK_NOT_SYNCHRONIZED = "BOOK_NOT_SYNCHRONIZED"
    MARKET_DATA_STALE = "MARKET_DATA_STALE"
    INVALID_SPREAD = "INVALID_SPREAD"
    DEPTH_INSUFFICIENT = "DEPTH_INSUFFICIENT"
    EVENT_GAP = "EVENT_GAP"
    RESYNC_IN_PROGRESS = "RESYNC_IN_PROGRESS"
    FEATURE_WARMUP = "FEATURE_WARMUP"
    NO_TRADE_ALLOWED = "NO_TRADE_ALLOWED"
    MARKET_STATE_UNKNOWN = "MARKET_STATE_UNKNOWN"
    REPLAY_INCONSISTENT = "REPLAY_INCONSISTENT"
    NO_TRADE_CONFLICT = "NO_TRADE_CONFLICT"


class CalibrationStatus(StrEnum):
    CALIBRATION_REQUIRED = "CALIBRATION_REQUIRED"


class OrderUrgency(StrEnum):
    PASSIVE = "PASSIVE"
    NORMAL = "NORMAL"
    URGENT = "URGENT"


class MakerPreference(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"
    NONE = "NONE"


class ProfitExtensionState(StrEnum):
    DISARMED = "DISARMED"
    ARMED = "ARMED"
    EXTENDING = "EXTENDING"
    REVERSAL_PENDING = "REVERSAL_PENDING"
    EXIT_REQUESTED = "EXIT_REQUESTED"
    FAILSAFE = "FAILSAFE"


@dataclass(frozen=True, slots=True)
class DepthLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        _finite(self.price, "price", positive=True)
        _finite(self.quantity, "quantity")
        if self.quantity < ZERO:
            raise ValueError("quantity must be non-negative")

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity


@dataclass(frozen=True, slots=True)
class LiquidityExecutionEstimate:
    side: PositionSide
    required_quantity: Decimal
    expected_vwap: Decimal | None
    expected_slippage_bps: Decimal | None
    percent_of_visible_depth: Decimal | None
    executable_notional: Decimal | None
    spread_bps: Decimal
    top_5_notional: Decimal
    top_10_notional: Decimal
    top_20_notional: Decimal
    book_age_ms: Decimal

    def __post_init__(self) -> None:
        _finite(self.required_quantity, "required_quantity", positive=True)
        for name in ("spread_bps", "top_5_notional", "top_10_notional", "top_20_notional"):
            _finite(getattr(self, name), name)
        _finite(self.book_age_ms, "book_age_ms")
        for name in (
            "expected_vwap",
            "expected_slippage_bps",
            "percent_of_visible_depth",
            "executable_notional",
        ):
            value = getattr(self, name)
            if value is not None:
                _finite(value, name)


@dataclass(frozen=True, slots=True)
class MicrostructureEvent:
    event_id: str
    exchange: str
    market_type: MarketType
    symbol: str
    stream_type: MicrostructureStreamType
    exchange_event_time: datetime
    exchange_transaction_time: datetime | None
    receive_wall_time: datetime
    receive_monotonic_ns: int
    sequence_first: int | None
    sequence_last: int | None
    sequence_previous: int | None
    raw_payload_hash: str
    raw_payload_json: str
    trade_id: int | None = None
    price: Decimal | None = None
    quantity: Decimal | None = None
    buyer_is_maker: bool | None = None
    aggressive_side: AggressiveSide | None = None
    best_bid: Decimal | None = None
    best_bid_quantity: Decimal | None = None
    best_ask: Decimal | None = None
    best_ask_quantity: Decimal | None = None
    bids: tuple[DepthLevel, ...] = ()
    asks: tuple[DepthLevel, ...] = ()
    mark_price: Decimal | None = None
    connection_state: str | None = None
    connection_id: str = "legacy-public-1"
    connection_sequence: int = 0
    first_trade_id: int | None = None
    last_trade_id: int | None = None
    index_price: Decimal | None = None
    funding_rate: Decimal | None = None
    next_funding_time: datetime | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.exchange or not self.symbol or not self.connection_id:
            raise ValueError("event_id, exchange, symbol and connection_id are required")
        if self.symbol != self.symbol.upper() or not self.symbol.isalnum():
            raise ValueError("symbol must be uppercase alphanumeric")
        _aware(self.exchange_event_time, "exchange_event_time")
        _aware(self.receive_wall_time, "receive_wall_time")
        if self.exchange_transaction_time is not None:
            _aware(self.exchange_transaction_time, "exchange_transaction_time")
        if self.receive_monotonic_ns < 0:
            raise ValueError("receive_monotonic_ns must be non-negative")
        for name in (
            "sequence_first",
            "sequence_last",
            "sequence_previous",
            "trade_id",
            "first_trade_id",
            "last_trade_id",
            "connection_sequence",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if (
            self.sequence_first is not None
            and self.sequence_last is not None
            and self.sequence_first > self.sequence_last
        ):
            raise ValueError("sequence_first must not exceed sequence_last")
        if len(self.raw_payload_hash) != 64:
            raise ValueError("raw_payload_hash must be a SHA-256 hex digest")
        for name in (
            "price",
            "quantity",
            "best_bid",
            "best_bid_quantity",
            "best_ask",
            "best_ask_quantity",
            "mark_price",
            "index_price",
        ):
            value = getattr(self, name)
            if value is not None:
                _finite(value, name, positive=True)
        if self.funding_rate is not None:
            _finite(self.funding_rate, "funding_rate")
        if self.next_funding_time is not None:
            _aware(self.next_funding_time, "next_funding_time")
        if (
            self.first_trade_id is not None
            and self.last_trade_id is not None
            and self.first_trade_id > self.last_trade_id
        ):
            raise ValueError("first_trade_id must not exceed last_trade_id")
        if self.stream_type is MicrostructureStreamType.AGG_TRADE:
            required = (
                self.trade_id,
                self.price,
                self.quantity,
                self.buyer_is_maker,
                self.aggressive_side,
            )
            if any(value is None for value in required):
                raise ValueError("aggregate trade event is incomplete")
            expected = (
                AggressiveSide.SELL if self.buyer_is_maker else AggressiveSide.BUY
            )
            if self.aggressive_side is not expected:
                raise ValueError("aggressive side conflicts with buyer_is_maker")


@dataclass(frozen=True, slots=True)
class LiquiditySnapshot:
    timestamp: datetime
    market_type: MarketType
    symbol: str
    best_bid: Decimal
    best_ask: Decimal
    bid_quantity: Decimal
    ask_quantity: Decimal
    mid_price: Decimal
    spread: Decimal
    spread_bps: Decimal
    top_5_bid_notional: Decimal
    top_5_ask_notional: Decimal
    top_10_bid_notional: Decimal
    top_10_ask_notional: Decimal
    top_20_bid_notional: Decimal
    top_20_ask_notional: Decimal
    depth_imbalance_5: Decimal
    depth_imbalance_10: Decimal
    depth_imbalance_20: Decimal
    book_age_ms: Decimal
    synchronized: bool
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]

    def __post_init__(self) -> None:
        _aware(self.timestamp, "timestamp")
        for name in (
            "best_bid",
            "best_ask",
            "bid_quantity",
            "ask_quantity",
            "mid_price",
            "spread",
            "spread_bps",
            "top_5_bid_notional",
            "top_5_ask_notional",
            "top_10_bid_notional",
            "top_10_ask_notional",
            "top_20_bid_notional",
            "top_20_ask_notional",
            "book_age_ms",
        ):
            _finite(getattr(self, name), name)
        for name in ("depth_imbalance_5", "depth_imbalance_10", "depth_imbalance_20"):
            value = getattr(self, name)
            _finite(value, name)
            if not Decimal("-1") <= value <= Decimal("1"):
                raise ValueError(f"{name} must be in [-1, 1]")

    def executable_buy_price(self, quantity: Decimal) -> Decimal | None:
        return self._vwap(self.asks, quantity)

    def executable_sell_price(self, quantity: Decimal) -> Decimal | None:
        return self._vwap(self.bids, quantity)

    def slippage_bps(self, side: PositionSide, quantity: Decimal) -> Decimal | None:
        executable = (
            self.executable_buy_price(quantity)
            if side is PositionSide.LONG
            else self.executable_sell_price(quantity)
        )
        if executable is None:
            return None
        return (
            (executable - self.best_ask) / self.best_ask * TEN_THOUSAND
            if side is PositionSide.LONG
            else (self.best_bid - executable) / self.best_bid * TEN_THOUSAND
        )

    def available_notional_within_bps(
        self,
        side: PositionSide,
        basis_points: Decimal,
    ) -> Decimal:
        if basis_points < ZERO:
            raise ValueError("basis_points must be non-negative")
        levels = self.asks if side is PositionSide.LONG else self.bids
        reference = self.best_ask if side is PositionSide.LONG else self.best_bid
        limit = (
            reference * (Decimal("1") + basis_points / TEN_THOUSAND)
            if side is PositionSide.LONG
            else reference * (Decimal("1") - basis_points / TEN_THOUSAND)
        )
        return sum(
            (
                level.notional
                for level in levels
                if (level.price <= limit if side is PositionSide.LONG else level.price >= limit)
            ),
            ZERO,
        )

    def available_notional_within_1bp(self, side: PositionSide) -> Decimal:
        return self.available_notional_within_bps(side, Decimal("1"))

    def available_notional_within_2bp(self, side: PositionSide) -> Decimal:
        return self.available_notional_within_bps(side, Decimal("2"))

    def available_notional_within_5bp(self, side: PositionSide) -> Decimal:
        return self.available_notional_within_bps(side, Decimal("5"))

    def visible_quantity(self, side: PositionSide) -> Decimal:
        levels = self.asks if side is PositionSide.LONG else self.bids
        return sum((level.quantity for level in levels), ZERO)

    def execution_estimate(
        self,
        side: PositionSide,
        required_quantity: Decimal,
    ) -> LiquidityExecutionEstimate:
        _finite(required_quantity, "required_quantity", positive=True)
        visible = self.visible_quantity(side)
        expected = (
            self.executable_buy_price(required_quantity)
            if side is PositionSide.LONG
            else self.executable_sell_price(required_quantity)
        )
        return LiquidityExecutionEstimate(
            side=side,
            required_quantity=required_quantity,
            expected_vwap=expected,
            expected_slippage_bps=self.slippage_bps(side, required_quantity),
            percent_of_visible_depth=(
                required_quantity / visible * HUNDRED if visible > ZERO else None
            ),
            executable_notional=(expected * required_quantity if expected is not None else None),
            spread_bps=self.spread_bps,
            top_5_notional=(
                self.top_5_ask_notional if side is PositionSide.LONG else self.top_5_bid_notional
            ),
            top_10_notional=(
                self.top_10_ask_notional
                if side is PositionSide.LONG
                else self.top_10_bid_notional
            ),
            top_20_notional=(
                self.top_20_ask_notional
                if side is PositionSide.LONG
                else self.top_20_bid_notional
            ),
            book_age_ms=self.book_age_ms,
        )

    @staticmethod
    def _vwap(levels: tuple[DepthLevel, ...], quantity: Decimal) -> Decimal | None:
        _finite(quantity, "quantity", positive=True)
        remaining = quantity
        notional = ZERO
        for level in levels:
            filled = min(remaining, level.quantity)
            notional += filled * level.price
            remaining -= filled
            if remaining == ZERO:
                return notional / quantity
        return None


@dataclass(frozen=True, slots=True)
class IntradayAlphaDecision:
    decision_id: str
    timestamp: datetime
    market: MarketType
    symbol: str
    model: AlphaModelName
    side: PositionSide | None
    status: AlphaDecisionStatus
    confidence_inputs: tuple[tuple[str, Decimal | bool | str], ...]
    feature_snapshot: object
    reason_codes: tuple[str, ...]
    liquidity_snapshot: LiquiditySnapshot
    expected_execution_side: str | None
    no_trade_reason: NoTradeReason | None

    def __post_init__(self) -> None:
        _aware(self.timestamp, "timestamp")
        if not self.decision_id or not self.symbol:
            raise ValueError("decision_id and symbol are required")
        if self.market is MarketType.SPOT and self.side is PositionSide.SHORT:
            raise ValueError("Spot alpha decision cannot be short")
        if self.status is AlphaDecisionStatus.NO_TRADE and self.no_trade_reason is None:
            raise ValueError("NO_TRADE decision requires a reason")
        if self.status in {AlphaDecisionStatus.LONG, AlphaDecisionStatus.SHORT}:
            if self.side is None or self.expected_execution_side is None:
                raise ValueError("actionable alpha decision requires side and execution side")


@dataclass(frozen=True, slots=True)
class IntradayOrderIntent:
    side: PositionSide
    quantity: Decimal
    reference_price: Decimal
    limit_price: Decimal | None
    urgency: OrderUrgency
    maker_preference: MakerPreference
    maximum_slippage_bps: Decimal
    expiry_ms: int
    reason: str

    def __post_init__(self) -> None:
        _finite(self.quantity, "quantity", positive=True)
        _finite(self.reference_price, "reference_price", positive=True)
        if self.limit_price is not None:
            _finite(self.limit_price, "limit_price", positive=True)
        _finite(self.maximum_slippage_bps, "maximum_slippage_bps")
        if self.maximum_slippage_bps < ZERO or self.expiry_ms <= 0 or not self.reason:
            raise ValueError("intent slippage, expiry or reason is invalid")


@dataclass(frozen=True, slots=True)
class IntradayRiskConfig:
    risk_per_trade_percent: Decimal
    maximum_daily_loss_percent: Decimal
    maximum_weekly_loss_percent: Decimal
    maximum_consecutive_losses: int
    cooldown_ms: int
    maximum_open_positions: int
    maximum_orders_per_minute: int
    maximum_notional: Decimal
    maximum_visible_depth_fraction: Decimal
    maximum_slippage_bps: Decimal
    kill_switch_enabled: bool
    leverage: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        for name in (
            "risk_per_trade_percent",
            "maximum_daily_loss_percent",
            "maximum_weekly_loss_percent",
            "maximum_notional",
            "maximum_visible_depth_fraction",
            "maximum_slippage_bps",
            "leverage",
        ):
            _finite(getattr(self, name), name, positive=True)
        if self.leverage != Decimal("1"):
            raise ValueError("intraday research leverage is locked to 1x")
        if self.maximum_visible_depth_fraction > Decimal("1"):
            raise ValueError("maximum_visible_depth_fraction must not exceed 1")
        if min(
            self.maximum_consecutive_losses,
            self.cooldown_ms,
            self.maximum_open_positions,
            self.maximum_orders_per_minute,
        ) <= 0:
            raise ValueError("intraday integer risk limits must be positive")


@dataclass(frozen=True, slots=True)
class ExecutionAnalysis:
    expected_edge_bps: Decimal
    realized_edge_bps: Decimal | None
    spread_cost_bps: Decimal
    fee_cost_bps: Decimal
    slippage_bps: Decimal
    total_cost_bps: Decimal
    markout_bps: Decimal | None
    adverse_selection_bps: Decimal | None
    fill_latency_ms: Decimal | None
    decision_latency_ms: Decimal
    book_age_at_decision_ms: Decimal
    post_event_only: bool = True

    def __post_init__(self) -> None:
        for name in (
            "expected_edge_bps",
            "spread_cost_bps",
            "fee_cost_bps",
            "slippage_bps",
            "total_cost_bps",
            "decision_latency_ms",
            "book_age_at_decision_ms",
        ):
            _finite(getattr(self, name), name)
        for name in (
            "realized_edge_bps",
            "markout_bps",
            "adverse_selection_bps",
            "fill_latency_ms",
        ):
            value = getattr(self, name)
            if value is not None:
                _finite(value, name)
        if not self.post_event_only:
            raise ValueError("execution analysis must remain post-event only")
