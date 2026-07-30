"""Typed, serializable domain objects used by every layer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path


class SignalDirection(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class MarketRegime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    UNKNOWN = "UNKNOWN"


class OrderStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"


type SerializedValue = (
    str | int | bool | None | list["SerializedValue"] | dict[str, "SerializedValue"]
)


def _require_decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")


def _require_positive(value: Decimal, field_name: str) -> None:
    _require_decimal(value, field_name)
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    exchange: str = "BINANCE"
    interval: str = "1m"
    close_time: datetime | None = None
    quote_volume: Decimal | None = None
    trades_count: int | None = None
    taker_buy_base_volume: Decimal | None = None
    taker_buy_quote_volume: Decimal | None = None
    is_closed: bool = True
    collected_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "timestamp")
        if self.close_time is not None:
            _require_aware(self.close_time, "close_time")
            if self.close_time < self.timestamp:
                raise ValueError("close_time must not precede timestamp")
        if self.collected_at is not None:
            _require_aware(self.collected_at, "collected_at")
        if not self.exchange or not self.interval:
            raise ValueError("exchange and interval are required")
        for name in ("open", "high", "low", "close"):
            _require_positive(getattr(self, name), name)
        _require_decimal(self.volume, "volume")
        if self.volume < 0:
            raise ValueError("volume must not be negative")
        for name in (
            "quote_volume",
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_decimal(value, name)
                if value < 0:
                    raise ValueError(f"{name} must not be negative")
        if self.trades_count is not None and self.trades_count < 0:
            raise ValueError("trades_count must not be negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle high/low do not contain open and close")
        if self.low > self.high:
            raise ValueError("candle low must not exceed high")

    @property
    def open_time(self) -> datetime:
        return self.timestamp


@dataclass(frozen=True, slots=True)
class MarketContext:
    symbol: str
    created_at: datetime
    candles: Sequence[Candle]
    latest_candle: Candle
    indicators: Mapping[str, Decimal]
    interval: str = "1m"

    def __post_init__(self) -> None:
        if not self.candles:
            raise ValueError("market context requires at least one candle")
        if self.latest_candle != self.candles[-1]:
            raise ValueError("latest_candle must be the last candle in candles")
        if self.latest_candle.symbol != self.symbol:
            raise ValueError("market context symbol must match candles")
        if self.latest_candle.interval != self.interval:
            raise ValueError("market context interval must match candles")
        for name, value in self.indicators.items():
            _require_decimal(value, f"indicator {name}")


@dataclass(frozen=True, slots=True)
class MarketSignal:
    signal_id: str
    symbol: str
    generated_at: datetime
    direction: SignalDirection
    regime: MarketRegime
    confidence: Decimal
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    suggested_quantity: Decimal
    rationale: str
    analyzer_name: str
    reason_code: str = "UNSPECIFIED"

    def __post_init__(self) -> None:
        _require_decimal(self.confidence, "confidence")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("confidence must be between 0 and 1")
        for name in ("entry_price", "stop_loss", "take_profit", "suggested_quantity"):
            _require_decimal(getattr(self, name), name)
        if self.direction is not SignalDirection.HOLD:
            for name in ("entry_price", "stop_loss", "take_profit", "suggested_quantity"):
                if getattr(self, name) <= 0:
                    raise ValueError(f"{name} must be positive for an actionable signal")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: str
    symbol: str
    direction: SignalDirection
    quantity: Decimal
    price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    created_at: datetime

    def __post_init__(self) -> None:
        if self.direction is SignalDirection.HOLD:
            raise ValueError("an order intent cannot have HOLD direction")
        for name in ("quantity", "price", "stop_loss", "take_profit"):
            _require_positive(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision_id: str
    signal_id: str
    decided_at: datetime
    approved: bool
    reason: str
    order_intent: OrderIntent | None
    reason_code: str = "UNSPECIFIED"

    def __post_init__(self) -> None:
        if self.approved and self.order_intent is None:
            raise ValueError("approved risk decision requires an order intent")
        if not self.approved and self.order_intent is not None:
            raise ValueError("rejected risk decision cannot contain an order intent")


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    order_id: str
    intent_id: str
    symbol: str
    direction: SignalDirection
    quantity: Decimal
    price: Decimal
    status: OrderStatus
    created_at: datetime
    reference_price: Decimal | None = None
    fee: Decimal = Decimal("0")
    slippage_cost: Decimal = Decimal("0")
    spread_cost: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _require_positive(self.quantity, "quantity")
        _require_positive(self.price, "price")
        for name in ("reference_price", "fee", "slippage_cost", "spread_cost"):
            value = getattr(self, name)
            if value is not None:
                _require_decimal(value, name)
                if value < 0:
                    raise ValueError(f"{name} must not be negative")


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    filled_at: datetime
    reference_price: Decimal | None = None
    slippage_cost: Decimal = Decimal("0")
    spread_cost: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name in ("quantity", "price"):
            _require_positive(getattr(self, name), name)
        _require_decimal(self.fee, "fee")
        if self.fee < 0:
            raise ValueError("fee must not be negative")
        for name in ("reference_price", "slippage_cost", "spread_cost"):
            value = getattr(self, name)
            if value is not None:
                _require_decimal(value, name)
                if value < 0:
                    raise ValueError(f"{name} must not be negative")


@dataclass(frozen=True, slots=True)
class Position:
    position_id: str
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    current_price: Decimal
    opened_at: datetime
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    initial_risk: Decimal | None = None
    entry_fee: Decimal = Decimal("0")
    partial_taken: bool = False

    def __post_init__(self) -> None:
        for name in ("quantity", "average_entry_price", "current_price"):
            _require_positive(getattr(self, name), name)
        for name in ("stop_loss", "take_profit", "initial_risk"):
            value = getattr(self, name)
            if value is not None:
                _require_positive(value, name)
        _require_decimal(self.entry_fee, "entry_fee")
        if self.entry_fee < 0:
            raise ValueError("entry_fee must not be negative")

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> Decimal:
        return (self.current_price - self.average_entry_price) * self.quantity


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    snapshot_id: str
    captured_at: datetime
    cash_balance: Decimal
    equity: Decimal
    day_start_equity: Decimal
    daily_loss: Decimal
    entries_today: int
    orders_today: int
    closed_trades_today: int
    positions: tuple[Position, ...]

    def __post_init__(self) -> None:
        for name in ("cash_balance", "equity", "day_start_equity", "daily_loss"):
            _require_decimal(getattr(self, name), name)
        if any(
            value < 0
            for value in (
                self.cash_balance,
                self.equity,
                self.day_start_equity,
                self.daily_loss,
            )
        ):
            raise ValueError("portfolio monetary values must not be negative")
        if any(
            value < 0
            for value in (self.entries_today, self.orders_today, self.closed_trades_today)
        ):
            raise ValueError("portfolio counters must not be negative")


@dataclass(frozen=True, slots=True)
class StrategyDecisionRecord:
    record_id: str
    analysis_time: datetime
    signal: MarketSignal
    context_candle_count: int
    indicators: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        if self.context_candle_count < 1:
            raise ValueError("context_candle_count must be positive")
        for name, value in self.indicators.items():
            _require_decimal(value, f"indicator {name}")


@dataclass(frozen=True, slots=True)
class StrategyDecisionTrace:
    timestamp: datetime
    symbol: str
    interval: str
    candle_index: int
    close_price: Decimal
    regime: MarketRegime
    short_ema: Decimal | None
    long_ema: Decimal | None
    ema_distance: Decimal | None
    atr: Decimal | None
    atr_relative: Decimal | None
    volume: Decimal | None
    average_volume: Decimal | None
    volume_ratio: Decimal | None
    risk_reward: Decimal | None
    signal_direction: SignalDirection
    strategy_reason_code: str
    risk_approved: bool | None
    risk_rejection_code: str | None
    execution_status: str
    execution_rejection_code: str | None
    position_open: bool
    pending_order: bool
    evaluation_segment: str | None = None
    fold_id: str | None = None
    parameter_set_id: str | None = None

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "timestamp")
        _require_positive(self.close_price, "close_price")
        if self.candle_index < 0:
            raise ValueError("candle_index must not be negative")
        for name in (
            "short_ema",
            "long_ema",
            "ema_distance",
            "atr",
            "atr_relative",
            "volume",
            "average_volume",
            "volume_ratio",
            "risk_reward",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_decimal(value, name)


def _encode(value: object) -> SerializedValue:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _encode(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported serialization type: {type(value).__name__}")


def serialize_model(model: object) -> dict[str, SerializedValue]:
    """Serialize a domain dataclass without converting Decimal to float."""

    encoded = _encode(model)
    if not isinstance(encoded, dict):
        raise TypeError("model must serialize to an object")
    return encoded
