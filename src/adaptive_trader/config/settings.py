"""Validated, secret-free configuration for research and paper trading."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class TradingConfig:
    symbol: str = "ETHUSDT"
    market: str = "SPOT"
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

    def __post_init__(self) -> None:
        if self.symbol != "ETHUSDT":
            raise ConfigError("only ETHUSDT is supported in this sprint")
        if self.market != "SPOT":
            raise ConfigError("only SPOT market is supported")
        for name in (
            "initial_balance",
            "maximum_position_percent",
            "maximum_daily_loss_percent",
            "minimum_risk_reward",
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
        if self.maximum_open_positions < 1 or self.maximum_trades_per_day < 1:
            raise ConfigError("position and trade limits must be positive")
        if self.allow_leverage or self.allow_margin or self.allow_futures:
            raise ConfigError("leverage, margin and futures are forbidden")
        if self.allow_average_down:
            raise ConfigError("average down is forbidden")

    def is_research_only(self) -> bool:
        return (
            not self.trading_enabled
            and self.market == "SPOT"
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


def load_config(environment: Mapping[str, str] | None = None) -> TradingConfig:
    """Load safe settings from environment without reading any credentials."""

    values = os.environ if environment is None else environment
    database_path = Path(values.get("ADAPTIVE_TRADER_DB_PATH", "data/adaptive_trader.sqlite3"))
    return TradingConfig(
        symbol=values.get("ADAPTIVE_TRADER_SYMBOL", "ETHUSDT"),
        market=values.get("ADAPTIVE_TRADER_MARKET", "SPOT"),
        initial_balance=_decimal(
            values.get("ADAPTIVE_TRADER_INITIAL_BALANCE", "10000"), "initial_balance"
        ),
        maximum_open_positions=int(values.get("ADAPTIVE_TRADER_MAX_OPEN_POSITIONS", "1")),
        maximum_position_percent=_decimal(
            values.get("ADAPTIVE_TRADER_MAX_POSITION_PERCENT", "5"), "maximum_position_percent"
        ),
        maximum_daily_loss_percent=_decimal(
            values.get("ADAPTIVE_TRADER_MAX_DAILY_LOSS_PERCENT", "1"), "maximum_daily_loss_percent"
        ),
        maximum_trades_per_day=int(values.get("ADAPTIVE_TRADER_MAX_TRADES_PER_DAY", "5")),
        minimum_risk_reward=_decimal(
            values.get("ADAPTIVE_TRADER_MIN_RISK_REWARD", "2"), "minimum_risk_reward"
        ),
        allow_leverage=_read_bool(values, "ADAPTIVE_TRADER_ALLOW_LEVERAGE", False),
        allow_margin=_read_bool(values, "ADAPTIVE_TRADER_ALLOW_MARGIN", False),
        allow_futures=_read_bool(values, "ADAPTIVE_TRADER_ALLOW_FUTURES", False),
        allow_average_down=_read_bool(values, "ADAPTIVE_TRADER_ALLOW_AVERAGE_DOWN", False),
        trading_enabled=_read_bool(values, "ADAPTIVE_TRADER_TRADING_ENABLED", False),
        database_path=database_path,
    )
