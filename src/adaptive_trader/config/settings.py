"""Validated, secret-free configuration for research and paper trading."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from adaptive_trader.domain.market import ContractType, MarginMode, MarketType, TradingMode
from adaptive_trader.domain.models import SerializedValue, serialize_model


class ConfigError(ValueError):
    """Raised when configuration violates a safety invariant."""


def _decimal(value: str, name: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ConfigError(f"{name} must be a valid Decimal") from exc
    if not result.is_finite():
        raise ConfigError(f"{name} must be finite")
    return result


def _enum_value[EnumValue: StrEnum](
    enum_type: type[EnumValue],
    value: str,
    name: str,
) -> EnumValue:
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ConfigError(f"{name} has an unsupported value") from exc


@dataclass(frozen=True, slots=True)
class TradingConfig:
    symbol: str = "ETHUSDT"
    market: MarketType = MarketType.SPOT
    contract_type: ContractType = ContractType.NONE
    margin_mode: MarginMode = MarginMode.NONE
    trading_mode: TradingMode = TradingMode.SPOT_LONG_ONLY
    initial_balance: Decimal = Decimal("10000")
    maximum_open_positions: int = 1
    maximum_position_percent: Decimal = Decimal("5")
    maximum_daily_loss_percent: Decimal = Decimal("1")
    maximum_trades_per_day: int = 5
    minimum_risk_reward: Decimal = Decimal("2")
    allow_leverage: bool = False
    allow_margin: bool = False
    allow_futures: bool = False
    allow_average_down: bool = False
    trading_enabled: bool = False
    database_path: Path = Path("data/adaptive_trader.sqlite3")
    exchange: str = "BINANCE"
    interval: str = "1m"
    request_timeout_seconds: int = 10
    maximum_retries: int = 4
    include_open_candle: bool = False
    short_ema_period: int = 20
    long_ema_period: int = 50
    atr_period: int = 14
    volume_period: int = 20
    minimum_volume_ratio: Decimal = Decimal("1")
    maximum_atr_relative: Decimal = Decimal("0.05")
    stop_atr_multiple: Decimal = Decimal("2")
    target_r_multiple: Decimal = Decimal("2")
    warmup_candles: int = 100
    maker_fee_bps: Decimal = Decimal("10")
    taker_fee_bps: Decimal = Decimal("20")
    slippage_bps: Decimal = Decimal("5")
    spread_bps: Decimal = Decimal("2")
    execute_on_next_candle_open: bool = True
    latency_candles: int = 1
    ambiguous_intrabar_policy: str = "STOP_FIRST"
    force_close_at_end: bool = True
    allow_short_selling: bool = False
    partial_take_profit_enabled: bool = False
    partial_take_profit_r_multiple: Decimal = Decimal("2")
    partial_take_profit_percent: Decimal = Decimal("50")
    trailing_stop_enabled: bool = False
    trailing_stop_atr_multiple: Decimal = Decimal("2")
    break_even_after_r_multiple: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.isalnum() or self.symbol != self.symbol.upper():
            raise ConfigError("symbol must be a non-empty uppercase alphanumeric value")
        if self.exchange != "BINANCE":
            raise ConfigError("only Binance public market data is supported")
        if self.market is not MarketType.SPOT:
            raise ConfigError("only SPOT market is supported")
        if self.contract_type is not ContractType.NONE:
            raise ConfigError("Spot configuration requires contract_type NONE")
        if self.margin_mode is not MarginMode.NONE:
            raise ConfigError("Spot configuration requires margin_mode NONE")
        if self.trading_mode is not TradingMode.SPOT_LONG_ONLY:
            raise ConfigError("Spot configuration requires SPOT_LONG_ONLY")
        if self.interval not in {"1m", "5m", "15m", "1h", "4h", "1d"}:
            raise ConfigError("unsupported interval")
        for name in (
            "initial_balance",
            "maximum_position_percent",
            "maximum_daily_loss_percent",
            "minimum_risk_reward",
            "minimum_volume_ratio",
            "maximum_atr_relative",
            "stop_atr_multiple",
            "target_r_multiple",
            "maker_fee_bps",
            "taker_fee_bps",
            "slippage_bps",
            "spread_bps",
            "partial_take_profit_r_multiple",
            "partial_take_profit_percent",
            "trailing_stop_atr_multiple",
            "break_even_after_r_multiple",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ConfigError(f"{name} must be a finite Decimal")
        if self.initial_balance <= 0:
            raise ConfigError("initial_balance must be positive")
        if not Decimal("0") < self.maximum_position_percent <= Decimal("100"):
            raise ConfigError("maximum_position_percent must be in (0, 100]")
        if not Decimal("0") < self.maximum_daily_loss_percent <= Decimal("100"):
            raise ConfigError("maximum_daily_loss_percent must be in (0, 100]")
        if self.minimum_risk_reward <= 0:
            raise ConfigError("minimum_risk_reward must be positive")
        if self.maximum_atr_relative <= 0:
            raise ConfigError("maximum_atr_relative must be positive")
        if any(
            value < 0
            for value in (
                self.maker_fee_bps,
                self.taker_fee_bps,
                self.slippage_bps,
                self.spread_bps,
            )
        ):
            raise ConfigError("cost basis points must not be negative")
        if not Decimal("0") < self.partial_take_profit_percent <= Decimal("100"):
            raise ConfigError("partial_take_profit_percent must be in (0, 100]")
        if self.maximum_open_positions < 1 or self.maximum_trades_per_day < 1:
            raise ConfigError("position and trade limits must be positive")
        for name in (
            "request_timeout_seconds",
            "maximum_retries",
            "short_ema_period",
            "long_ema_period",
            "atr_period",
            "volume_period",
            "warmup_candles",
            "latency_candles",
        ):
            if getattr(self, name) < 1:
                raise ConfigError(f"{name} must be positive")
        if self.long_ema_period <= self.short_ema_period:
            raise ConfigError("long_ema_period must exceed short_ema_period")
        if self.ambiguous_intrabar_policy != "STOP_FIRST":
            raise ConfigError("only STOP_FIRST intrabar policy is supported")
        if self.allow_leverage or self.allow_margin or self.allow_futures:
            raise ConfigError("leverage, margin and futures are forbidden")
        if self.allow_average_down:
            raise ConfigError("average down is forbidden")
        if self.allow_short_selling:
            raise ConfigError("short selling is forbidden")
        if not self.execute_on_next_candle_open:
            raise ConfigError("execution must occur on a later candle open")

    def is_research_only(self) -> bool:
        return (
            not self.trading_enabled
            and self.market is MarketType.SPOT
            and not self.allow_leverage
            and not self.allow_margin
            and not self.allow_futures
        )

    def as_dict(self) -> dict[str, SerializedValue]:
        return serialize_model(self)


def _read_bool(environment: Mapping[str, str], name: str, default: bool) -> bool:
    value = environment.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ConfigError(f"{name} must be true or false")
    return normalized == "true"


def _integer(value: str, name: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    return result


def load_config(environment: Mapping[str, str] | None = None) -> TradingConfig:
    """Load safe settings from environment without reading any credentials."""

    values = os.environ if environment is None else environment
    database_path = Path(values.get("ADAPTIVE_TRADER_DB_PATH", "data/adaptive_trader.sqlite3"))
    return TradingConfig(
        symbol=values.get("ADAPTIVE_TRADER_SYMBOL", "ETHUSDT"),
        market=_enum_value(
            MarketType,
            values.get("ADAPTIVE_TRADER_MARKET", "SPOT"),
            "market",
        ),
        contract_type=_enum_value(
            ContractType,
            values.get("ADAPTIVE_TRADER_CONTRACT_TYPE", "NONE"),
            "contract_type",
        ),
        margin_mode=_enum_value(
            MarginMode,
            values.get("ADAPTIVE_TRADER_MARGIN_MODE", "NONE"),
            "margin_mode",
        ),
        trading_mode=_enum_value(
            TradingMode,
            values.get("ADAPTIVE_TRADER_TRADING_MODE", "SPOT_LONG_ONLY"),
            "trading_mode",
        ),
        initial_balance=_decimal(
            values.get("ADAPTIVE_TRADER_INITIAL_BALANCE", "10000"), "initial_balance"
        ),
        maximum_open_positions=_integer(
            values.get("ADAPTIVE_TRADER_MAX_OPEN_POSITIONS", "1"), "maximum_open_positions"
        ),
        maximum_position_percent=_decimal(
            values.get("ADAPTIVE_TRADER_MAX_POSITION_PERCENT", "5"), "maximum_position_percent"
        ),
        maximum_daily_loss_percent=_decimal(
            values.get("ADAPTIVE_TRADER_MAX_DAILY_LOSS_PERCENT", "1"), "maximum_daily_loss_percent"
        ),
        maximum_trades_per_day=_integer(
            values.get("ADAPTIVE_TRADER_MAX_TRADES_PER_DAY", "5"), "maximum_trades_per_day"
        ),
        minimum_risk_reward=_decimal(
            values.get("ADAPTIVE_TRADER_MIN_RISK_REWARD", "2"), "minimum_risk_reward"
        ),
        allow_leverage=_read_bool(values, "ADAPTIVE_TRADER_ALLOW_LEVERAGE", False),
        allow_margin=_read_bool(values, "ADAPTIVE_TRADER_ALLOW_MARGIN", False),
        allow_futures=_read_bool(values, "ADAPTIVE_TRADER_ALLOW_FUTURES", False),
        allow_average_down=_read_bool(values, "ADAPTIVE_TRADER_ALLOW_AVERAGE_DOWN", False),
        trading_enabled=_read_bool(values, "ADAPTIVE_TRADER_TRADING_ENABLED", False),
        database_path=database_path,
        exchange=values.get("ADAPTIVE_TRADER_EXCHANGE", "BINANCE"),
        interval=values.get("ADAPTIVE_TRADER_INTERVAL", "1m"),
        request_timeout_seconds=_integer(
            values.get("ADAPTIVE_TRADER_REQUEST_TIMEOUT_SECONDS", "10"),
            "request_timeout_seconds",
        ),
        maximum_retries=_integer(
            values.get("ADAPTIVE_TRADER_MAXIMUM_RETRIES", "4"), "maximum_retries"
        ),
        include_open_candle=_read_bool(values, "ADAPTIVE_TRADER_INCLUDE_OPEN_CANDLE", False),
        short_ema_period=_integer(
            values.get("ADAPTIVE_TRADER_SHORT_EMA_PERIOD", "20"), "short_ema_period"
        ),
        long_ema_period=_integer(
            values.get("ADAPTIVE_TRADER_LONG_EMA_PERIOD", "50"), "long_ema_period"
        ),
        atr_period=_integer(values.get("ADAPTIVE_TRADER_ATR_PERIOD", "14"), "atr_period"),
        volume_period=_integer(values.get("ADAPTIVE_TRADER_VOLUME_PERIOD", "20"), "volume_period"),
        minimum_volume_ratio=_decimal(
            values.get("ADAPTIVE_TRADER_MINIMUM_VOLUME_RATIO", "1"), "minimum_volume_ratio"
        ),
        maximum_atr_relative=_decimal(
            values.get("ADAPTIVE_TRADER_MAXIMUM_ATR_RELATIVE", "0.05"),
            "maximum_atr_relative",
        ),
        stop_atr_multiple=_decimal(
            values.get("ADAPTIVE_TRADER_STOP_ATR_MULTIPLE", "2"), "stop_atr_multiple"
        ),
        target_r_multiple=_decimal(
            values.get("ADAPTIVE_TRADER_TARGET_R_MULTIPLE", "2"), "target_r_multiple"
        ),
        warmup_candles=_integer(
            values.get("ADAPTIVE_TRADER_WARMUP_CANDLES", "100"), "warmup_candles"
        ),
        maker_fee_bps=_decimal(values.get("ADAPTIVE_TRADER_MAKER_FEE_BPS", "10"), "maker_fee_bps"),
        taker_fee_bps=_decimal(values.get("ADAPTIVE_TRADER_TAKER_FEE_BPS", "20"), "taker_fee_bps"),
        slippage_bps=_decimal(values.get("ADAPTIVE_TRADER_SLIPPAGE_BPS", "5"), "slippage_bps"),
        spread_bps=_decimal(values.get("ADAPTIVE_TRADER_SPREAD_BPS", "2"), "spread_bps"),
        execute_on_next_candle_open=_read_bool(
            values, "ADAPTIVE_TRADER_EXECUTE_ON_NEXT_CANDLE_OPEN", True
        ),
        latency_candles=_integer(
            values.get("ADAPTIVE_TRADER_LATENCY_CANDLES", "1"), "latency_candles"
        ),
        ambiguous_intrabar_policy=values.get(
            "ADAPTIVE_TRADER_AMBIGUOUS_INTRABAR_POLICY", "STOP_FIRST"
        ),
        force_close_at_end=_read_bool(values, "ADAPTIVE_TRADER_FORCE_CLOSE_AT_END", True),
        allow_short_selling=_read_bool(values, "ADAPTIVE_TRADER_ALLOW_SHORT_SELLING", False),
        partial_take_profit_enabled=_read_bool(
            values, "ADAPTIVE_TRADER_PARTIAL_TAKE_PROFIT_ENABLED", False
        ),
        partial_take_profit_r_multiple=_decimal(
            values.get("ADAPTIVE_TRADER_PARTIAL_TAKE_PROFIT_R", "2"),
            "partial_take_profit_r_multiple",
        ),
        partial_take_profit_percent=_decimal(
            values.get("ADAPTIVE_TRADER_PARTIAL_TAKE_PROFIT_PERCENT", "50"),
            "partial_take_profit_percent",
        ),
        trailing_stop_enabled=_read_bool(values, "ADAPTIVE_TRADER_TRAILING_STOP_ENABLED", False),
        trailing_stop_atr_multiple=_decimal(
            values.get("ADAPTIVE_TRADER_TRAILING_STOP_ATR", "2"), "trailing_stop_atr_multiple"
        ),
        break_even_after_r_multiple=_decimal(
            values.get("ADAPTIVE_TRADER_BREAK_EVEN_AFTER_R", "1"),
            "break_even_after_r_multiple",
        ),
    )
