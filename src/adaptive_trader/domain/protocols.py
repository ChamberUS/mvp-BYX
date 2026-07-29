"""Ports that keep analysis, risk, execution and persistence decoupled."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import (
    Candle,
    Fill,
    MarketContext,
    MarketSignal,
    OrderIntent,
    PortfolioSnapshot,
    Position,
    RiskDecision,
    SimulatedOrder,
    StrategyDecisionRecord,
)


class MarketDataProvider(Protocol):
    def get_candles(self, symbol: str, limit: int) -> tuple[Candle, ...]: ...


class MarketAnalyzer(Protocol):
    def analyze(self, context: MarketContext) -> MarketSignal: ...


class RiskManager(Protocol):
    def evaluate(
        self,
        signal: MarketSignal,
        portfolio: PortfolioSnapshot,
        limits: TradingConfig,
    ) -> RiskDecision: ...


class OrderExecutor(Protocol):
    def execute(self, intent: OrderIntent) -> SimulatedOrder: ...


class Repository(Protocol):
    def save_candle(self, candle: Candle) -> None: ...

    def save_strategy_decision(self, record: StrategyDecisionRecord) -> None: ...

    def save_risk_decision(self, decision: RiskDecision) -> None: ...

    def save_simulated_order(self, order: SimulatedOrder) -> None: ...

    def save_fill(self, fill: Fill) -> None: ...

    def save_position(self, position: Position) -> None: ...

    def save_portfolio_snapshot(self, snapshot: PortfolioSnapshot) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
