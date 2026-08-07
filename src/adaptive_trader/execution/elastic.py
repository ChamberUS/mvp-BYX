"""Mechanical bridge from executable Elastic observations to simulated exit orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.execution.engine import ExecutionResult, ExecutionSimulator
from adaptive_trader.execution.models import (
    BookState,
    OrderSide,
    OrderType,
    PositionEffect,
)
from adaptive_trader.microstructure.elastic_exit import (
    ElasticProfitExitController,
    ElasticProfitExitObservation,
)
from adaptive_trader.microstructure.models import (
    LiquiditySnapshot,
    MakerPreference,
    ProfitExtensionState,
)


class ElasticExitOrderStyle(StrEnum):
    MARKET = "MARKET"
    MARKETABLE_LIMIT = "MARKETABLE_LIMIT"


@dataclass(frozen=True, slots=True)
class ReversalDiagnostics:
    price_reversal_detected: bool = False
    ofi_reversal_detected: bool = False
    trade_flow_reversal_detected: bool = False
    depth_reversal_detected: bool = False
    microprice_reversal_detected: bool = False

    @property
    def microstructure_reversal(self) -> bool:
        return any(
            (
                self.ofi_reversal_detected,
                self.trade_flow_reversal_detected,
                self.depth_reversal_detected,
                self.microprice_reversal_detected,
            )
        )


@dataclass(frozen=True, slots=True)
class ElasticExitExecutionResult:
    observation: ElasticProfitExitObservation
    execution: ExecutionResult | None
    diagnostics: ReversalDiagnostics


class ElasticExitExecutionAdapter:
    """Submit at most one local exit after the controller requests it."""

    def __init__(
        self,
        *,
        simulator: ExecutionSimulator,
        controller: ElasticProfitExitController,
        market: MarketType,
        symbol: str,
        style: ElasticExitOrderStyle = ElasticExitOrderStyle.MARKET,
        maximum_slippage_bps: Decimal = Decimal("10"),
    ) -> None:
        if maximum_slippage_bps < 0:
            raise ValueError("maximum slippage must be non-negative")
        if market is MarketType.SPOT and controller.side is PositionSide.SHORT:
            raise ValueError("Spot cannot execute a short Elastic exit")
        self.simulator = simulator
        self.controller = controller
        self.market = market
        self.symbol = symbol.upper()
        self.style = style
        self.maximum_slippage_bps = maximum_slippage_bps
        self.exit_submitted = False

    def observe(
        self,
        *,
        timestamp: datetime,
        liquidity: LiquiditySnapshot,
        books: tuple[BookState, ...],
        diagnostics: ReversalDiagnostics,
        mark_price: Decimal | None = None,
    ) -> ElasticExitExecutionResult:
        observation = self.controller.observe(
            timestamp=timestamp,
            liquidity=liquidity,
            microstructure_reversal=diagnostics.microstructure_reversal,
            mark_price=mark_price,
        )
        if self.exit_submitted or observation.state not in {
            ProfitExtensionState.EXIT_REQUESTED,
            ProfitExtensionState.FAILSAFE,
        }:
            return ElasticExitExecutionResult(observation, None, diagnostics)
        reference = observation.executable_reference
        if reference is None:
            reference = (
                liquidity.best_bid
                if self.controller.side is PositionSide.LONG
                else liquidity.best_ask
            )
        is_long = self.controller.side is PositionSide.LONG
        order_type = (
            OrderType.MARKET
            if self.style is ElasticExitOrderStyle.MARKET
            else OrderType.MARKETABLE_LIMIT
        )
        execution = self.simulator.submit(
            client_intent_id=f"elastic-exit-{self.controller.config.profile_id}",
            market=self.market,
            symbol=self.symbol,
            side=OrderSide.SELL if is_long else OrderSide.BUY,
            position_effect=(
                PositionEffect.CLOSE_LONG if is_long else PositionEffect.CLOSE_SHORT
            ),
            order_type=order_type,
            quantity=self.controller.quantity,
            decision_time=timestamp,
            books=books,
            reference_price=reference,
            limit_price=reference if order_type is OrderType.MARKETABLE_LIMIT else None,
            maker_preference=MakerPreference.TAKER,
            maximum_slippage_bps=self.maximum_slippage_bps,
        )
        self.exit_submitted = True
        return ElasticExitExecutionResult(observation, execution, diagnostics)
