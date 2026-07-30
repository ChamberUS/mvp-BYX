"""Typed USD-M Futures research models with Decimal-only accounting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from adaptive_trader.domain.market import (
    ContractType,
    MarginMode,
    MarketType,
    PositionSide,
    TradingMode,
)
from adaptive_trader.domain.models import Candle, MarketRegime, SerializedValue


class FuturesPriceSource(StrEnum):
    FUTURES_KLINE = "FUTURES_KLINE"
    MARK_PRICE = "MARK_PRICE"
    SPOT_PROXY_FOR_TESTS_ONLY = "SPOT_PROXY_FOR_TESTS_ONLY"


class FundingMissingPolicy(StrEnum):
    FAIL = "FAIL"
    WARN_AND_SKIP = "WARN_AND_SKIP"
    DISABLE_EXPLICITLY = "DISABLE_EXPLICITLY"


class LiquidationPriceSource(StrEnum):
    MARK_PRICE = "MARK_PRICE"


class FuturesIntrabarPolicy(StrEnum):
    LIQUIDATION_FIRST = "LIQUIDATION_FIRST"


class FuturesSignalDirection(StrEnum):
    ENTER_LONG = "ENTER_LONG"
    EXIT_LONG = "EXIT_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT_SHORT = "EXIT_SHORT"
    HOLD = "HOLD"


class FuturesExitReason(StrEnum):
    LIQUIDATION = "LIQUIDATION"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TIME_EXIT = "TIME_EXIT"
    FORCED_END = "FORCED_END"
    MANUAL_SIMULATED_EXIT = "MANUAL_SIMULATED_EXIT"


class FuturesRiskReasonCode(StrEnum):
    APPROVED = "APPROVED"
    LEVERAGE_LIMIT = "LEVERAGE_LIMIT"
    MARGIN_INSUFFICIENT = "MARGIN_INSUFFICIENT"
    MAINTENANCE_MARGIN_UNSAFE = "MAINTENANCE_MARGIN_UNSAFE"
    STOP_REQUIRED = "STOP_REQUIRED"
    SHORT_NOT_ALLOWED = "SHORT_NOT_ALLOWED"
    LONG_NOT_ALLOWED = "LONG_NOT_ALLOWED"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    POST_LIQUIDATION_COOLDOWN = "POST_LIQUIDATION_COOLDOWN"
    NOTIONAL_LIMIT = "NOTIONAL_LIMIT"
    FUNDING_DATA_MISSING = "FUNDING_DATA_MISSING"
    POSITION_ALREADY_OPEN = "POSITION_ALREADY_OPEN"
    MINIMUM_BALANCE = "MINIMUM_BALANCE"
    KILL_STATE = "KILL_STATE"


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _decimal(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class FuturesCandle:
    exchange: str
    market_type: MarketType
    contract_type: ContractType
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int
    is_closed: bool
    collected_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.market_type is not MarketType.USD_M_FUTURES:
            raise ValueError("futures candle requires USD_M_FUTURES")
        if self.contract_type is not ContractType.PERPETUAL:
            raise ValueError("futures candle requires PERPETUAL contract")
        _aware(self.open_time, "open_time")
        _aware(self.close_time, "close_time")
        if self.collected_at is not None:
            _aware(self.collected_at, "collected_at")
        if self.close_time < self.open_time:
            raise ValueError("close_time must not precede open_time")
        for name in ("open", "high", "low", "close"):
            _decimal(getattr(self, name), name, positive=True)
        for name in ("volume", "quote_volume"):
            _decimal(getattr(self, name), name)
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.trade_count < 0:
            raise ValueError("trade_count must not be negative")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("futures candle OHLC is inconsistent")

    def as_indicator_candle(self) -> Candle:
        return Candle(
            exchange=self.exchange,
            symbol=self.symbol,
            interval=self.interval,
            timestamp=self.open_time,
            close_time=self.close_time,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            quote_volume=self.quote_volume,
            trades_count=self.trade_count,
            is_closed=self.is_closed,
            collected_at=self.collected_at,
        )


@dataclass(frozen=True, slots=True)
class MarkPriceCandle:
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    index_price: Decimal | None = None
    estimated_settle_price: Decimal | None = None
    is_closed: bool = True
    collected_at: datetime | None = None

    def __post_init__(self) -> None:
        _aware(self.open_time, "open_time")
        _aware(self.close_time, "close_time")
        if self.collected_at is not None:
            _aware(self.collected_at, "collected_at")
        for name in ("open", "high", "low", "close"):
            _decimal(getattr(self, name), name, positive=True)
        for name in ("index_price", "estimated_settle_price"):
            value = getattr(self, name)
            if value is not None:
                _decimal(value, name, positive=True)
        if self.close_time < self.open_time:
            raise ValueError("close_time must not precede open_time")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("mark price OHLC is inconsistent")

    @property
    def timestamp(self) -> datetime:
        return self.open_time

    @property
    def mark_price(self) -> Decimal:
        return self.close


@dataclass(frozen=True, slots=True)
class FundingRate:
    symbol: str
    funding_time: datetime
    funding_rate: Decimal
    mark_price: Decimal | None = None

    def __post_init__(self) -> None:
        _aware(self.funding_time, "funding_time")
        _decimal(self.funding_rate, "funding_rate")
        if self.mark_price is not None:
            _decimal(self.mark_price, "mark_price", positive=True)


@dataclass(frozen=True, slots=True)
class FuturesBacktestConfig:
    market_type: MarketType = MarketType.USD_M_FUTURES
    contract_type: ContractType = ContractType.PERPETUAL
    margin_mode: MarginMode = MarginMode.ISOLATED
    trading_mode: TradingMode = TradingMode.FUTURES_LONG_SHORT
    initial_balance: Decimal = Decimal("10000")
    leverage: Decimal = Decimal("1")
    maximum_leverage: Decimal = Decimal("3")
    maximum_position_notional_percent: Decimal = Decimal("25")
    maintenance_margin_rate: Decimal = Decimal("0.005")
    liquidation_fee_rate: Decimal = Decimal("0.005")
    maker_fee_bps: Decimal = Decimal("2")
    taker_fee_bps: Decimal = Decimal("5")
    spread_bps: Decimal = Decimal("2")
    slippage_bps: Decimal = Decimal("5")
    funding_enabled: bool = True
    funding_missing_policy: FundingMissingPolicy = FundingMissingPolicy.FAIL
    liquidation_price_source: LiquidationPriceSource = LiquidationPriceSource.MARK_PRICE
    force_close_at_end: bool = True
    latency_candles: int = 1
    intrabar_policy: FuturesIntrabarPolicy = FuturesIntrabarPolicy.LIQUIDATION_FIRST
    stop_loss_required: bool = True
    margin_buffer_percent: Decimal = Decimal("1")
    maximum_daily_loss_percent: Decimal = Decimal("1")
    maximum_entries_per_day: int = 5
    post_liquidation_cooldown_candles: int = 24
    minimum_wallet_balance: Decimal = Decimal("1")
    risk_per_trade_percent: Decimal = Decimal("1")
    symbol: str = "ETHUSDT"
    interval: str = "1h"
    warmup_candles: int = 100
    short_ema_period: int = 20
    long_ema_period: int = 50
    atr_period: int = 14
    volume_period: int = 20
    minimum_volume_ratio: Decimal = Decimal("1")
    maximum_atr_relative: Decimal = Decimal("0.05")
    stop_atr_multiple: Decimal = Decimal("2")
    target_r_multiple: Decimal = Decimal("2")
    time_exit_candles: int | None = None
    price_source: FuturesPriceSource = FuturesPriceSource.FUTURES_KLINE

    def __post_init__(self) -> None:
        if self.market_type is not MarketType.USD_M_FUTURES:
            raise ValueError("FuturesBacktestConfig requires USD_M_FUTURES")
        if self.contract_type is not ContractType.PERPETUAL:
            raise ValueError("only perpetual contracts are supported")
        if self.margin_mode is not MarginMode.ISOLATED:
            raise ValueError("only ISOLATED margin is supported")
        if self.trading_mode not in {
            TradingMode.FUTURES_LONG_ONLY,
            TradingMode.FUTURES_SHORT_ONLY,
            TradingMode.FUTURES_LONG_SHORT,
        }:
            raise ValueError("futures config requires a futures trading mode")
        for name in (
            "initial_balance",
            "leverage",
            "maximum_leverage",
            "maximum_position_notional_percent",
            "maintenance_margin_rate",
            "liquidation_fee_rate",
            "maker_fee_bps",
            "taker_fee_bps",
            "spread_bps",
            "slippage_bps",
            "margin_buffer_percent",
            "maximum_daily_loss_percent",
            "minimum_wallet_balance",
            "risk_per_trade_percent",
            "minimum_volume_ratio",
            "maximum_atr_relative",
            "stop_atr_multiple",
            "target_r_multiple",
        ):
            _decimal(getattr(self, name), name)
        if self.initial_balance <= 0 or self.minimum_wallet_balance < 0:
            raise ValueError("wallet balances must be valid")
        if self.leverage < 1 or self.maximum_leverage < 1:
            raise ValueError("leverage must be at least 1")
        if self.maximum_leverage > Decimal("3") or self.leverage > self.maximum_leverage:
            raise ValueError("leverage cannot exceed the sprint maximum of 3")
        if not Decimal("0") < self.maximum_position_notional_percent <= Decimal("100"):
            raise ValueError("maximum_position_notional_percent must be in (0, 100]")
        for name in (
            "maintenance_margin_rate",
            "liquidation_fee_rate",
        ):
            if not Decimal("0") <= getattr(self, name) < Decimal("1"):
                raise ValueError(f"{name} must be in [0, 1)")
        for name in (
            "maker_fee_bps",
            "taker_fee_bps",
            "spread_bps",
            "slippage_bps",
            "margin_buffer_percent",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if not self.stop_loss_required:
            raise ValueError("stop_loss_required must remain true")
        if self.liquidation_price_source is not LiquidationPriceSource.MARK_PRICE:
            raise ValueError("liquidation must use mark price")
        if self.intrabar_policy is not FuturesIntrabarPolicy.LIQUIDATION_FIRST:
            raise ValueError("liquidation must have intrabar priority")
        if not self.funding_enabled and (
            self.funding_missing_policy is not FundingMissingPolicy.DISABLE_EXPLICITLY
        ):
            raise ValueError("disabled funding must be explicit")
        if self.funding_enabled and (
            self.funding_missing_policy is FundingMissingPolicy.DISABLE_EXPLICITLY
        ):
            raise ValueError("DISABLE_EXPLICITLY requires funding_enabled=false")
        for name in (
            "latency_candles",
            "maximum_entries_per_day",
            "post_liquidation_cooldown_candles",
            "warmup_candles",
            "short_ema_period",
            "long_ema_period",
            "atr_period",
            "volume_period",
        ):
            minimum = 0 if name == "post_liquidation_cooldown_candles" else 1
            if getattr(self, name) < minimum:
                raise ValueError(f"{name} is below its minimum")
        if self.long_ema_period <= self.short_ema_period:
            raise ValueError("long_ema_period must exceed short_ema_period")
        if self.time_exit_candles is not None and self.time_exit_candles < 1:
            raise ValueError("time_exit_candles must be positive")
        if not self.symbol or self.symbol != self.symbol.upper() or not self.symbol.isalnum():
            raise ValueError("symbol must be uppercase alphanumeric")

    def as_dict(self) -> dict[str, SerializedValue]:
        from adaptive_trader.domain.models import serialize_model

        return serialize_model(self)


@dataclass(frozen=True, slots=True)
class FuturesSignal:
    signal_id: str
    symbol: str
    generated_at: datetime
    direction: FuturesSignalDirection
    regime: MarketRegime
    entry_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    rationale: str
    reason_code: str

    def __post_init__(self) -> None:
        _aware(self.generated_at, "generated_at")
        _decimal(self.entry_price, "entry_price", positive=True)
        for name in ("stop_loss", "take_profit"):
            value = getattr(self, name)
            if value is not None:
                _decimal(value, name, positive=True)


@dataclass(frozen=True, slots=True)
class FuturesOrderIntent:
    intent_id: str
    signal_id: str
    symbol: str
    side: PositionSide
    quantity: Decimal
    reference_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    leverage: Decimal
    created_at: datetime

    def __post_init__(self) -> None:
        _aware(self.created_at, "created_at")
        for name in (
            "quantity",
            "reference_price",
            "stop_loss",
            "take_profit",
            "leverage",
        ):
            _decimal(getattr(self, name), name, positive=True)


@dataclass(frozen=True, slots=True)
class FuturesRiskDecision:
    approved: bool
    reason_code: FuturesRiskReasonCode
    reason: str
    intent: FuturesOrderIntent | None

    def __post_init__(self) -> None:
        if self.approved != (self.intent is not None):
            raise ValueError("approved decisions require exactly one order intent")


@dataclass(frozen=True, slots=True)
class FuturesPortfolioState:
    wallet_balance: Decimal
    day_start_equity: Decimal
    entries_today: int
    daily_loss: Decimal
    position_open: bool
    candles_since_liquidation: int | None = None
    liquidated_today: bool = False
    kill_state: bool = False

    def __post_init__(self) -> None:
        for name in ("wallet_balance", "day_start_equity", "daily_loss"):
            _decimal(getattr(self, name), name)


@dataclass(slots=True)
class FuturesPosition:
    position_id: str
    symbol: str
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    notional: Decimal
    leverage: Decimal
    isolated_margin: Decimal
    free_balance_after_entry: Decimal
    maintenance_margin: Decimal
    liquidation_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    accumulated_funding: Decimal
    entry_fee: Decimal
    opened_at: datetime
    stop_loss: Decimal
    take_profit: Decimal
    initial_risk: Decimal
    holding_candles: int = 0

    def __post_init__(self) -> None:
        _aware(self.opened_at, "opened_at")
        for name in (
            "quantity",
            "entry_price",
            "mark_price",
            "notional",
            "leverage",
            "isolated_margin",
            "stop_loss",
            "take_profit",
            "initial_risk",
        ):
            _decimal(getattr(self, name), name, positive=True)
        for name in (
            "free_balance_after_entry",
            "maintenance_margin",
            "unrealized_pnl",
            "realized_pnl",
            "accumulated_funding",
            "entry_fee",
            "liquidation_price",
        ):
            _decimal(getattr(self, name), name)
        if self.liquidation_price < 0:
            raise ValueError("liquidation_price must not be negative")
        if self.holding_candles < 0:
            raise ValueError("holding_candles must not be negative")


@dataclass(frozen=True, slots=True)
class FuturesTrade:
    trade_id: str
    symbol: str
    side: PositionSide
    quantity: Decimal
    leverage: Decimal
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    entry_notional: Decimal
    initial_margin: Decimal
    free_balance_after_entry: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    trading_fees: Decimal
    liquidation_fee: Decimal
    funding_paid: Decimal
    funding_received: Decimal
    net_funding: Decimal
    exit_reason: FuturesExitReason
    holding_candles: int
    intrabar_ambiguous: bool


@dataclass(frozen=True, slots=True)
class FuturesMetrics:
    initial_wallet: Decimal
    final_wallet: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    long_pnl: Decimal
    short_pnl: Decimal
    funding_paid: Decimal
    funding_received: Decimal
    net_funding: Decimal
    funding_event_count: int
    trading_fees: Decimal
    liquidation_fees: Decimal
    liquidation_count: int
    trade_count: int
    long_trade_count: int
    short_trade_count: int
    long_win_rate: Decimal | None
    short_win_rate: Decimal | None
    average_margin_utilization: Decimal
    maximum_margin_utilization: Decimal
    average_effective_leverage: Decimal
    maximum_effective_leverage: Decimal
    maximum_position_notional: Decimal
    average_initial_margin: Decimal
    minimum_free_balance: Decimal
    return_on_wallet: Decimal
    return_on_notional: Decimal
    maximum_drawdown: Decimal
    minimum_margin_ratio: Decimal | None
    margin_call_count: int
    bankrupt: bool
    depleted: bool
    exposure_long_percent: Decimal
    exposure_short_percent: Decimal
    fees_as_percent_of_margin: Decimal
    funding_as_percent_of_margin: Decimal


@dataclass(frozen=True, slots=True)
class FuturesDecisionTrace:
    timestamp: datetime
    candle_index: int
    signal: FuturesSignalDirection
    reason_code: str
    risk_reason_code: FuturesRiskReasonCode | None
    position_side: PositionSide | None
    mark_price: Decimal


@dataclass(frozen=True, slots=True)
class FuturesBacktestResult:
    report_version: str
    strategy_version: str
    market_type: MarketType
    contract_type: ContractType
    trading_mode: TradingMode
    leverage: Decimal
    symbol: str
    interval: str
    start_time: datetime
    end_time: datetime
    input_candle_count: int
    warmup_candle_count: int
    evaluated_candle_count: int
    metrics: FuturesMetrics
    trades: tuple[FuturesTrade, ...]
    warnings: tuple[str, ...]
    equity_curve: tuple[Decimal, ...]
    margin_utilization_curve: tuple[Decimal, ...]
    effective_leverage_curve: tuple[Decimal, ...]
    decision_traces: tuple[FuturesDecisionTrace, ...] = ()
    metadata: dict[str, SerializedValue] = field(default_factory=dict)
