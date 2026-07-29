"""Typed, serializable domain objects used by every layer."""

from __future__ import annotations

from collections.abc import Mapping
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


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        for name in ("open", "high", "low", "close"):
            _require_positive(getattr(self, name), name)
        _require_decimal(self.volume, "volume")
        if self.volume < 0:
            raise ValueError("volume must not be negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle high/low do not contain open and close")
        if self.low > self.high:
            raise ValueError("candle low must not exceed high")


@dataclass(frozen=True, slots=True)
class MarketContext:
    symbol: str
    created_at: datetime
    candles: tuple[Candle, ...]
    latest_candle: Candle
    indicators: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        if not self.candles:
            raise ValueError("market context requires at least one candle")
        if self.latest_candle != self.candles[-1]:
            raise ValueError("latest_candle must be the last candle in candles")
        if self.latest_candle.symbol != self.symbol:
            raise ValueError("market context symbol must match candles")
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

    def __post_init__(self) -> None:
        _require_positive(self.quantity, "quantity")
        _require_positive(self.price, "price")


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    filled_at: datetime

    def __post_init__(self) -> None:
        for name in ("quantity", "price"):
            _require_positive(getattr(self, name), name)
        _require_decimal(self.fee, "fee")
        if self.fee < 0:
            raise ValueError("fee must not be negative")


@dataclass(frozen=True, slots=True)
class Position:
    position_id: str
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    current_price: Decimal
    opened_at: datetime

    def __post_init__(self) -> None:
        for name in ("quantity", "average_entry_price", "current_price"):
            _require_positive(getattr(self, name), name)

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
    daily_loss: Decimal
    trades_today: int
    positions: tuple[Position, ...]

    def __post_init__(self) -> None:
        for name in ("cash_balance", "equity", "daily_loss"):
            _require_decimal(getattr(self, name), name)
        if self.cash_balance < 0 or self.equity < 0 or self.daily_loss < 0:
            raise ValueError("portfolio monetary values must not be negative")
        if self.trades_today < 0:
            raise ValueError("trades_today must not be negative")


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
