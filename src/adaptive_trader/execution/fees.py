"""Configurable per-fill research fee model; no account fee claims."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from adaptive_trader.domain.market import MarketType
from adaptive_trader.execution.models import LiquidityRole

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class MarketFeeRates:
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal

    def __post_init__(self) -> None:
        for name in ("maker_fee_rate", "taker_fee_rate"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO:
                raise ValueError(f"{name} must be a non-negative finite Decimal")


@dataclass(frozen=True, slots=True)
class FeeConfig:
    spot: MarketFeeRates = MarketFeeRates(Decimal("0.0010"), Decimal("0.0010"))
    futures: MarketFeeRates = MarketFeeRates(Decimal("0.0002"), Decimal("0.0005"))
    fee_asset: str = "USDT"
    research_defaults_only: bool = True


class FeeModel:
    def __init__(self, config: FeeConfig | None = None) -> None:
        self.config = config or FeeConfig()

    def rate(self, market: MarketType, role: LiquidityRole) -> Decimal:
        rates = self.config.spot if market is MarketType.SPOT else self.config.futures
        return rates.maker_fee_rate if role is LiquidityRole.MAKER else rates.taker_fee_rate

    def calculate(
        self,
        market: MarketType,
        role: LiquidityRole,
        price: Decimal,
        quantity: Decimal,
    ) -> Decimal:
        if price <= ZERO or quantity <= ZERO:
            raise ValueError("fee price and quantity must be positive")
        return price * quantity * self.rate(market, role)
