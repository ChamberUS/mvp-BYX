"""High-level research services used by the CLI and offline validation."""

from __future__ import annotations

from pathlib import Path

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.research.costs import run_cost_scenarios
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
from adaptive_trader.research.sensitivity import local_variations, parameters_to_dict
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
    sensitivity_rows = _sensitivity_rows(split.test, config, experiment_runner)
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
    results = WalkForwardRunner().run(plan, config, selection_mode=selection_mode)
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
    ResearchReportWriter().write(
        output_dir=output_root / experiment_id,
        manifest=manifest,
        dataset=dataset,
        runs=validation_runs,
        summary=summary,
        diagnostics=diagnostics,
        benchmarks=tuple(item for run in validation_runs for item in run.benchmarks),
        regime_metrics=regime_metrics,
    )
    return results
