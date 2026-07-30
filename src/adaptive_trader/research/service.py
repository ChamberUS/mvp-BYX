"""High-level research services used by the CLI and offline validation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.research.costs import run_cost_scenarios, run_cost_scenarios_by_fold
from adaptive_trader.research.datasets import _segment
from adaptive_trader.research.diagnostics import (
    candidate_assessment,
    decision_funnel_rows,
    detailed_regime_rows,
    entry_diagnostic_rows,
    entry_exit_decomposition_rows,
    exit_diagnostic_rows,
    hold_reason_rows,
    robustness_scorecard,
)
from adaptive_trader.research.experiment import ResearchExperimentRunner
from adaptive_trader.research.manifest import build_manifest
from adaptive_trader.research.models import (
    DatasetSegment,
    ResearchDataset,
    ResearchExperimentResult,
    SelectionMode,
    TemporalSplit,
    WalkForwardFoldResult,
    WalkForwardPlan,
)
from adaptive_trader.research.regime_analysis import analyze_regimes
from adaptive_trader.research.report import ResearchReportWriter
from adaptive_trader.research.robustness import consolidate_runs, diagnose
from adaptive_trader.research.sensitivity import (
    local_variations,
    parameters_to_dict,
    run_ofat,
)
from adaptive_trader.research.walk_forward import WalkForwardRunner

_OUTPUT_FILES = (
    "manifest.json",
    "dataset.json",
    "summary.json",
    "summary.csv",
    "folds.csv",
    "parameter_results.csv",
    "sensitivity.csv",
    "cost_scenarios.csv",
    "regime_metrics.csv",
    "warnings.json",
    "benchmarks.json",
    "trades.json",
    "report.md",
    "README.txt",
    "decision_funnel.json",
    "decision_funnel.csv",
    "hold_reason_analysis.csv",
    "entry_diagnostics.csv",
    "exit_diagnostics.csv",
    "entry_exit_decomposition.csv",
    "cost_scenarios_by_fold.csv",
    "detailed_regime_metrics.csv",
    "timeframe_comparison.csv",
    "sensitivity_ofat.csv",
    "robustness_scorecard.json",
    "candidate_assessment.json",
    "diagnostics_report.md",
)


def _experiment_id(name: str, dataset: ResearchDataset, config: TradingConfig) -> str:
    from adaptive_trader.research.manifest import config_hash

    safe_name = "-".join(part for part in name.strip().lower().split() if part) or "research"
    return f"{safe_name}-{dataset.content_hash[:10]}-{config_hash(config)[:10]}"


def _sensitivity_rows(
    segment: DatasetSegment, config: TradingConfig, runner: ResearchExperimentRunner
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for candidate in local_variations(config):
        run = runner.run_segment(segment, candidate)
        rows.append(
            {
                "parameters": parameters_to_dict(candidate),
                "net_return": (
                    run.result.metrics.net_return / run.result.metrics.initial_capital * 100
                    if run.result
                    else None
                ),
                "warning": "" if run.result else run.error or "failed",
            }
        )
    return tuple(rows)


def run_holdout_experiment(
    *,
    dataset: ResearchDataset,
    split: TemporalSplit,
    config: TradingConfig,
    experiment_name: str,
    output_root: Path,
    gap_policy: str,
    runner: ResearchExperimentRunner | None = None,
    include_sensitivity: bool = True,
) -> ResearchExperimentResult:
    experiment_runner = runner or ResearchExperimentRunner()
    segments = (split.train, split.validation, split.test)
    runs = experiment_runner.run_segments(segments, config)
    summary = consolidate_runs(runs)
    diagnostics = diagnose(
        runs[0].result,
        runs[1].result,
        runs,
    )
    experiment_id = _experiment_id(experiment_name, dataset, config)
    output_dir = output_root / experiment_id
    manifest = build_manifest(
        experiment_id=experiment_id,
        experiment_name=experiment_name,
        dataset=dataset,
        segments=segments,
        config=config,
        output_files=_OUTPUT_FILES,
        gap_policy=gap_policy,
        split={
            "split_id": split.split_id,
            "train": split.train.content_hash,
            "validation": split.validation.content_hash,
            "test": split.test.content_hash,
        },
    )
    sensitivity_rows = (
        _sensitivity_rows(split.test, config, experiment_runner)
        if include_sensitivity
        else ()
    )
    cost_rows = run_cost_scenarios(split.test, config, experiment_runner)
    regime_metrics = tuple(
        metric
        for run in runs
        if run.result is not None
        for metric in analyze_regimes(
            run.segment,
            run.result,
            short_period=config.short_ema_period,
            long_period=config.long_ema_period,
            maximum_atr_relative=config.maximum_atr_relative,
        )
    )
    cost_by_fold = run_cost_scenarios_by_fold(runs, config, experiment_runner)
    writer = ResearchReportWriter()
    writer.write(
        output_dir=output_dir,
        manifest=manifest,
        dataset=dataset,
        runs=runs,
        summary=summary,
        diagnostics=diagnostics,
        benchmarks=tuple(item for run in runs for item in run.benchmarks),
        sensitivity_rows=sensitivity_rows,
        cost_rows=cost_rows,
        regime_metrics=regime_metrics,
        decision_funnel_rows=decision_funnel_rows(runs, config),
        hold_reason_rows=hold_reason_rows(runs),
        entry_diagnostic_rows=entry_diagnostic_rows(runs),
        exit_diagnostic_rows=exit_diagnostic_rows(runs),
        cost_scenarios_by_fold_rows=cost_by_fold,
        detailed_regime_rows=detailed_regime_rows(runs),
        scorecard=robustness_scorecard(runs, config),
        candidate={
            "status": "INCONCLUSIVE",
            "reason": (
                "existing holdout includes a consumed test period; "
                "no candidate selection performed"
            ),
            "uses_consumed_test_period": True,
        },
    )
    return ResearchExperimentResult(
        experiment_id=experiment_id,
        manifest=manifest,
        dataset=dataset,
        segments=runs,
        summary=summary,
        benchmarks=tuple(item for run in runs for item in run.benchmarks),
        diagnostics=diagnostics,
        warnings=tuple(dict.fromkeys((*manifest.warnings, *diagnostics.warnings))),
    )


def run_walk_forward_experiment(
    *,
    dataset: ResearchDataset,
    plan: WalkForwardPlan,
    config: TradingConfig,
    experiment_name: str,
    output_root: Path,
    gap_policy: str,
    selection_mode: SelectionMode = SelectionMode.FIXED_PARAMETERS,
) -> tuple[WalkForwardFoldResult, ...]:
    experiment_runner = ResearchExperimentRunner()
    results = WalkForwardRunner(experiment_runner).run(
        plan, config, selection_mode=selection_mode
    )
    validation_runs = tuple(
        item.validation for item in results if item.validation is not None
    )
    if not validation_runs:
        raise ValueError("walk-forward produced no validation results")
    train_result = results[0].train.result if results[0].train is not None else None
    validation_result = validation_runs[0].result
    summary = consolidate_runs(validation_runs)
    diagnostics = diagnose(train_result, validation_result, validation_runs)
    experiment_id = f"{_experiment_id(experiment_name, dataset, config)}-walk"
    segments = tuple(
        item
        for fold in plan.folds
        for item in (fold.train, fold.validation)
    )
    manifest = build_manifest(
        experiment_id=experiment_id,
        experiment_name=experiment_name,
        dataset=dataset,
        segments=segments,
        config=config,
        output_files=_OUTPUT_FILES,
        gap_policy=gap_policy,
        split={
            "plan_id": plan.plan_id,
            "mode": plan.mode.value,
            "fold_count": len(plan.folds),
        },
    )
    regime_metrics = tuple(
        metric
        for run in validation_runs
        if run.result is not None
        for metric in analyze_regimes(
            run.segment,
            run.result,
            short_period=config.short_ema_period,
            long_period=config.long_ema_period,
            maximum_atr_relative=config.maximum_atr_relative,
        )
    )
    cost_by_fold = run_cost_scenarios_by_fold(validation_runs, config, experiment_runner)
    cost_rows = tuple(
        {
            "scenario": row["scenario"],
            "net_return": row["net_return"],
            "gross_return": None,
            "total_costs": row["total_costs"],
            "warning": row["warning"],
        }
        for row in cost_by_fold
        if row["fold"] == "CONSOLIDATED"
    )
    ResearchReportWriter().write(
        output_dir=output_root / experiment_id,
        manifest=manifest,
        dataset=dataset,
        runs=validation_runs,
        summary=summary,
        diagnostics=diagnostics,
        benchmarks=tuple(item for run in validation_runs for item in run.benchmarks),
        cost_rows=cost_rows,
        regime_metrics=regime_metrics,
        decision_funnel_rows=decision_funnel_rows(validation_runs, config),
        hold_reason_rows=hold_reason_rows(validation_runs),
        entry_diagnostic_rows=entry_diagnostic_rows(validation_runs),
        exit_diagnostic_rows=exit_diagnostic_rows(validation_runs),
        cost_scenarios_by_fold_rows=cost_by_fold,
        detailed_regime_rows=detailed_regime_rows(validation_runs),
        scorecard=robustness_scorecard(validation_runs, config),
        candidate={
            "status": "INCONCLUSIVE",
            "reason": (
                "walk-forward validation includes a consumed period; "
                "no candidate selection performed"
            ),
            "uses_consumed_test_period": True,
        },
    )
    return results


def run_diagnostics_experiment(
    *,
    dataset: ResearchDataset,
    development_start: datetime,
    development_end: datetime,
    validation_start: datetime,
    validation_end: datetime,
    config: TradingConfig,
    experiment_name: str,
    output_root: Path,
    gap_policy: str,
    excluded_period: tuple[datetime, datetime] | None = None,
    maximum_parameter_combinations: int = 60,
) -> ResearchExperimentResult:
    """Run the diagnostic funnel only on development and validation data."""

    development = _segment(
        dataset,
        name="development",
        evaluation_start=development_start,
        evaluation_end=development_end,
        warmup_candles=config.warmup_candles,
    )
    validation = _segment(
        dataset,
        name="validation",
        evaluation_start=validation_start,
        evaluation_end=validation_end,
        warmup_candles=config.warmup_candles,
    )
    segments = (development, validation)
    runner = ResearchExperimentRunner()
    runs = runner.run_segments(segments, config)
    summary = consolidate_runs(runs)
    diagnostics = diagnose(runs[0].result, runs[1].result, runs)
    experiment_id = _experiment_id(experiment_name, dataset, config)
    excluded_warning = (
        f"CONSUMED_TEST_EXCLUDED: {excluded_period[0]} -> {excluded_period[1]}"
        if excluded_period
        else "CONSUMED_TEST_EXCLUDED: no consumed test candles were loaded"
    )
    manifest = build_manifest(
        experiment_id=experiment_id,
        experiment_name=experiment_name,
        dataset=dataset,
        segments=segments,
        config=config,
        output_files=_OUTPUT_FILES,
        gap_policy=gap_policy,
        split={
            "development": development.content_hash,
            "validation": validation.content_hash,
            "consumed_test_used_for_selection": False,
            "excluded_period": (
                {
                    "start": excluded_period[0].isoformat(),
                    "end": excluded_period[1].isoformat(),
                }
                if excluded_period
                else None
            ),
        },
        warnings=(excluded_warning,),
    )
    cost_by_fold = run_cost_scenarios_by_fold(runs, config, runner)
    writer = ResearchReportWriter()
    writer.write(
        output_dir=output_root / experiment_id,
        manifest=manifest,
        dataset=dataset,
        runs=runs,
        summary=summary,
        diagnostics=diagnostics,
        benchmarks=tuple(item for run in runs for item in run.benchmarks),
        cost_rows=run_cost_scenarios(validation, config, runner),
        regime_metrics=tuple(
            metric
            for run in runs
            if run.result is not None
            for metric in analyze_regimes(
                run.segment,
                run.result,
                short_period=config.short_ema_period,
                long_period=config.long_ema_period,
                maximum_atr_relative=config.maximum_atr_relative,
            )
        ),
        decision_funnel_rows=decision_funnel_rows(runs, config),
        hold_reason_rows=hold_reason_rows(runs),
        entry_diagnostic_rows=entry_diagnostic_rows(runs),
        exit_diagnostic_rows=exit_diagnostic_rows(runs),
        entry_exit_decomposition_rows=tuple(
            row
            for segment in segments
            for row in entry_exit_decomposition_rows(segment, config, runner)
        ),
        cost_scenarios_by_fold_rows=cost_by_fold,
        detailed_regime_rows=detailed_regime_rows(runs),
        sensitivity_ofat_rows=run_ofat(
            segments,
            config,
            runner,
            maximum_parameter_combinations=maximum_parameter_combinations,
        ),
        scorecard=robustness_scorecard(runs, config),
        candidate=candidate_assessment(runs, cost_rows=cost_by_fold),
    )
    return ResearchExperimentResult(
        experiment_id=experiment_id,
        manifest=manifest,
        dataset=dataset,
        segments=runs,
        summary=summary,
        benchmarks=tuple(item for run in runs for item in run.benchmarks),
        diagnostics=diagnostics,
        warnings=tuple(
            dict.fromkeys((*manifest.warnings, excluded_warning, *diagnostics.warnings))
        ),
    )
