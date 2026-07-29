"""Pure backtest metric calculations."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from adaptive_trader.backtest.models import BacktestMetrics, TradeRecord


def _average(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def calculate_metrics(
    *,
    initial_capital: Decimal,
    final_capital: Decimal,
    trades: tuple[TradeRecord, ...],
    equity_curve: tuple[Decimal, ...],
    exposure_curve: tuple[Decimal, ...],
    start_price: Decimal,
    end_price: Decimal,
    unrealized_pnl: Decimal = Decimal("0"),
) -> BacktestMetrics:
    fees = sum((trade.fees for trade in trades), Decimal("0"))
    slippage = sum((trade.slippage_cost for trade in trades), Decimal("0"))
    spread = sum((trade.spread_cost for trade in trades), Decimal("0"))
    realized = sum((trade.net_pnl for trade in trades), Decimal("0"))
    gains = tuple(trade.net_pnl for trade in trades if trade.net_pnl > 0)
    losses = tuple(trade.net_pnl for trade in trades if trade.net_pnl < 0)
    total = len(trades)
    win_rate = Decimal(len(gains)) / Decimal(total) * Decimal("100") if total else None
    average_gain = _average(gains)
    average_loss = _average(losses)
    payoff = average_gain / abs(average_loss) if average_gain is not None and average_loss else None
    gross_wins = sum(gains, Decimal("0"))
    gross_losses = abs(sum(losses, Decimal("0")))
    profit_factor = gross_wins / gross_losses if gross_losses else None
    expectancy = realized / Decimal(total) if total else None
    holding_seconds = tuple(
        Decimal(
            (trade.exit_time - trade.entry_time).days * 86400
            + (trade.exit_time - trade.entry_time).seconds
        )
        for trade in trades
    )
    max_drawdown_value, max_drawdown_percent = _drawdown(equity_curve)
    if start_price <= 0:
        buy_hold = None
    else:
        buy_hold = (end_price - start_price) / start_price * Decimal("100")
    return BacktestMetrics(
        initial_capital=initial_capital,
        final_capital=final_capital,
        gross_return=sum((trade.gross_pnl for trade in trades), Decimal("0")),
        net_return=final_capital - initial_capital,
        realized_pnl=realized,
        unrealized_pnl=unrealized_pnl,
        total_fees=fees,
        estimated_slippage=slippage,
        total_spread_cost=spread,
        total_trades=total,
        winning_trades=len(gains),
        losing_trades=len(losses),
        win_rate=win_rate,
        average_gain=average_gain,
        average_loss=average_loss,
        largest_gain=max(gains) if gains else None,
        largest_loss=min(losses) if losses else None,
        payoff=payoff,
        profit_factor=profit_factor,
        expectancy_per_trade=expectancy,
        maximum_drawdown_value=max_drawdown_value,
        maximum_drawdown_percent=max_drawdown_percent,
        largest_winning_streak=_streak(trades, positive=True),
        largest_losing_streak=_streak(trades, positive=False),
        average_holding_candles=_average(tuple(Decimal(trade.holding_candles) for trade in trades)),
        average_holding_seconds=_average(holding_seconds),
        average_exposure_percent=_average(exposure_curve) or Decimal("0"),
        buy_and_hold_return=buy_hold,
    )


def _drawdown(equity_curve: tuple[Decimal, ...]) -> tuple[Decimal, Decimal]:
    peak: Decimal | None = None
    maximum_value = Decimal("0")
    maximum_percent = Decimal("0")
    for equity in equity_curve:
        peak = equity if peak is None else max(peak, equity)
        value = peak - equity
        percent = value / peak * Decimal("100") if peak else Decimal("0")
        maximum_value = max(maximum_value, value)
        maximum_percent = max(maximum_percent, percent)
    return maximum_value, maximum_percent


def _streak(trades: tuple[TradeRecord, ...], *, positive: bool) -> int:
    best = current = 0
    for trade in trades:
        matches = trade.net_pnl > 0 if positive else trade.net_pnl < 0
        current = current + 1 if matches else 0
        best = max(best, current)
    return best
