"""Diagnostics for stability and concentration; not statistical proof."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from adaptive_trader.backtest.models import BacktestResult, TradeRecord
from adaptive_trader.research.models import ResearchSummary, RobustnessDiagnostics, SegmentRun


def _average(values: tuple[Decimal, ...]) -> Decimal | None:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _return_percent(result: BacktestResult) -> Decimal:
    if result.metrics.initial_capital == 0:
        return Decimal("0")
    return result.metrics.net_return / result.metrics.initial_capital * Decimal("100")


def consolidate_runs(runs: tuple[SegmentRun, ...]) -> ResearchSummary:
    completed = tuple(run for run in runs if run.result is not None and not run.failed)
    returns = tuple(_return_percent(run.result) for run in completed if run.result is not None)
    drawdowns = tuple(
        run.result.metrics.maximum_drawdown_percent for run in completed if run.result is not None
    )
    profit_factors = tuple(
        run.result.metrics.profit_factor
        for run in completed
        if run.result is not None and run.result.metrics.profit_factor is not None
    )
    expectancies = tuple(
        run.result.metrics.expectancy_per_trade
        for run in completed
        if run.result is not None and run.result.metrics.expectancy_per_trade is not None
    )
    positive = sum(value > 0 for value in returns)
    negative = sum(value < 0 for value in returns)
    flat = sum(value == 0 for value in returns)
    benchmark_wins = benchmark_losses = benchmark_ties = 0
    for run in completed:
        if run.result is None:
            continue
        buy_hold = next((item for item in run.benchmarks if item.name == "BUY_AND_HOLD"), None)
        if buy_hold is None:
            continue
        strategy_return = _return_percent(run.result)
        if strategy_return > buy_hold.net_return_percent:
            benchmark_wins += 1
        elif strategy_return < buy_hold.net_return_percent:
            benchmark_losses += 1
        else:
            benchmark_ties += 1
    total_comparisons = benchmark_wins + benchmark_losses + benchmark_ties
    warnings: list[str] = []
    if len(completed) < len(runs):
        warnings.append("FOLD_FAILURES_PRESENT")
    if sum(run.result.metrics.closed_trade_count for run in completed if run.result) < 10:
        warnings.append("TOO_FEW_TRADES")
    return ResearchSummary(
        fold_count=len(runs),
        completed_fold_count=len(completed),
        failed_fold_count=len(runs) - len(completed),
        total_evaluated_candles=sum(run.segment.candle_count for run in completed),
        total_entries=sum(run.result.metrics.entry_count for run in completed if run.result),
        total_closed_trades=sum(
            run.result.metrics.closed_trade_count for run in completed if run.result
        ),
        positive_fold_count=positive,
        negative_fold_count=negative,
        flat_fold_count=flat,
        positive_fold_percent=(Decimal(positive) / Decimal(len(returns)) * Decimal("100"))
        if returns
        else Decimal("0"),
        mean_net_return=_average(returns),
        median_net_return=_median(returns),
        worst_net_return=min(returns) if returns else None,
        best_net_return=max(returns) if returns else None,
        mean_max_drawdown=_average(drawdowns),
        worst_max_drawdown=max(drawdowns) if drawdowns else None,
        mean_profit_factor=_average(profit_factors),
        median_profit_factor=_median(profit_factors),
        mean_expectancy=_average(expectancies),
        benchmark_win_count=benchmark_wins,
        benchmark_loss_count=benchmark_losses,
        benchmark_tie_count=benchmark_ties,
        benchmark_win_percent=(
            Decimal(benchmark_wins) / Decimal(total_comparisons) * Decimal("100")
        )
        if total_comparisons
        else Decimal("0"),
        parameter_selection_frequency={},
        warnings=tuple(warnings),
    )


def _trade_concentration(
    trades: tuple[TradeRecord, ...],
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if not trades:
        return None, None, None
    total = sum((trade.net_pnl for trade in trades), Decimal("0"))
    profitable = sorted(
        (trade.net_pnl for trade in trades if trade.net_pnl > 0),
        reverse=True,
    )
    gross_profit = sum(profitable, Decimal("0"))
    if not profitable or gross_profit == 0:
        return Decimal("0"), Decimal("0"), total
    return (
        profitable[0] / gross_profit * Decimal("100"),
        sum(profitable[:5], Decimal("0")) / gross_profit * Decimal("100"),
        total - profitable[0],
    )


def _period_concentration(
    trades: tuple[TradeRecord, ...], period: str
) -> tuple[Decimal | None, Decimal | None]:
    grouped: dict[date | tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0"))
    for trade in trades:
        key = (
            trade.exit_time.date()
            if period == "day"
            else (trade.exit_time.year, trade.exit_time.month)
        )
        grouped[key] += trade.net_pnl
    if not grouped:
        return None, None
    profitable = sorted((value for value in grouped.values() if value > 0), reverse=True)
    gross_profit = sum(profitable, Decimal("0"))
    if not profitable or gross_profit == 0:
        return Decimal("0"), Decimal("0")
    return (
        profitable[0] / gross_profit * Decimal("100"),
        sum(profitable[:5], Decimal("0")) / gross_profit * Decimal("100"),
    )


def _month_counts(trades: tuple[TradeRecord, ...]) -> tuple[int, int]:
    grouped: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0"))
    for trade in trades:
        grouped[(trade.exit_time.year, trade.exit_time.month)] += trade.net_pnl
    return (
        sum(value > 0 for value in grouped.values()),
        sum(value < 0 for value in grouped.values()),
    )


def _longest_without_top(trades: tuple[TradeRecord, ...]) -> int:
    if not trades:
        return 0
    ordered = sorted(trades, key=lambda trade: trade.exit_time)
    equity = Decimal("0")
    top = Decimal("0")
    top_time = ordered[0].exit_time.date()
    longest = 0
    for trade in ordered:
        equity += trade.net_pnl
        if equity >= top:
            longest = max(longest, (trade.exit_time.date() - top_time).days)
            top = equity
            top_time = trade.exit_time.date()
    return longest


def diagnose(
    train: BacktestResult | None,
    validation: BacktestResult | None,
    runs: tuple[SegmentRun, ...],
    *,
    high_gap_percent: Decimal = Decimal("20"),
    concentration_single_percent: Decimal = Decimal("50"),
    concentration_top_five_percent: Decimal = Decimal("80"),
) -> RobustnessDiagnostics:
    warnings: list[str] = []
    train_gap = validation_gap = expectancy_gap = None
    if train is not None and validation is not None:
        train_return = _return_percent(train)
        validation_return = _return_percent(validation)
        train_gap = train_return - validation_return
        validation_gap = (
            (train.metrics.profit_factor or Decimal("0"))
            - (validation.metrics.profit_factor or Decimal("0"))
        )
        expectancy_gap = (train.metrics.expectancy_per_trade or Decimal("0")) - (
            validation.metrics.expectancy_per_trade or Decimal("0")
        )
        if abs(train_gap) > high_gap_percent:
            warnings.append("TRAIN_VALIDATION_GAP_HIGH")
        if validation_return < 0:
            warnings.append("OUT_OF_SAMPLE_DEGRADATION")
    all_trades = tuple(
        trade for run in runs if run.result is not None for trade in run.result.trades
    )
    best, top_five, without_best = _trade_concentration(all_trades)
    best_day, top_five_days = _period_concentration(all_trades, "day")
    positive_months, negative_months = _month_counts(all_trades)
    total_pnl = sum((trade.net_pnl for trade in all_trades), Decimal("0"))
    top_five_values = sorted(
        (trade.net_pnl for trade in all_trades if trade.net_pnl > 0),
        reverse=True,
    )[:5]
    if total_pnl > 0:
        if best is not None and best > concentration_single_percent:
            warnings.append("RESULTS_CONCENTRATED")
        if top_five is not None and top_five > concentration_top_five_percent:
            warnings.append("RESULTS_CONCENTRATED")
        if without_best is not None and without_best < 0:
            warnings.append("RESULTS_CONCENTRATED")
    summary = consolidate_runs(runs)
    if summary.benchmark_loss_count > summary.benchmark_win_count:
        warnings.append("BENCHMARK_UNDERPERFORMANCE")
    return RobustnessDiagnostics(
        train_validation_return_gap=train_gap,
        train_validation_profit_factor_gap=validation_gap,
        train_validation_expectancy_gap=expectancy_gap,
        positive_fold_percent=summary.positive_fold_percent,
        benchmark_win_percent=summary.benchmark_win_percent,
        best_trade_profit_percent=best,
        top_five_trade_profit_percent=top_five,
        result_without_best_trade=without_best,
        best_day_profit_percent=best_day,
        top_five_day_profit_percent=top_five_days,
        result_without_top_five_trades=total_pnl - sum(top_five_values, Decimal("0")),
        positive_month_count=positive_months,
        negative_month_count=negative_months,
        longest_period_without_new_top_days=_longest_without_top(all_trades),
        warnings=tuple(dict.fromkeys(warnings)),
    )
