"""Exact artifact writer for the Sprint 3B.1 pullback experiment."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from adaptive_trader.domain.models import serialize_model
from adaptive_trader.research.pullback_experiment import (
    PullbackExperimentBundle,
)

ARTIFACT_NAMES = (
    "experiment_manifest.json",
    "hypothesis_catalog.json",
    "pullback_decision_funnel.csv",
    "pullback_reason_codes.csv",
    "development_results.csv",
    "development_walk_forward.csv",
    "validation_results.csv",
    "validation_walk_forward.csv",
    "market_comparison.csv",
    "side_contribution.csv",
    "cost_scenarios.csv",
    "pullback_entry_diagnostics.csv",
    "regime_loss_exit_diagnostics.csv",
    "concentration_analysis.csv",
    "bootstrap_uncertainty.json",
    "hypothesis_assessment.json",
    "future_holdout_plan.json",
    "pullback_hypothesis_report.md",
)

_CSV_FIELDS: dict[str, tuple[str, ...]] = {
    "pullback_decision_funnel.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "candles_evaluated",
        "trend_detected",
        "persistence_accepted",
        "pullbacks_detected",
        "pullbacks_valid",
        "resumptions",
        "long_signals",
        "short_signals",
        "risk_approvals",
        "executions",
        "closed_trades",
    ),
    "pullback_reason_codes.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "reason_code",
        "count",
    ),
    "development_results.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "scenario",
        "net_return_percent",
        "maximum_drawdown_percent",
        "trades",
    ),
    "development_walk_forward.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "scenario",
        "fold",
        "train_start",
        "train_end",
        "validation_start",
        "validation_end",
    ),
    "validation_results.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "scenario",
        "net_return_percent",
        "maximum_drawdown_percent",
        "trades",
    ),
    "validation_walk_forward.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "scenario",
        "fold",
        "train_start",
        "train_end",
        "validation_start",
        "validation_end",
        "locked_parameters",
    ),
    "market_comparison.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "net_return_percent",
        "maximum_drawdown_percent",
        "trades",
    ),
    "side_contribution.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "side",
        "trades",
        "net_pnl",
    ),
    "cost_scenarios.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "scenario",
        "net_return_percent",
        "total_costs",
        "net_funding",
        "warnings",
    ),
    "pullback_entry_diagnostics.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "side",
        "entry_time",
        "exit_time",
        "trend_persistence",
        "pullback_candles",
        "pullback_depth_atr",
        "distance_to_short_ema",
        "distance_to_long_ema",
        "atr_relative",
        "volume_ratio",
        "mfe",
        "mae",
        "holding_candles",
        "gross_pnl",
        "fees",
        "funding",
        "net_pnl",
        "exit_reason",
    ),
    "regime_loss_exit_diagnostics.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "side",
        "exit_time",
        "actual_net_pnl",
        "mfe_before_exit",
        "mae_before_exit",
        "counterfactual_outcome",
        "counterfactual_gross_pnl",
        "return_after_6_candles_percent",
        "return_after_12_candles_percent",
        "return_after_24_candles_percent",
        "offline_post_event_only",
        "used_by_strategy",
        "used_for_additional_selection",
    ),
    "concentration_analysis.csv": (
        "market",
        "mode",
        "variant_id",
        "period",
        "trades",
        "top_1_percent",
        "top_3_percent",
        "top_5_percent",
        "net_pnl_without_top_1",
        "net_pnl_without_top_3",
        "net_pnl_without_top_5",
    ),
}


def expected_pullback_artifact_names() -> tuple[str, ...]:
    return ARTIFACT_NAMES


def write_pullback_experiment_report(
    bundle: PullbackExperimentBundle,
    output_root: Path,
    *,
    git_commit: str,
    git_dirty: bool,
) -> Path:
    output_dir = output_root / bundle.experiment_id
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "experiment_id": bundle.experiment_id,
        "experiment_version": "PULLBACK_CONTINUATION_V1",
        "started_at": bundle.started_at,
        "completed_at": bundle.completed_at,
        "duration_seconds": bundle.duration_seconds,
        "initial_commit": git_commit,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "symbol": bundle.request.symbol,
        "interval": bundle.request.interval,
        "periods": bundle.request.periods,
        "development_policy": {
            "selection_source": "2022_2023_BASE_COST_ONLY",
            "train_days": 365,
            "validation_days": 90,
            "step_days": 90,
            "mode": "ROLLING",
            "maximum_pullback_variants_selected_per_market_mode": 2,
        },
        "validation_policy": {
            "period": "2024_ONLY",
            "window_days": 90,
            "parameters_locked": True,
            "selection_after_validation": False,
        },
        "consumed_reference": {
            "start": bundle.request.periods.consumed_start,
            "end": bundle.request.periods.consumed_end,
            "loaded": False,
            "used_for_selection": False,
            "used_for_backtest": False,
        },
        "catalog": {
            "path": bundle.catalog.path,
            "version": bundle.catalog.version,
            "canonical_hash": bundle.catalog.content_hash,
            "file_sha256": bundle.catalog_file_sha256,
            "variant_count": len(bundle.catalog.hypotheses),
            "changed_during_execution": False,
        },
        "markets": bundle.request.markets,
        "futures_modes": bundle.request.futures_modes,
        "leverages_executed": ("1",),
        "datasets": bundle.dataset_manifest,
        "development_selections": bundle.selections,
        "validation_locks": bundle.validation_locks,
        "cost_scenarios": ("LOW", "BASE", "HIGH", "STRESS"),
        "funding_policy": "REAL_FUNDING_UNCHANGED_ACROSS_COST_SCENARIOS",
        "spot_exit_priority": (
            "STOP_LOSS",
            "TAKE_PROFIT",
            "REGIME_LOSS_EXIT",
            "TIME_EXIT",
            "FORCED_END",
        ),
        "futures_exit_priority": (
            "FUNDING",
            "MARK_UPDATE",
            "LIQUIDATION",
            "STOP_LOSS",
            "TAKE_PROFIT",
            "REGIME_LOSS_EXIT",
            "TIME_EXIT",
            "FORCED_END",
        ),
        "intrabar_policy": "STOP_FIRST",
        "futures_liquidation_policy": "LIQUIDATION_FIRST",
        "network_used": False,
        "automatic_download": False,
        "authenticated_api_used": False,
        "api_key_used": False,
        "external_orders_sent": False,
        "paper_trading_enabled": False,
        "candidate_frozen": False,
        "warnings": bundle.warnings,
        "artifacts": ARTIFACT_NAMES,
    }
    catalog_payload = {
        "version": bundle.catalog.version,
        "path": bundle.catalog.path,
        "canonical_hash": bundle.catalog.content_hash,
        "file_sha256": bundle.catalog_file_sha256,
        "immutable_during_execution": True,
        "hypotheses": bundle.catalog.hypotheses,
    }
    assessment_payload = {
        "assessments": bundle.assessments,
        "development_selections": bundle.selections,
        "validation_locks": bundle.validation_locks,
        "candidate_frozen": False,
        "selection_after_validation": False,
    }
    bootstrap_payload = {
        "seed": 42,
        "iterations": 2000,
        "confidence_percent": "95",
        "unit": "CLOSED_TRADES",
        "candle_bootstrap": False,
        "results": bundle.bootstrap_uncertainty,
    }
    _write_json(output_dir / "experiment_manifest.json", manifest)
    _write_json(output_dir / "hypothesis_catalog.json", catalog_payload)
    _write_json(output_dir / "bootstrap_uncertainty.json", bootstrap_payload)
    _write_json(output_dir / "hypothesis_assessment.json", assessment_payload)
    _write_json(
        output_dir / "future_holdout_plan.json",
        bundle.future_holdout_plan,
    )
    csv_payloads: dict[str, tuple[dict[str, object], ...]] = {
        "pullback_decision_funnel.csv": bundle.pullback_decision_funnel,
        "pullback_reason_codes.csv": bundle.pullback_reason_codes,
        "development_results.csv": bundle.development_results,
        "development_walk_forward.csv": bundle.development_walk_forward,
        "validation_results.csv": bundle.validation_results,
        "validation_walk_forward.csv": bundle.validation_walk_forward,
        "market_comparison.csv": bundle.market_comparison,
        "side_contribution.csv": bundle.side_contribution,
        "cost_scenarios.csv": bundle.cost_scenarios,
        "pullback_entry_diagnostics.csv": (
            bundle.pullback_entry_diagnostics
        ),
        "regime_loss_exit_diagnostics.csv": (
            bundle.regime_loss_exit_diagnostics
        ),
        "concentration_analysis.csv": bundle.concentration_analysis,
    }
    for name, rows in csv_payloads.items():
        _write_csv(
            output_dir / name,
            rows,
            initial_fields=_CSV_FIELDS[name],
        )
    (output_dir / "pullback_hypothesis_report.md").write_text(
        _markdown(bundle),
        encoding="utf-8",
    )
    actual = tuple(sorted(path.name for path in output_dir.iterdir()))
    expected = tuple(sorted(ARTIFACT_NAMES))
    if actual != expected:
        raise RuntimeError("pullback report artifact set differs from pre-registration")
    return output_dir


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(
            serialize_model(payload),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, object]],
    *,
    initial_fields: tuple[str, ...],
) -> None:
    fields = list(initial_fields)
    for row in rows:
        for name in row:
            if name not in fields:
                fields.append(name)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            serialized = serialize_model(row)
            writer.writerow(
                {
                    field: _csv_value(serialized.get(field))
                    for field in fields
                }
            )


def _csv_value(value: object) -> object:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    if value is None:
        return ""
    return value


def _markdown(bundle: PullbackExperimentBundle) -> str:
    selections = "\n".join(
        (
            f"- {item.market}/{item.mode}: {item.status.value}; "
            f"selected={','.join(item.selected_variant_ids) or 'none'}"
        )
        for item in bundle.selections
    )
    classifications = "\n".join(
        (
            f"- {item.market}/{item.mode}/"
            f"{item.variant_id or 'NONE'}: {item.classification.value}"
        )
        for item in bundle.assessments
    )
    catalog = "\n".join(
        (
            f"- `{item.variant_id}`: analyzer={item.analyzer}, "
            f"persistence={item.trend_persistence_candles}, "
            f"time_exit={item.time_exit_candles}, "
            f"regime_loss_exit={item.regime_loss_exit}"
        )
        for item in bundle.catalog.hypotheses
    )
    development_table = _result_table(bundle.development_results)
    validation_table = _result_table(bundle.validation_results)
    funnel = "\n".join(
        (
            f"- {row['market']}/{row['mode']}/{row['variant_id']}/"
            f"{row['period']}: pullbacks={row['pullbacks_detected']}, "
            f"resumptions={row['resumptions']}, trades={row['closed_trades']}"
        )
        for row in bundle.pullback_decision_funnel
    )
    cost_warnings = sorted(
        {
            warning
            for row in bundle.cost_scenarios
            for warning in str(row.get("warnings", "")).split(";")
            if warning
        }
    )
    holdout_status = str(bundle.future_holdout_plan["status"])
    spot_dataset = bundle.dataset_manifest.get("spot")
    spot_gap_note = "Spot dataset not requested."
    if isinstance(spot_dataset, dict):
        gap_details = spot_dataset.get("gap_details", ())
        spot_gap_note = (
            f"Spot gap count: {spot_dataset.get('gap_count', 0)}. "
            f"Details: {gap_details}. Missing candles were not fabricated."
        )
    return f"""# Pullback Continuation Hypothesis — Sprint 3B.1

