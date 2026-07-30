"""Configured cost scenarios for robustness reporting."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.research.experiment import ResearchExperimentRunner
from adaptive_trader.research.models import DatasetSegment


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
