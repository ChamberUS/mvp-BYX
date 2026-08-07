"""Non-executing portfolio, planning, markout and latency contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.microstructure.models import (
    IntradayAlphaDecision,
    IntradayOrderIntent,
    IntradayRiskConfig,
)

TEN_THOUSAND = Decimal("10000")
MARKOUT_HORIZONS_MS = (100, 250, 500, 1000, 3000, 5000, 15000, 60000)


class PortfolioRiskGovernor(Protocol):
    def approve(
        self,
        decision: IntradayAlphaDecision,
        config: IntradayRiskConfig,
    ) -> bool: ...


class ExecutionPlanner(Protocol):
    def plan(self, decision: IntradayAlphaDecision) -> IntradayOrderIntent | None: ...


@dataclass(frozen=True, slots=True)
class MarkoutPrice:
    timestamp: datetime
    executable_price: Decimal
    mid_price: Decimal

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("markout price timestamp must be timezone-aware")
        if self.executable_price <= 0 or self.mid_price <= 0:
            raise ValueError("markout prices must be positive")


@dataclass(frozen=True, slots=True)
class MarkoutResult:
    signal_time: datetime
    side: PositionSide
    execution_style: str
    reference_price: Decimal
    horizons_bps: tuple[tuple[int, Decimal | None], ...]
    post_event_only: bool = True


def calculate_markouts(
    *,
    signal_time: datetime,
    side: PositionSide,
    reference_price: Decimal,
    prices: tuple[MarkoutPrice, ...],
    execution_style: str,
    use_mid: bool,
) -> MarkoutResult:
    if signal_time.tzinfo is None or signal_time.utcoffset() is None:
        raise ValueError("signal_time must be timezone-aware")
    if reference_price <= 0 or execution_style not in {"MAKER", "TAKER"}:
        raise ValueError("markout reference or execution style is invalid")
    if any(item.timestamp < signal_time for item in prices):
        raise ValueError("markout inputs must be post-event only")
    ordered = tuple(sorted(prices, key=lambda item: item.timestamp))
    results: list[tuple[int, Decimal | None]] = []
    for horizon in MARKOUT_HORIZONS_MS:
        target = signal_time + timedelta(milliseconds=horizon)
        observed = next((item for item in ordered if item.timestamp >= target), None)
        if observed is None:
            results.append((horizon, None))
            continue
        price = observed.mid_price if use_mid else observed.executable_price
        markout = (
            (price / reference_price - Decimal("1")) * TEN_THOUSAND
            if side is PositionSide.LONG
            else (reference_price / price - Decimal("1")) * TEN_THOUSAND
        )
        results.append((horizon, markout))
    return MarkoutResult(
        signal_time=signal_time,
        side=side,
        execution_style=execution_style,
        reference_price=reference_price,
        horizons_bps=tuple(results),
    )