## 1. Hypothesis

Research-only continuation after a point-in-time controlled pullback on ETHUSDT 1h.
No statement in this report is a guarantee of profit.

## 2. Diagnostic Motivation

Prior diagnostics associated persistent TRENDING_UP/TRENDING_DOWN regimes with better
historical outcomes and transitions to RANGING with worse outcomes. This is association,
not demonstrated causality.

## 3. Periods Used

- Development: 2022-01-01 through 2023-12-31.
- Locked validation: 2024-01-01 through 2024-12-31.
- Future information was never supplied to an analyzer.
- {spot_gap_note}

## 4. Consumed Periods Excluded

2025-01-01 through 2026-07-01 was neither loaded nor backtested. It appears only as an
explicitly excluded consumed reference in the manifest.

## 5. Fixed Catalog

- Canonical hash: `{bundle.catalog.content_hash}`
- File SHA-256: `{bundle.catalog_file_sha256}`
{catalog}

## 6. Decision Funnel

{funnel}

## 7. Original Baseline

The existing deterministic analyzer remains unchanged and is included only as a reference.

## 8. Pullback Base

Requires three persistent trend candles, a one-to-six candle pullback between 0.10 and
1.0 ATR, and close-confirmed resumption with at most 1.0 ATR long-EMA extension.

