"""Backtest result models with Decimal financial fields."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from adaptive_trader.domain.models import SerializedValue


@dataclass(frozen=True, slots=True)
class TradeRecord:
    trade_id: str
    symbol: str
    quantity: Decimal
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    fees: Decimal
    slippage_cost: Decimal
    spread_cost: Decimal
    net_pnl: Decimal
    exit_reason: str
    intrabar_ambiguous: bool
    holding_candles: int


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    initial_capital: Decimal
    final_capital: Decimal
    gross_return: Decimal
    net_return: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    estimated_slippage: Decimal
    total_spread_cost: Decimal
    entry_count: int
    order_count: int
    closed_trade_count: int
    partial_exit_count: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal | None
    average_gain: Decimal | None
    average_loss: Decimal | None
    largest_gain: Decimal | None
    largest_loss: Decimal | None
    payoff: Decimal | None
    profit_factor: Decimal | None
    expectancy_per_trade: Decimal | None
    maximum_drawdown_value: Decimal
    maximum_drawdown_percent: Decimal
    largest_winning_streak: int
    largest_losing_streak: int
    average_holding_candles: Decimal | None
    average_holding_seconds: Decimal | None
    average_exposure_percent: Decimal
    buy_and_hold_return: Decimal | None

    @property
    def total_trades(self) -> int:
        return self.closed_trade_count


@dataclass(frozen=True, slots=True)
class BacktestResult:
    report_version: str
    strategy_version: str
    symbol: str
    interval: str
    start_time: datetime
    end_time: datetime
    executed_at: datetime
    candle_count: int
    parameters: dict[str, SerializedValue]
    metrics: BacktestMetrics
    trades: tuple[TradeRecord, ...]
    warnings: tuple[str, ...]
