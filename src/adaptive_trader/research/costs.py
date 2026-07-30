"""Configured cost scenarios for robustness reporting."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import cast

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.research.experiment import ResearchExperimentRunner
from adaptive_trader.research.models import DatasetSegment, SegmentRun


def cost_scenarios(config: TradingConfig) -> dict[str, TradingConfig]:
    return {
        "LOW_COST": replace(
            config,
            taker_fee_bps=max(Decimal("1"), config.taker_fee_bps / Decimal("2")),
            spread_bps=max(Decimal("1"), config.spread_bps / Decimal("2")),
            slippage_bps=max(Decimal("1"), config.slippage_bps / Decimal("2")),
        ),
        "BASE_COST": config,
        "HIGH_COST": replace(
            config,
            taker_fee_bps=config.taker_fee_bps * Decimal("2"),
            spread_bps=config.spread_bps * Decimal("2"),
            slippage_bps=config.slippage_bps * Decimal("2"),
        ),
        "STRESS_COST": replace(
            config,
            taker_fee_bps=config.taker_fee_bps * Decimal("4"),
            spread_bps=config.spread_bps * Decimal("4"),
            slippage_bps=config.slippage_bps * Decimal("4"),
        ),
    }


def run_cost_scenarios(
    segment: DatasetSegment,
    config: TradingConfig,
    runner: ResearchExperimentRunner,
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for name, scenario_config in cost_scenarios(config).items():
        run = runner.run_segment(segment, scenario_config)
        result = run.result
        rows.append(
            {
                "scenario": name,
                "net_return": (
                    result.metrics.net_return / result.metrics.initial_capital * Decimal("100")
                    if result
                    else None
                ),
                "gross_return": (
                    result.metrics.gross_return / result.metrics.initial_capital * Decimal("100")
                    if result
                    else None
                ),
                "total_costs": (
                    result.metrics.total_fees
                    + result.metrics.estimated_slippage
                    + result.metrics.total_spread_cost
                    if result
                    else None
                ),
                "warning": "COST_SENSITIVITY_HIGH"
                if result is not None and result.metrics.net_return < 0
                else "",
            }
        )
    return tuple(rows)


def run_cost_scenarios_by_fold(
    runs: tuple[SegmentRun, ...],
    config: TradingConfig,
    runner: ResearchExperimentRunner,
) -> tuple[dict[str, object], ...]:
    """Re-run each supplied evaluation fold under bounded cost scenarios."""

    scenarios = cost_scenarios(config)
    results: dict[tuple[str, str], SegmentRun] = {}
    for run in runs:
        for name, scenario_config in scenarios.items():
            results[(run.segment.name, name)] = runner.run_segment(run.segment, scenario_config)
    rows: list[dict[str, object]] = []
    for run in runs:
        base = results[(run.segment.name, "BASE_COST")]
        base_return = _return_percent(base)
        for name in scenarios:
            scenario_run = results[(run.segment.name, name)]
            result = scenario_run.result
            net_return = _return_percent(scenario_run)
            rows.append(
                {
                    "fold": run.segment.name,
                    "scenario": name,
                    "net_return": net_return,
                    "final_capital": result.metrics.final_capital if result else None,
                    "total_costs": (
                        result.metrics.total_fees
                        + result.metrics.estimated_slippage
                        + result.metrics.total_spread_cost
                        if result
                        else None
                    ),
                    "trade_count": result.metrics.closed_trade_count if result else 0,
                    "drawdown": result.metrics.maximum_drawdown_percent if result else None,
                    "difference_against_base": (
                        net_return - base_return
                        if net_return is not None and base_return is not None
                        else None
                    ),
                    "status": (
                        "POSITIVE" if net_return is not None and net_return > 0 else "NEGATIVE"
                    ),
                    "warning": "" if result else scenario_run.error or "SCENARIO_FAILED",
                }
            )
    for name in scenarios:
        scenario_rows = [row for row in rows if row["scenario"] == name]
        returns = [
            cast(Decimal, row["net_return"])
            for row in scenario_rows
            if row["net_return"] is not None
        ]
        mean_return = (
            sum(returns, Decimal("0")) / Decimal(len(returns))
            if returns
            else None
        )
        scenario_changed = any(
            isinstance(row["difference_against_base"], Decimal)
            and abs(row["difference_against_base"]) >= Decimal("0.10")
            for row in scenario_rows
        )
        rows.append(
            {
                "fold": "CONSOLIDATED",
                "scenario": name,
                "net_return": mean_return,
                "final_capital": None,
                "total_costs": sum(
                    (
                        cast(Decimal, row["total_costs"])
                        for row in scenario_rows
                        if row["total_costs"] is not None
                    ),
                    Decimal("0"),
                ),
                "trade_count": sum(cast(int, row["trade_count"]) for row in scenario_rows),
                "drawdown": max(
                    (
                        cast(Decimal, row["drawdown"])
                        for row in scenario_rows
                        if row["drawdown"] is not None
                    ),
                    default=None,
                ),
                "difference_against_base": None,
                "status": (
                    "POSITIVE"
                    if mean_return is not None and mean_return > 0
                    else "NEGATIVE"
                ),
                "warning": (
                    "COST_SENSITIVITY_HIGH"
                    if scenario_changed
                    else ""
                ),
            }
        )
    return tuple(rows)


def _return_percent(run: SegmentRun) -> Decimal | None:
    if run.result is None or run.result.metrics.initial_capital == 0:
        return None
    return run.result.metrics.net_return / run.result.metrics.initial_capital * Decimal("100")