## 9. Persistence 6

Uses the same fixed rules with six trend-persistence candles.

## 10. Time Exit

Uses the base pullback and one fixed 24-candle time exit.

## 11. Regime-Loss Exit

Detects regime loss only at close and executes no earlier than the next candle open.
Protective stop/target and Futures liquidation retain priority.

## 12. Spot

Spot is long-only, without leverage, margin, short selling, balance transfer, or real orders.

## 13. Futures Long

USD-M Futures long research used isolated 1x simulation and real stored funding.

## 14. Futures Short

Short logic mirrors the long setup semantically, while reporting outcomes independently.

## 15. Futures Long-Short

Long and short signals share one isolated simulated wallet and never hedge simultaneously.

## 16. Development

{development_table}

Selection:
{selections}

## 17. Validation

{validation_table}

Only baseline and up to two development-qualified variants per market/mode were evaluated.
All validation locks remained unchanged.

## 18. Costs

LOW, BASE, HIGH, and STRESS were executed. Funding was unchanged across scenarios.
Warnings: {", ".join(cost_warnings) if cost_warnings else "none"}.

## 19. Funding

Funding is reported separately from trading fees and was applied only to open Futures
positions from the persisted public dataset.

## 20. Concentration

Top-one, top-three, and top-five positive-trade concentration plus results excluding the
best trades are available in `concentration_analysis.csv`.

## 21. Bootstrap

Closed trades were resampled deterministically with seed 42, 2,000 iterations, and 95%
intervals. Candles were never bootstrapped.

## 22. Classification

{classifications}

No candidate was frozen in this sprint.

## 23. Future Holdout Plan

Status: `{holdout_status}`. Any future plan is plan-only, starts strictly after
2026-07-01, requires at least 90 calendar days and 20 closed trades, and restarts after
any configuration change.

## 24. Limitations

This is a deterministic historical simulation with approximate execution, maintenance
margin, and liquidation mechanics. Historical association is not causal evidence; costs,
latency, liquidity, regime classification, and future market structure may differ.
No network, authentication, download, paper trading, Testnet, or external order path ran.
"""


def _result_table(rows: tuple[dict[str, object], ...]) -> str:
    lines = (
        "| Market | Mode | Variant | Net return % | Drawdown % | Trades |",
        "|---|---|---|---:|---:|---:|",
    )
    body = tuple(
        (
            f"| {row['market']} | {row['mode']} | {row['variant_id']} | "
            f"{row['net_return_percent']} | {row['maximum_drawdown_percent']} | "
            f"{row['trades']} |"
        )
        for row in rows
    )
    return "\n".join((*lines, *body))
