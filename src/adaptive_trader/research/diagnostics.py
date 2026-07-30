"""Offline diagnostics built from point-in-time traces and backtest results."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from statistics import median
from typing import Protocol, cast

from adaptive_trader.backtest.models import BacktestResult
from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import (
    Candle,
    MarketRegime,
    SignalDirection,
    StrategyDecisionTrace,
)
from adaptive_trader.research.models import DatasetSegment, SegmentRun


class SegmentRunner(Protocol):
    def run_segment(
        self,
        segment: DatasetSegment,
        config: TradingConfig,
        *,
        time_exit_candles: int | None = None,
    ) -> SegmentRun: ...


def _percent(value: Decimal | None, denominator: Decimal) -> Decimal | None:
    if value is None or denominator == 0:
        return None
    return value / denominator * Decimal("100")


def annotate_traces(result: BacktestResult, segment_name: str) -> BacktestResult:
    parts = segment_name.split("-")
    fold_id = "-".join(parts[:2]) if len(parts) > 1 else None
    traces = tuple(
        replace(
            trace,
            evaluation_segment=segment_name,
            fold_id=fold_id,
            parameter_set_id="base",
        )
        for trace in result.decision_traces
    )
    return replace(result, decision_traces=traces)


def _traces(runs: Iterable[SegmentRun]) -> tuple[tuple[SegmentRun, StrategyDecisionTrace], ...]:
    return tuple(
        (run, trace)
        for run in runs
        if run.result is not None
        for trace in run.result.decision_traces
    )


def _stage_counts(
    traces: tuple[StrategyDecisionTrace, ...], config: TradingConfig
) -> dict[str, int]:
    def ema_confirmed(trace: StrategyDecisionTrace) -> bool:
        return (
            trace.short_ema is not None
            and trace.long_ema is not None
            and trace.short_ema > trace.long_ema
        )

    def volume_confirmed(trace: StrategyDecisionTrace) -> bool:
        return trace.volume_ratio is not None and trace.volume_ratio >= config.minimum_volume_ratio

    def volatility_ok(trace: StrategyDecisionTrace) -> bool:
        return trace.atr_relative is not None and trace.atr_relative <= config.maximum_atr_relative

    def reward_ok(trace: StrategyDecisionTrace) -> bool:
        return trace.risk_reward is not None and trace.risk_reward >= config.minimum_risk_reward

    return {
        "total_candles_evaluated": len(traces),
        "eligible_after_warmup": len(traces),
        "trending_up": sum(trace.regime is MarketRegime.TRENDING_UP for trace in traces),
        "ema_confirmed": sum(ema_confirmed(trace) for trace in traces),
        "volume_confirmed": sum(volume_confirmed(trace) for trace in traces),
        "volatility_acceptable": sum(volatility_ok(trace) for trace in traces),
        "risk_reward_acceptable": sum(reward_ok(trace) for trace in traces),
        "buy_signals": sum(trace.signal_direction is SignalDirection.BUY for trace in traces),
        "risk_approved": sum(trace.risk_approved is True for trace in traces),
        "orders_executed": sum(trace.execution_status == "EXECUTED" for trace in traces),
    }


def _funnel_row(
    label: str,
    traces: tuple[StrategyDecisionTrace, ...],
    config: TradingConfig,
    closed_trades: int,
) -> dict[str, object]:
    counts = _stage_counts(traces, config)
    denominator = Decimal(max(counts["total_candles_evaluated"], 1))
    return {
        "scope": label,
        **counts,
        "closed_trades": closed_trades,
        **{
            f"{key}_percent": Decimal(value) / denominator * Decimal("100")
            for key, value in counts.items()
            if key != "total_candles_evaluated"
        },
    }


def decision_funnel_rows(
    runs: tuple[SegmentRun, ...], config: TradingConfig
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    all_traces: list[StrategyDecisionTrace] = []
    for run in runs:
        if run.result is None:
            continue
        traces = run.result.decision_traces
        all_traces.extend(traces)
        rows.append(
            _funnel_row(
                run.segment.name,
                traces,
                config,
                run.result.metrics.closed_trade_count,
            )
        )
    if all_traces:
        closed_trades = sum(
            run.result.metrics.closed_trade_count
            for run in runs
            if run.result is not None
        )
        rows.insert(
            0,
            _funnel_row("all_segments", tuple(all_traces), config, closed_trades),
        )
    return tuple(rows)


def _future_stats(
    candles: tuple[Candle, ...], index: int, close: Decimal, horizon: int
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    end = index + horizon
    if index < 0 or end >= len(candles):
        return None, None, None
    future = candles[index + 1 : end + 1]
    if not future:
        return None, None, None
    highs = [candle.high for candle in future]
    lows = [candle.low for candle in future]
    closes = future[-1].close
    return (
        closes / close - Decimal("1"),
        max(highs) / close - Decimal("1"),
        min(lows) / close - Decimal("1"),
    )


def hold_reason_rows(
    runs: tuple[SegmentRun, ...], horizons: tuple[int, ...] = (1, 3, 6, 12, 24)
) -> tuple[dict[str, object], ...]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for run, trace in _traces(runs):
        if trace.signal_direction is not SignalDirection.HOLD or run.result is None:
            continue
        for horizon in horizons:
            future_return, favorable, adverse = _future_stats(
                run.segment.candles, trace.candle_index, trace.close_price, horizon
            )
            grouped[trace.strategy_reason_code].append(
                {
                    "reason_code": trace.strategy_reason_code,
                    "regime": trace.regime,
                    "horizon_candles": horizon,
                    "future_return": future_return,
                    "maximum_favorable_movement": favorable,
                    "maximum_adverse_movement": adverse,
                    "atr_relative": trace.atr_relative,
                    "volume_ratio": trace.volume_ratio,
                }
            )
    rows: list[dict[str, object]] = []
    totals_by_horizon = {
        horizon: sum(
            item["horizon_candles"] == horizon
            for values in grouped.values()
            for item in values
        )
        for horizon in horizons
    }
    for reason, values in sorted(grouped.items()):
        for horizon in horizons:
            subset = [item for item in values if item["horizon_candles"] == horizon]
            returns = [
                cast(Decimal, item["future_return"])
                for item in subset
                if item["future_return"] is not None
            ]
            favorable_values = [
                cast(Decimal, item["maximum_favorable_movement"])
                for item in subset
                if item["maximum_favorable_movement"] is not None
            ]
            adverse_values = [
                cast(Decimal, item["maximum_adverse_movement"])
                for item in subset
                if item["maximum_adverse_movement"] is not None
            ]
            regimes = sorted({str(item["regime"]) for item in subset})
            rows.append(
                {
                    "reason_code": reason,
                    "count": len(subset),
                    "percent": (
                        Decimal(len(subset))
                        / Decimal(max(totals_by_horizon[horizon], 1))
                        * Decimal("100")
                    ),
                    "horizon_candles": horizon,
                    "future_return_mean": (
                        sum(returns, Decimal("0")) / Decimal(len(returns))
                        if returns
                        else None
                    ),
                    "maximum_favorable_movement_mean": (
                        sum(favorable_values, Decimal("0")) / Decimal(len(favorable_values))
                        if favorable_values
                        else None
                    ),
                    "maximum_adverse_movement_mean": (
                        sum(adverse_values, Decimal("0")) / Decimal(len(adverse_values))
                        if adverse_values
                        else None
                    ),
                    "regimes": ",".join(regimes),
                    "post_event_only": True,
                }
            )
    return tuple(rows)


def _candle_index(candles: tuple[Candle, ...], timestamp: datetime) -> int:
    for index, candle in enumerate(candles):
        if candle.open_time == timestamp or candle.close_time == timestamp:
            return index
    return -1


def entry_diagnostic_rows(runs: tuple[SegmentRun, ...]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for run in runs:
        if run.result is None:
            continue
        candles = run.segment.candles
        for trade in run.result.trades:
            entry_index = _candle_index(candles, trade.entry_time)
            exit_index = _candle_index(candles, trade.exit_time)
            if entry_index < 0 or exit_index < entry_index:
                continue
            window = candles[entry_index : exit_index + 1]
            entry_price = trade.entry_price
            high_values = [candle.high for candle in window]
            low_values = [candle.low for candle in window]
            maximum_favorable = max(high_values) - entry_price
            maximum_adverse = min(low_values) - entry_price
            mfe_index = entry_index + high_values.index(max(high_values))
            mae_index = entry_index + low_values.index(min(low_values))
            realized = trade.net_pnl
            regime_at_entry = _regime_at(run.result, trade.entry_time)
            regime_at_exit = _regime_at(run.result, trade.exit_time)
            rows.append(
                {
                    "segment": run.segment.name,
                    "trade_id": trade.trade_id,
                    "entry_time": trade.entry_time,
                    "exit_time": trade.exit_time,
                    "entry_price": entry_price,
                    "exit_price": trade.exit_price,
                    "regime": regime_at_entry,
                    "regime_at_entry": regime_at_entry,
                    "regime_at_exit": regime_at_exit,
                    "regime_transition": (
                        f"{regime_at_entry}->{regime_at_exit}"
                        if regime_at_entry is not None and regime_at_exit is not None
                        else None
                    ),
                    "mfe": maximum_favorable,
                    "mae": maximum_adverse,
                    "mfe_percent": maximum_favorable / entry_price * Decimal("100"),
                    "mae_percent": maximum_adverse / entry_price * Decimal("100"),
                    "time_to_mfe": mfe_index - entry_index,
                    "time_to_mae": mae_index - entry_index,
                    "time_to_stop": (
                        trade.holding_candles if trade.exit_reason == "STOP_LOSS" else None
                    ),
                    "time_to_target": (
                        trade.holding_candles if trade.exit_reason == "TAKE_PROFIT" else None
                    ),
                    "exit_reason": trade.exit_reason,
                    "gross_pnl": trade.gross_pnl,
                    "costs": trade.fees + trade.slippage_cost + trade.spread_cost,
                    "net_pnl": realized,
                    "holding_candles": trade.holding_candles,
                    "excursion_efficiency": (
                        realized / (maximum_favorable * trade.quantity)
                        if maximum_favorable > 0
                        else None
                    ),
                }
            )
    return tuple(rows)


def _regime_at(result: BacktestResult, timestamp: datetime) -> MarketRegime | None:
    candidates = [
        trace
        for trace in result.decision_traces
        if trace.timestamp <= timestamp.astimezone(UTC)
    ]
    return max(candidates, key=lambda trace: trace.timestamp).regime if candidates else None


def exit_diagnostic_rows(runs: tuple[SegmentRun, ...]) -> tuple[dict[str, object], ...]:
    entries = entry_diagnostic_rows(runs)
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in entries:
        grouped[str(row["exit_reason"])].append(row)
    total_pnl = sum(
        (cast(Decimal, row["net_pnl"]) for row in entries), Decimal("0")
    )
    absolute_pnl = sum(
        (abs(cast(Decimal, row["net_pnl"])) for row in entries),
        Decimal("0"),
    )
    rows: list[dict[str, object]] = []
    for reason, values in sorted(grouped.items()):
        pnl = sum(
            (cast(Decimal, row["net_pnl"]) for row in values), Decimal("0")
        )
        net_values = [cast(Decimal, row["net_pnl"]) for row in values]
        wins = sum(value > 0 for value in net_values)
        rows.append(
            {
                "exit_reason": reason,
                "count": len(values),
                "average_net_pnl": pnl / Decimal(len(values)),
                "median_net_pnl": median(net_values),
                "win_rate": Decimal(wins) / Decimal(len(values)) * Decimal("100"),
                "mfe_mean": (
                    sum((cast(Decimal, row["mfe"]) for row in values), Decimal("0"))
                    / Decimal(len(values))
                ),
                "mae_mean": (
                    sum((cast(Decimal, row["mae"]) for row in values), Decimal("0"))
                    / Decimal(len(values))
                ),
                "costs": sum(
                    (cast(Decimal, row["costs"]) for row in values), Decimal("0")
                ),
                "holding_mean": (
                    sum((cast(int, row["holding_candles"]) for row in values), 0)
                    / Decimal(len(values))
                ),
                "pnl_contribution_percent_of_absolute_pnl": (
                    pnl / absolute_pnl * Decimal("100")
                    if absolute_pnl
                    else Decimal("0")
                ),
                "result_without_exit_type": total_pnl - pnl,
            }
        )
    return tuple(rows)


def entry_exit_decomposition_rows(
    segment: DatasetSegment, config: TradingConfig, runner: SegmentRunner
) -> tuple[dict[str, object], ...]:
    """Compare bounded exit hypotheses while keeping the entry component fixed."""

    scenarios: list[tuple[str, TradingConfig, int | None]] = [
        ("CURRENT", config, None),
        ("TIME_EXIT_6", config, 6),
        ("TIME_EXIT_12", config, 12),
        ("TIME_EXIT_24", config, 24),
        ("TIME_EXIT_48", config, 48),
    ]
    for stop in (Decimal("1.5"), Decimal("2.0"), Decimal("2.5")):
        for target in (Decimal("1.5"), Decimal("2.0"), Decimal("2.5")):
            scenarios.append(
                (
                    f"STOP_{stop}_TARGET_{target}",
                    replace(config, stop_atr_multiple=stop, target_r_multiple=target),
                    None,
                )
            )
    scenarios.extend(
        (
            ("TRAILING_DISABLED", replace(config, trailing_stop_enabled=False), None),
            (
                "BREAK_EVEN_DISABLED",
                replace(config, break_even_after_r_multiple=Decimal("1000000")),
                None,
            ),
        )
    )
    rows: list[dict[str, object]] = []
    for name, scenario_config, time_exit in scenarios:
        run = runner.run_segment(segment, scenario_config, time_exit_candles=time_exit)
        result = run.result
        rows.append(
            {
                "segment": segment.name,
                "scenario": name,
                "entry_rules": "CURRENT",
                "exit_rules": name,
                "net_return": (
                    result.metrics.net_return / result.metrics.initial_capital * Decimal("100")
                    if result
                    else None
                ),
                "closed_trades": result.metrics.closed_trade_count if result else 0,
                "drawdown": result.metrics.maximum_drawdown_percent if result else None,
                "costs": (
                    result.metrics.total_fees
                    + result.metrics.estimated_slippage
                    + result.metrics.total_spread_cost
                    if result
                    else None
                ),
                "warning": "" if result else run.error or "SCENARIO_FAILED",
            }
        )
    return tuple(rows)


def detailed_regime_rows(runs: tuple[SegmentRun, ...]) -> tuple[dict[str, object], ...]:
    grouped: dict[tuple[str, str], list[StrategyDecisionTrace]] = defaultdict(list)
    for run, trace in _traces(runs):
        grouped[(run.segment.name, trace.regime.value)].append(trace)
    entry_rows = entry_diagnostic_rows(runs)
    rows: list[dict[str, object]] = []
    for (segment, regime), traces in sorted(grouped.items()):
        result = next(run.result for run in runs if run.segment.name == segment)
        if result is None:
            continue
        entries = tuple(
            row
            for row in entry_rows
            if row["segment"] == segment
            and isinstance(row["regime_at_entry"], MarketRegime)
            and row["regime_at_entry"].value == regime
        )
        trade_ids = {str(row["trade_id"]) for row in entries}
        trades = tuple(trade for trade in result.trades if trade.trade_id in trade_ids)
        pnl = sum((trade.net_pnl for trade in trades), Decimal("0"))
        wins = sum(trade.net_pnl > 0 for trade in trades)
        losses = sum((trade.net_pnl for trade in trades if trade.net_pnl < 0), Decimal("0"))
        gains = sum((trade.net_pnl for trade in trades if trade.net_pnl > 0), Decimal("0"))
        rows.append(
            {
                "segment": segment,
                "regime": regime,
                "candle_count": len(traces),
                "eligible_candle_count": len(traces),
                "signal_count": sum(
                    trace.signal_direction is SignalDirection.BUY for trace in traces
                ),
                "entry_count": sum(
                    trace.execution_status == "EXECUTED" for trace in traces
                ),
                "closed_trade_count": len(trades),
                "win_rate": (
                    Decimal(wins) / Decimal(len(trades)) * Decimal("100")
                    if trades
                    else None
                ),
                "net_pnl": pnl,
                "net_return": (
                    pnl / result.metrics.initial_capital * Decimal("100")
                    if result.metrics.initial_capital
                    else None
                ),
                "profit_factor": gains / abs(losses) if losses else None,
                "expectancy": pnl / Decimal(len(trades)) if trades else None,
                "mfe": (
                    sum(
                        (cast(Decimal, entry["mfe"]) for entry in entries),
                        Decimal("0"),
                    )
                    / Decimal(len(entries))
                    if entries
                    else None
                ),
                "mae": (
                    sum(
                        (cast(Decimal, entry["mae"]) for entry in entries),
                        Decimal("0"),
                    )
                    / Decimal(len(entries))
                    if entries
                    else None
                ),
                "drawdown": result.metrics.maximum_drawdown_percent,
                "exposure": result.metrics.average_exposure_percent,
                "costs": sum(
                    (
                        trade.fees + trade.slippage_cost + trade.spread_cost
                        for trade in trades
                    ),
                    Decimal("0"),
                ),
                "hold_reasons": ",".join(
                    sorted(
                        {
                            trace.strategy_reason_code
                            for trace in traces
                            if trace.signal_direction is SignalDirection.HOLD
                        }
                    )
                ),
                "exit_reasons": ",".join(sorted({trade.exit_reason for trade in trades})),
            }
        )
    return tuple(rows)


def robustness_scorecard(
    runs: tuple[SegmentRun, ...], config: TradingConfig
) -> tuple[dict[str, object], ...]:
    completed = [run for run in runs if run.result is not None]
    if not completed:
        return ()
    returns = [
        run.result.metrics.net_return / run.result.metrics.initial_capital * Decimal("100")
        for run in completed
        if run.result is not None
    ]
    positive_folds = sum(value > 0 for value in returns)
    trades = sum(run.result.metrics.closed_trade_count for run in completed if run.result)
    drawdown = max(
        (run.result.metrics.maximum_drawdown_percent for run in completed if run.result),
        default=Decimal("0"),
    )
    values = {
        "net_return": (
            "GOOD" if sum(returns, Decimal("0")) > 0 else "POOR",
            "net return across completed segments",
        ),
        "drawdown": (
            "GOOD" if drawdown <= Decimal("10") else "POOR",
            f"worst drawdown={drawdown}%",
        ),
        "trade_sample": ("GOOD" if trades >= 30 else "INCONCLUSIVE", f"closed trades={trades}"),
        "fold_consistency": (
            "GOOD" if positive_folds >= len(returns) / 2 else "POOR",
            f"positive folds={positive_folds}/{len(returns)}",
        ),
        "cost_resilience": ("INCONCLUSIVE", "cost scenarios must be inspected separately"),
        "parameter_stability": ("INCONCLUSIVE", "no parameter selection was performed"),
        "regime_dependence": ("INCONCLUSIVE", "regime sample is diagnostic, not causal"),
        "benchmark_comparison": ("MIXED", "BUY_AND_HOLD is a reference only"),
        "out_of_sample_degradation": ("MIXED", "compare train and later validation explicitly"),
    }
    return tuple(
        {"dimension": dimension, "classification": classification, "justification": justification}
        for dimension, (classification, justification) in values.items()
    )


def candidate_assessment(
    runs: tuple[SegmentRun, ...],
    *,
    cost_rows: tuple[dict[str, object], ...] = (),
    minimum_closed_trades: int = 30,
    minimum_positive_fold_percent: Decimal = Decimal("50"),
    minimum_median_net_return: Decimal = Decimal("0"),
    maximum_worst_drawdown_percent: Decimal = Decimal("10"),
    minimum_cost_stress_positive_fold_percent: Decimal = Decimal("30"),
    maximum_best_trade_concentration_percent: Decimal = Decimal("50"),
    maximum_zero_trade_fold_percent: Decimal = Decimal("25"),
) -> dict[str, object]:
    completed = [run for run in runs if run.result is not None]
    returns = sorted(
        run.result.metrics.net_return / run.result.metrics.initial_capital * Decimal("100")
        for run in completed
        if run.result
    )
    positive_percent = (
        Decimal(sum(value > 0 for value in returns))
        / Decimal(max(len(returns), 1))
        * Decimal("100")
    )
    trades = sum(run.result.metrics.closed_trade_count for run in completed if run.result)
    worst_drawdown = max(
        (run.result.metrics.maximum_drawdown_percent for run in completed if run.result),
        default=Decimal("0"),
    )
    zero_trade_percent = (
        Decimal(
            sum(
                run.result.metrics.closed_trade_count == 0
                for run in completed
                if run.result
            )
        )
        / Decimal(max(len(completed), 1))
        * Decimal("100")
    )
    median_return = median(returns) if returns else Decimal("0")
    all_trades = tuple(
        trade for run in completed if run.result is not None for trade in run.result.trades
    )
    profitable_trades = tuple(trade.net_pnl for trade in all_trades if trade.net_pnl > 0)
    gross_profit = sum(profitable_trades, Decimal("0"))
    best_concentration = (
        max(profitable_trades) / gross_profit * Decimal("100")
        if gross_profit > 0
        else Decimal("0")
    )
    stress_rows = [
        row
        for row in cost_rows
        if row.get("scenario") == "STRESS_COST" and row.get("fold") != "CONSOLIDATED"
    ]
    stress_positive_percent = (
        Decimal(sum(row.get("status") == "POSITIVE" for row in stress_rows))
        / Decimal(len(stress_rows))
        * Decimal("100")
        if stress_rows
        else None
    )
    checks = {
        "minimum_closed_trades": trades >= minimum_closed_trades,
        "minimum_positive_fold_percent": positive_percent >= minimum_positive_fold_percent,
        "minimum_median_net_return": median_return >= minimum_median_net_return,
        "maximum_worst_drawdown_percent": worst_drawdown <= maximum_worst_drawdown_percent,
        "minimum_cost_stress_positive_fold_percent": (
            stress_positive_percent is not None
            and stress_positive_percent >= minimum_cost_stress_positive_fold_percent
        ),
        "maximum_best_trade_concentration_percent": (
            best_concentration is not None
            and best_concentration <= maximum_best_trade_concentration_percent
        ),
        "maximum_zero_trade_fold_percent": zero_trade_percent <= maximum_zero_trade_fold_percent,
    }
    status = "CANDIDATE" if all(checks.values()) else "NOT_CANDIDATE"
    if not completed or not returns or stress_positive_percent is None:
        status = "INCONCLUSIVE"
    return {
        "status": status,
        "checks": checks,
        "observed": {
            "closed_trades": trades,
            "positive_fold_percent": positive_percent,
            "median_net_return": median_return,
            "worst_drawdown_percent": worst_drawdown,
            "stress_positive_fold_percent": stress_positive_percent,
            "best_trade_concentration_percent": best_concentration,
            "zero_trade_fold_percent": zero_trade_percent,
        },
        "uses_consumed_test_period": False,
    }
