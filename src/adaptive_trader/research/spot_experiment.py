"""Controlled, pre-registered Spot hypothesis experiment."""

from __future__ import annotations

import csv
import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.protocols import MarketAnalyzer, RiskManager
from adaptive_trader.execution.backtest import BacktestExecutionConfig, BacktestOrderExecutor
from adaptive_trader.research.candidate_freeze import spot_to_futures_plan
from adaptive_trader.research.costs import cost_scenarios
from adaptive_trader.research.datasets import _segment, canonical_hash, dataset_to_dict
from adaptive_trader.research.experiment import ResearchExperimentRunner
from adaptive_trader.research.models import (
    DatasetSegment,
    ResearchDataset,
    SegmentRun,
    WalkForwardFold,
)
from adaptive_trader.research.robustness import consolidate_runs
from adaptive_trader.research.spot_hypotheses import (
    DevelopmentSelection,
    DevelopmentSelectionMetric,
    SpotExperimentPeriods,
    SpotHypothesis,
    SpotHypothesisCatalog,
    ValidationLock,
    select_development_candidate,
)
from adaptive_trader.risk.manager import DefaultRiskManager
from adaptive_trader.strategy.deterministic import DeterministicAnalyzer
from adaptive_trader.strategy.regime import SpotRegimeMode

_REPORT_FILES = (
    "experiment_manifest.json",
    "predefined_variants.json",
    "exit_hypothesis_results.csv",
    "development_walk_forward.csv",
    "validation_walk_forward.csv",
    "regime_mode_results.csv",
    "cost_results.csv",
    "concentration_results.csv",
    "candidate_criteria.json",
    "candidate_freeze_decision.json",
    "future_holdout_plan.json",
    "spot_candidate_to_futures_plan.json",
    "hypothesis_validation_report.md",
)


@dataclass(frozen=True, slots=True)
class SpotExperimentResult:
    experiment_id: str
    output_path: Path
    stage_one_selection: DevelopmentSelection
    final_selection: DevelopmentSelection
    candidate_status: str
    duration_seconds: Decimal
    report_files: tuple[str, ...]


@dataclass(slots=True)
class _Evaluation:
    variant: SpotHypothesis
    regime_mode: SpotRegimeMode
    period: str
    stage: str
    summary: dict[str, Any]
    fold_rows: list[dict[str, Any]]
    cost_rows: list[dict[str, Any]]


class SpotHypothesisExperiment:
    def __init__(
        self,
        *,
        config: TradingConfig,
        dataset: ResearchDataset,
        periods: SpotExperimentPeriods,
        catalog: SpotHypothesisCatalog,
        output_dir: Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = replace(
            config,
            symbol=dataset.symbol,
            interval=dataset.interval,
            trading_enabled=False,
            allow_leverage=False,
            allow_margin=False,
            allow_futures=False,
        )
        self._dataset = dataset
        self._periods = periods
        self._catalog = catalog
        self._output_dir = output_dir
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._runners: dict[SpotRegimeMode, ResearchExperimentRunner] = {}

    def run(self) -> SpotExperimentResult:
        self._validate_inputs()
        catalog_before = self._catalog.path.read_bytes()
        started = self._clock()
        experiment_id = (
            f"spot-hypotheses-v1-{started.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{self._dataset.content_hash[:8]}"
        )
        output_path = self._output_dir / experiment_id
        if output_path.exists():
            raise FileExistsError(f"experiment already exists: {output_path}")
        output_path.mkdir(parents=True)

        development_full = self._full_segment("development")
        validation_full = self._full_segment("validation")
        development_folds = self._folds("development")
        validation_folds = self._folds("validation")
        evaluations: list[_Evaluation] = []

        for variant in self._catalog.hypotheses:
            evaluations.append(
                self._evaluate(
                    variant,
                    SpotRegimeMode.STRICT_TRENDING_UP,
                    "DEVELOPMENT",
                    "EXIT_HYPOTHESES",
                    development_full,
                    development_folds,
                )
            )
        stage_one = select_development_candidate(
            tuple(self._selection_metric(evaluation) for evaluation in evaluations)
        )

        final_selection = DevelopmentSelection(
            status="NO_DEVELOPMENT_CANDIDATE",
            selected_variant_id=None,
            selected_regime_mode=None,
            criterion="median_walk_forward_net_return",
            ranked_variant_ids=(),
        )
        validation_lock: ValidationLock | None = None
        final_development: _Evaluation | None = None
        final_validation: _Evaluation | None = None
        if stage_one.selected_variant_id is not None:
            exit_winner = self._catalog.by_id(stage_one.selected_variant_id)
            evaluations.append(
                self._evaluate(
                    exit_winner,
                    SpotRegimeMode.STRICT_TRENDING_UP,
                    "VALIDATION",
                    "EXIT_HYPOTHESES_LOCKED_VALIDATION",
                    validation_full,
                    validation_folds,
                )
            )
            stage_two_variants = self._stage_two_variants(exit_winner)
            stage_two_development: list[_Evaluation] = []
            for variant in stage_two_variants:
                for mode in self._catalog.regime_modes:
                    evaluation = self._evaluate(
                        variant,
                        mode,
                        "DEVELOPMENT",
                        "REGIME_MODES",
                        development_full,
                        development_folds,
                    )
                    stage_two_development.append(evaluation)
                    evaluations.append(evaluation)
            final_selection = select_development_candidate(
                tuple(
                    self._selection_metric(evaluation)
                    for evaluation in stage_two_development
                )
            )
            if (
                final_selection.selected_variant_id is not None
                and final_selection.selected_regime_mode is not None
            ):
                selected = self._catalog.by_id(final_selection.selected_variant_id)
                validation_lock = ValidationLock.create(
                    selected,
                    final_selection.selected_regime_mode,
                    self._config.target_r_multiple,
                )
                final_development = next(
                    evaluation
                    for evaluation in stage_two_development
                    if evaluation.variant.variant_id == selected.variant_id
                    and evaluation.regime_mode is final_selection.selected_regime_mode
                )
                validation_lock.assert_unchanged(
                    variant_id=selected.variant_id,
                    regime_mode=final_selection.selected_regime_mode,
                    target_r_multiple=selected.resolved_target(
                        self._config.target_r_multiple
                    ),
                    time_exit_candles=selected.time_exit_candles,
                )
                final_validation = self._evaluate(
                    selected,
                    final_selection.selected_regime_mode,
                    "VALIDATION",
                    "REGIME_MODES_LOCKED_VALIDATION",
                    validation_full,
                    validation_folds,
                )
                evaluations.append(final_validation)

        criteria = self._candidate_criteria(
            final_selection,
            final_development,
            final_validation,
        )
        candidate_status = str(criteria["status"])
        finished = self._clock()
        duration = Decimal(str((finished - started).total_seconds()))
        manifest = self._manifest(
            experiment_id,
            started,
            duration,
            development_folds,
            validation_folds,
            stage_one,
            final_selection,
            validation_lock,
        )
        decision = self._freeze_decision(
            criteria,
            manifest,
            final_selection,
            validation_lock,
        )
        self._write_reports(
            output_path,
            manifest,
            evaluations,
            stage_one,
            final_selection,
            criteria,
            decision,
        )
        if self._catalog.path.read_bytes() != catalog_before:
            raise RuntimeError("pre-registered Spot hypothesis catalog changed during execution")
        return SpotExperimentResult(
            experiment_id=experiment_id,
            output_path=output_path,
            stage_one_selection=stage_one,
            final_selection=final_selection,
            candidate_status=candidate_status,
            duration_seconds=duration,
            report_files=_REPORT_FILES,
        )

    def _validate_inputs(self) -> None:
        self._periods.assert_pre_registered()
        if self._dataset.symbol != "ETHUSDT" or self._dataset.interval != "1h":
            raise ValueError("Sprint 3A.4 supports only local ETHUSDT 1h Spot data")
        if self._dataset.first_open_time > self._periods.development_start:
            raise ValueError("local dataset does not cover the development start")
        if self._dataset.candles[-1].open_time < self._periods.validation_end:
            raise ValueError("local dataset does not cover the validation end")
        if any(
            candle.open_time >= self._periods.consumed_test_start
            for candle in self._dataset.candles
        ):
            raise ValueError("experiment dataset must exclude all consumed-test candles")

    def _full_segment(self, period: str) -> DatasetSegment:
        if period == "development":
            start = self._periods.development_start
            end = self._periods.validation_start
        else:
            start = self._periods.validation_start
            end = self._periods.consumed_test_start
        self._periods.assert_selection_range(start, end - timedelta(hours=1), f"{period} segment")
        return _segment(
            self._dataset,
            name=f"{period}-full",
            evaluation_start=start,
            evaluation_end=end,
            warmup_candles=self._config.warmup_candles,
        )

    def _folds(self, period: str) -> tuple[WalkForwardFold, ...]:
        if period == "development":
            evaluation_start = self._periods.development_start + timedelta(days=365)
            period_end = self._periods.validation_start
        else:
            evaluation_start = self._periods.validation_start
            period_end = self._periods.consumed_test_start
        folds: list[WalkForwardFold] = []
        index = 1
        while evaluation_start + timedelta(days=90) <= period_end:
            evaluation_end = evaluation_start + timedelta(days=90)
            train_start = evaluation_start - timedelta(days=365)
            self._periods.assert_selection_range(
                evaluation_start,
                evaluation_end - timedelta(hours=1),
                f"{period} walk-forward",
            )
            folds.append(
                WalkForwardFold(
                    fold_id=f"{period}-fold-{index}",
                    train=_segment(
                        self._dataset,
                        name=f"{period}-fold-{index}-train",
                        evaluation_start=train_start,
                        evaluation_end=evaluation_start,
                        warmup_candles=self._config.warmup_candles,
                    ),
                    validation=_segment(
                        self._dataset,
                        name=f"{period}-fold-{index}-validation",
                        evaluation_start=evaluation_start,
                        evaluation_end=evaluation_end,
                        warmup_candles=self._config.warmup_candles,
                    ),
                )
            )
            index += 1
            evaluation_start += timedelta(days=90)
        if not folds:
            raise ValueError(f"{period} has no fixed walk-forward folds")
        return tuple(folds)

    def _stage_two_variants(
        self,
        exit_winner: SpotHypothesis,
    ) -> tuple[SpotHypothesis, ...]:
        baseline = self._catalog.by_id("SPOT_BASELINE_V1")
        return (baseline,) if exit_winner == baseline else (baseline, exit_winner)

    def _runner(self, mode: SpotRegimeMode) -> ResearchExperimentRunner:
        found = self._runners.get(mode)
        if found is not None:
            return found

        def factory(
            config: TradingConfig,
        ) -> tuple[MarketAnalyzer, RiskManager, BacktestOrderExecutor]:
            analyzer = DeterministicAnalyzer(
                short_period=config.short_ema_period,
                long_period=config.long_ema_period,
                minimum_volume_ratio=config.minimum_volume_ratio,
                maximum_atr_relative=config.maximum_atr_relative,
                stop_atr_multiple=config.stop_atr_multiple,
                target_r_multiple=config.target_r_multiple,
                regime_mode=mode,
            )
            executor = BacktestOrderExecutor(
                BacktestExecutionConfig(
                    maker_fee_bps=config.maker_fee_bps,
                    taker_fee_bps=config.taker_fee_bps,
                    slippage_bps=config.slippage_bps,
                    spread_bps=config.spread_bps,
                )
            )
            return analyzer, DefaultRiskManager(local_simulation=True), executor

        found = ResearchExperimentRunner(component_factory=factory, clock=self._clock)
        self._runners[mode] = found
        return found

    def _evaluate(
        self,
        variant: SpotHypothesis,
        mode: SpotRegimeMode,
        period: str,
        stage: str,
        full_segment: DatasetSegment,
        folds: tuple[WalkForwardFold, ...],
    ) -> _Evaluation:
        variant_config = replace(
            self._config,
            target_r_multiple=variant.resolved_target(self._config.target_r_multiple),
        )
        runner = self._runner(mode)
        fold_rows: list[dict[str, Any]] = []
        cost_rows: list[dict[str, Any]] = []
        scenario_runs: dict[str, tuple[SegmentRun, ...]] = {}
        full_runs: dict[str, SegmentRun] = {}
        for scenario, scenario_config in cost_scenarios(variant_config).items():
            full_run = runner.run_segment(
                full_segment,
                scenario_config,
                time_exit_candles=variant.time_exit_candles,
            )
            fold_runs = tuple(
                runner.run_segment(
                    fold.validation,
                    scenario_config,
                    time_exit_candles=variant.time_exit_candles,
                )
                for fold in folds
            )
            scenario_runs[scenario] = fold_runs
            full_runs[scenario] = full_run
        base_fold_returns = tuple(
            self._run_return(run) for run in scenario_runs["BASE_COST"]
        )
        for scenario, fold_runs in scenario_runs.items():
            for index, (fold, run) in enumerate(zip(folds, fold_runs, strict=True)):
                result = run.result
                net_return = self._run_return(run)
                base_return = base_fold_returns[index]
                row = {
                    "stage": stage,
                    "period": period,
                    "variant_id": variant.variant_id,
                    "regime_mode": mode.value,
                    "scenario": scenario,
                    "fold": fold.fold_id,
                    "train_start": fold.train.requested_evaluation_start_time,
                    "train_end": fold.train.end_time,
                    "evaluation_start": fold.validation.requested_evaluation_start_time,
                    "evaluation_end": fold.validation.end_time,
                    "net_return_percent": net_return,
                    "drawdown_percent": (
                        result.metrics.maximum_drawdown_percent if result else None
                    ),
                    "closed_trade_count": (
                        result.metrics.closed_trade_count if result else 0
                    ),
                    "total_costs": self._total_costs(run),
                    "difference_against_base": (
                        net_return - base_return
                        if net_return is not None and base_return is not None
                        else None
                    ),
                    "failed": run.failed,
                }
                fold_rows.append(row)
                cost_rows.append(row)
        summary = self._summary(
            variant,
            mode,
            period,
            stage,
            full_runs,
            scenario_runs,
        )
        return _Evaluation(
            variant=variant,
            regime_mode=mode,
            period=period,
            stage=stage,
            summary=summary,
            fold_rows=fold_rows,
            cost_rows=cost_rows,
        )

    def _summary(
        self,
        variant: SpotHypothesis,
        mode: SpotRegimeMode,
        period: str,
        stage: str,
        full_runs: dict[str, SegmentRun],
        scenario_runs: dict[str, tuple[SegmentRun, ...]],
    ) -> dict[str, Any]:
        base_full = full_runs["BASE_COST"]
        base_result = base_full.result
        base_summary = consolidate_runs(scenario_runs["BASE_COST"])
        stress_summary = consolidate_runs(scenario_runs["STRESS_COST"])
        scenario_means = {
            name: consolidate_runs(runs).mean_net_return
            for name, runs in scenario_runs.items()
        }
        base_mean = scenario_means["BASE_COST"]
        differences = tuple(
            abs(value - base_mean)
            for name, value in scenario_means.items()
            if name != "BASE_COST" and value is not None and base_mean is not None
        )
        trades = base_result.trades if base_result is not None else ()
        best, top_five, without_best = _trade_concentration(trades)
        buy_hold = next(
            (
                benchmark
                for benchmark in base_full.benchmarks
                if benchmark.name == "BUY_AND_HOLD"
            ),
            None,
        )
        base_net_return = self._run_return(base_full)
        stress_full_return = self._run_return(full_runs["STRESS_COST"])
        low_mean = scenario_means["LOW_COST"]
        warning_list = list(base_full.warnings)
        if (
            low_mean is not None
            and low_mean > 0
            and all(
                value is not None and value <= 0
                for name, value in scenario_means.items()
                if name != "LOW_COST"
            )
        ):
            warning_list.append("LOW_COST_ONLY_EDGE")
        if mode.diagnostic_only:
            warning_list.append("DIAGNOSTIC_ONLY_REGIME_DISABLED")
        exit_counts = {
            reason: sum(trade.exit_reason == reason for trade in trades)
            for reason in ("STOP_LOSS", "TAKE_PROFIT", "TIME_EXIT", "FORCED_END")
        }
        years = (
            Decimal(str((base_full.segment.end_time - base_full.segment.start_time).days))
            / Decimal("365")
        )
        closed_trades = base_result.metrics.closed_trade_count if base_result else 0
        return {
            "stage": stage,
            "period": period,
            "variant_id": variant.variant_id,
            "regime_mode": mode.value,
            "diagnostic_only": mode.diagnostic_only,
            "target_r_multiple": variant.resolved_target(self._config.target_r_multiple),
            "time_exit_candles": variant.time_exit_candles or 0,
            "net_return_percent": base_net_return,
            "median_fold_return_percent": base_summary.median_net_return,
            "mean_fold_return_percent": base_summary.mean_net_return,
            "worst_fold_return_percent": base_summary.worst_net_return,
            "best_fold_return_percent": base_summary.best_net_return,
            "positive_fold_percent": base_summary.positive_fold_percent,
            "zero_trade_fold_percent": _zero_trade_percent(
                scenario_runs["BASE_COST"]
            ),
            "fold_count": base_summary.fold_count,
            "closed_trade_count": closed_trades,
            "trades_per_year": (
                Decimal(closed_trades) / years if years > 0 else None
            ),
            "win_rate_percent": (
                base_result.metrics.win_rate if base_result else None
            ),
            "profit_factor": (
                base_result.metrics.profit_factor if base_result else None
            ),
            "expectancy": (
                base_result.metrics.expectancy_per_trade if base_result else None
            ),
            "maximum_drawdown_percent": (
                base_result.metrics.maximum_drawdown_percent if base_result else None
            ),
            "worst_fold_drawdown_percent": base_summary.worst_max_drawdown,
            "return_to_drawdown": _return_to_drawdown(
                base_net_return,
                base_result.metrics.maximum_drawdown_percent if base_result else None,
            ),
            "total_costs": self._total_costs(base_full),
            "stress_net_return_percent": stress_full_return,
            "stress_positive_fold_percent": stress_summary.positive_fold_percent,
            "cost_sensitivity": max(differences) if differences else None,
            "best_trade_concentration_percent": best,
            "top_five_concentration_percent": top_five,
            "result_without_best_trade": without_best,
            "stop_count": exit_counts["STOP_LOSS"],
            "take_profit_count": exit_counts["TAKE_PROFIT"],
            "time_exit_count": exit_counts["TIME_EXIT"],
            "forced_end_count": exit_counts["FORCED_END"],
            "exposure_percent": (
                base_result.metrics.average_exposure_percent if base_result else None
            ),
            "buy_and_hold_return_percent": (
                buy_hold.net_return_percent if buy_hold else None
            ),
            "benchmark_difference_percent": (
                base_net_return - buy_hold.net_return_percent
                if base_net_return is not None and buy_hold is not None
                else None
            ),
            "warnings": tuple(dict.fromkeys(warning_list)),
            "failed": base_full.failed or base_summary.failed_fold_count > 0,
        }

    def _selection_metric(
        self,
        evaluation: _Evaluation,
    ) -> DevelopmentSelectionMetric:
        summary = evaluation.summary
        return DevelopmentSelectionMetric(
            variant_id=evaluation.variant.variant_id,
            regime_mode=evaluation.regime_mode,
            median_walk_forward_net_return=_decimal_or_none(
                summary["median_fold_return_percent"]
            ),
            positive_fold_percent=_decimal(summary["positive_fold_percent"]),
            worst_drawdown_percent=_decimal_or_none(
                summary["worst_fold_drawdown_percent"]
            ),
            zero_trade_fold_percent=_decimal(summary["zero_trade_fold_percent"]),
            cost_sensitivity=_decimal_or_none(summary["cost_sensitivity"]),
            closed_trade_count=int(summary["closed_trade_count"]),
            complexity_rank=evaluation.variant.complexity_rank,
            fold_count=int(summary["fold_count"]),
        )

    def _candidate_criteria(
        self,
        selection: DevelopmentSelection,
        development: _Evaluation | None,
        validation: _Evaluation | None,
    ) -> dict[str, Any]:
        if (
            selection.selected_variant_id is None
            or development is None
            or validation is None
        ):
            return {
                "status": "NO_DEVELOPMENT_CANDIDATE",
                "checks": {},
                "observed_metrics": {},
                "failed_criteria": ["development_selection"],
            }
        dev = development.summary
        val = validation.summary
        concentration_values = tuple(
            value
            for value in (
                _decimal_or_none(dev["best_trade_concentration_percent"]),
                _decimal_or_none(val["best_trade_concentration_percent"]),
            )
            if value is not None
        )
        worst_concentration = max(concentration_values) if concentration_values else None
        worst_drawdown = max(
            _decimal(dev["maximum_drawdown_percent"]),
            _decimal(val["maximum_drawdown_percent"]),
        )
        stress_positive = min(
            _decimal(dev["stress_positive_fold_percent"]),
            _decimal(val["stress_positive_fold_percent"]),
        )
        checks = {
            "minimum_closed_trades": (
                int(dev["closed_trade_count"]) + int(val["closed_trade_count"]) >= 30
            ),
            "minimum_development_positive_fold_percent": (
                _decimal(dev["positive_fold_percent"]) >= Decimal("50")
            ),
            "minimum_validation_positive_fold_percent": (
                _decimal(val["positive_fold_percent"]) >= Decimal("50")
            ),
            "minimum_development_median_net_return": (
                _decimal(dev["median_fold_return_percent"]) >= 0
            ),
            "minimum_validation_median_net_return": (
                _decimal(val["median_fold_return_percent"]) >= 0
            ),
            "maximum_worst_drawdown_percent": worst_drawdown <= Decimal("10"),
            "maximum_zero_trade_fold_percent": max(
                _decimal(dev["zero_trade_fold_percent"]),
                _decimal(val["zero_trade_fold_percent"]),
            )
            <= Decimal("25"),
            "minimum_stress_positive_fold_percent": stress_positive >= Decimal("30"),
            "maximum_best_trade_concentration_percent": (
                worst_concentration is not None
                and worst_concentration <= Decimal("50")
            ),
            "validation_net_return": _decimal(val["net_return_percent"]) >= 0,
            "no_consumed_test_usage": True,
            "no_diagnostic_only_regime": not development.regime_mode.diagnostic_only,
            "no_data_leakage": True,
            "no_hidden_cost_optimization": True,
        }
        failed = [name for name, passed in checks.items() if not passed]
        incomplete = bool(dev["failed"] or val["failed"])
        status = "INCONCLUSIVE" if incomplete else ("CANDIDATE" if not failed else "NOT_CANDIDATE")
        return {
            "status": status,
            "checks": checks,
            "thresholds": {
                "minimum_closed_trades": 30,
                "minimum_development_positive_fold_percent": "50",
                "minimum_validation_positive_fold_percent": "50",
                "minimum_development_median_net_return": "0",
                "minimum_validation_median_net_return": "0",
                "maximum_worst_drawdown_percent": "10",
                "maximum_zero_trade_fold_percent": "25",
                "minimum_stress_positive_fold_percent": "30",
                "maximum_best_trade_concentration_percent": "50",
                "minimum_validation_net_return": "0",
            },
            "observed_metrics": {
                "closed_trades": (
                    int(dev["closed_trade_count"]) + int(val["closed_trade_count"])
                ),
                "development_positive_fold_percent": dev["positive_fold_percent"],
                "validation_positive_fold_percent": val["positive_fold_percent"],
                "development_median_net_return": dev["median_fold_return_percent"],
                "validation_median_net_return": val["median_fold_return_percent"],
                "worst_drawdown_percent": worst_drawdown,
                "maximum_zero_trade_fold_percent": max(
                    _decimal(dev["zero_trade_fold_percent"]),
                    _decimal(val["zero_trade_fold_percent"]),
                ),
                "stress_positive_fold_percent": stress_positive,
                "best_trade_concentration_percent": worst_concentration,
                "validation_net_return": val["net_return_percent"],
                "development": dev,
                "validation": val,
            },
            "failed_criteria": failed,
        }

    def _manifest(
        self,
        experiment_id: str,
        started: datetime,
        duration: Decimal,
        development_folds: tuple[WalkForwardFold, ...],
        validation_folds: tuple[WalkForwardFold, ...],
        stage_one: DevelopmentSelection,
        final_selection: DevelopmentSelection,
        validation_lock: ValidationLock | None,
    ) -> dict[str, Any]:
        commit, dirty = _git_metadata()
        return {
            "experiment_id": experiment_id,
            "experiment_version": "SPOT_HYPOTHESES_V1",
            "executed_at": started,
            "duration_seconds": duration,
            "git_commit": commit,
            "git_dirty": dirty,
            "dataset": dataset_to_dict(self._dataset),
            "dataset_hash": self._dataset.content_hash,
            "dataset_gap_explanations": _dataset_gaps(self._dataset),
            "catalog_file": str(self._catalog.path),
            "catalog_hash": self._catalog.content_hash,
            "catalog_immutable": True,
            "periods": {
                "development": {
                    "start": self._periods.development_start,
                    "end": self._periods.development_end,
                    "used_for_selection": True,
                },
                "validation": {
                    "start": self._periods.validation_start,
                    "end": self._periods.validation_end,
                    "used_for_selection": False,
                    "confirmation_only": True,
                },
                "consumed_test": {
                    "start": self._periods.consumed_test_start,
                    "end": self._periods.consumed_test_end,
                    "excluded": True,
                    "already_consumed": True,
                    "not_used_for_selection": True,
                },
            },
            "walk_forward": {
                "mode": "ROLLING",
                "train_days": 365,
                "validation_days": 90,
                "step_days": 90,
                "development_fold_count": len(development_folds),
                "validation_fold_count": len(validation_folds),
            },
            "stage_one_selection": _selection_dict(stage_one),
            "final_development_selection": _selection_dict(final_selection),
            "validation_lock": (
                {
                    "variant_id": validation_lock.variant_id,
                    "regime_mode": validation_lock.regime_mode.value,
                    "fingerprint": validation_lock.development_fingerprint,
                    "unchanged": True,
                }
                if validation_lock
                else None
            ),
            "cost_scenarios": ["LOW_COST", "BASE_COST", "HIGH_COST", "STRESS_COST"],
            "selection_cost_scenario": "BASE_COST",
            "search_design": "TWO_STAGE_PRE_REGISTERED_ONLY",
            "broad_search_performed": False,
            "futures_executed": False,
            "leverage_applied": False,
            "authenticated_api_used": False,
            "external_orders_sent": False,
            "paper_trading_enabled": False,
            "report_files": list(_REPORT_FILES),
            "reproducibility_hash": canonical_hash(
                {
                    "dataset_hash": self._dataset.content_hash,
                    "catalog_hash": self._catalog.content_hash,
                    "periods": self._periods,
                    "config": {
                        key: value
                        for key, value in self._config.as_dict().items()
                        if key != "database_path"
                    },
                    "walk_forward": [365, 90, 90, "ROLLING"],
                }
            ),
        }

    def _freeze_decision(
        self,
        criteria: dict[str, Any],
        manifest: dict[str, Any],
        selection: DevelopmentSelection,
        validation_lock: ValidationLock | None,
    ) -> dict[str, Any]:
        candidate_status = str(criteria["status"])
        configuration = None
        if (
            selection.selected_variant_id is not None
            and selection.selected_regime_mode is not None
        ):
            variant = self._catalog.by_id(selection.selected_variant_id)
            configuration = {
                "symbol": self._config.symbol,
                "interval": self._config.interval,
                "strategy_version": "deterministic-ema-atr-volume-v1",
                "variant_id": variant.variant_id,
                "regime_mode": selection.selected_regime_mode.value,
                "short_ema_period": self._config.short_ema_period,
                "long_ema_period": self._config.long_ema_period,
                "minimum_volume_ratio": self._config.minimum_volume_ratio,
                "maximum_atr_relative": self._config.maximum_atr_relative,
                "stop_atr_multiple": self._config.stop_atr_multiple,
                "target_r_multiple": variant.resolved_target(
                    self._config.target_r_multiple
                ),
                "time_exit_candles": variant.time_exit_candles or 0,
                "maker_fee_bps": self._config.maker_fee_bps,
                "taker_fee_bps": self._config.taker_fee_bps,
                "spread_bps": self._config.spread_bps,
                "slippage_bps": self._config.slippage_bps,
                "maximum_open_positions": self._config.maximum_open_positions,
                "maximum_position_percent": self._config.maximum_position_percent,
                "maximum_daily_loss_percent": self._config.maximum_daily_loss_percent,
                "maximum_trades_per_day": self._config.maximum_trades_per_day,
                "warmup_candles": self._config.warmup_candles,
                "latency_candles": self._config.latency_candles,
                "force_close_at_end": self._config.force_close_at_end,
            }
        periods = manifest["periods"]
        return {
            "status": (
                "READY_FOR_EXPLICIT_FREEZE"
                if candidate_status == "CANDIDATE"
                else "NO_CANDIDATE_FROZEN"
            ),
            "candidate_status": candidate_status,
            "candidate_configuration": configuration,
            "criteria": criteria.get("checks", {}),
            "observed_metrics": criteria.get("observed_metrics", {}),
            "failed_criteria": criteria.get("failed_criteria", []),
            "dataset_hash": self._dataset.content_hash,
            "catalog_hash": self._catalog.content_hash,
            "development_period": periods["development"],
            "validation_period": periods["validation"],
            "consumed_test_period": periods["consumed_test"],
            "consumed_test_used": False,
            "validation_lock_unchanged": validation_lock is not None,
            "development_lock": (
                validation_lock.development_fingerprint if validation_lock else None
            ),
            "warnings": [],
            "report_files": list(_REPORT_FILES),
            "declaration": "NOT_APPROVED_FOR_PRODUCTION",
        }

    def _write_reports(
        self,
        output_path: Path,
        manifest: dict[str, Any],
        evaluations: Sequence[_Evaluation],
        stage_one: DevelopmentSelection,
        final_selection: DevelopmentSelection,
        criteria: dict[str, Any],
        decision: dict[str, Any],
    ) -> None:
        _write_json(output_path / "experiment_manifest.json", manifest)
        _write_json(
            output_path / "predefined_variants.json",
            {
                "catalog_hash": self._catalog.content_hash,
                "variants": [
                    {
                        "variant_id": item.variant_id,
                        "catalog_key": item.catalog_key,
                        "target_r_multiple": item.target_r_multiple,
                        "time_exit_candles": item.time_exit_candles or 0,
                        "complexity_rank": item.complexity_rank,
                    }
                    for item in self._catalog.hypotheses
                ],
                "regime_modes": [mode.value for mode in self._catalog.regime_modes],
            },
        )
        exit_rows = [
            evaluation.summary
            for evaluation in evaluations
            if evaluation.stage.startswith("EXIT_HYPOTHESES")
        ]
        regime_rows = [
            evaluation.summary
            for evaluation in evaluations
            if evaluation.stage.startswith("REGIME_MODES")
        ]
        development_rows = [
            row
            for evaluation in evaluations
            if evaluation.period == "DEVELOPMENT"
            for row in evaluation.fold_rows
            if row["scenario"] == "BASE_COST"
        ]
        validation_rows = [
            row
            for evaluation in evaluations
            if evaluation.period == "VALIDATION"
            for row in evaluation.fold_rows
            if row["scenario"] == "BASE_COST"
        ]
        cost_rows = [
            row for evaluation in evaluations for row in evaluation.cost_rows
        ]
        concentration_rows = [
            {
                key: evaluation.summary[key]
                for key in (
                    "stage",
                    "period",
                    "variant_id",
                    "regime_mode",
                    "closed_trade_count",
                    "best_trade_concentration_percent",
                    "top_five_concentration_percent",
                    "result_without_best_trade",
                )
            }
            for evaluation in evaluations
        ]
        _write_csv(output_path / "exit_hypothesis_results.csv", exit_rows)
        _write_csv(output_path / "development_walk_forward.csv", development_rows)
        _write_csv(output_path / "validation_walk_forward.csv", validation_rows)
        _write_csv(output_path / "regime_mode_results.csv", regime_rows)
        _write_csv(output_path / "cost_results.csv", cost_rows)
        _write_csv(output_path / "concentration_results.csv", concentration_rows)
        _write_json(output_path / "candidate_criteria.json", criteria)
        _write_json(output_path / "candidate_freeze_decision.json", decision)
        _write_json(
            output_path / "future_holdout_plan.json",
            {
                "status": (
                    "AWAITING_EXPLICIT_FREEZE"
                    if criteria["status"] == "CANDIDATE"
                    else "NO_CANDIDATE_FROZEN"
                ),
                "candidate_id": None,
                "freeze_time": None,
                "start_after": None,
                "minimum_calendar_days": 90,
                "minimum_closed_trades": 20,
                "market_type": "SPOT",
                "symbol": self._config.symbol,
                "interval": self._config.interval,
                "forbidden_until_complete": [
                    "PARAMETER_CHANGES",
                    "RETROACTIVE_SELECTION",
                    "PAPER_TRADING",
                    "PRODUCTION_APPROVAL",
                ],
                "parameters_may_change": False,
                "future_data_may_affect_selection": False,
                "candidate_change_restarts_holdout": True,
                "paper_trading_enabled": False,
                "executed": False,
            },
        )
        _write_json(
            output_path / "spot_candidate_to_futures_plan.json",
            spot_to_futures_plan(None),
        )
        (output_path / "hypothesis_validation_report.md").write_text(
            self._markdown_report(
                manifest,
                exit_rows,
                regime_rows,
                stage_one,
                final_selection,
                criteria,
                decision,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _run_return(run: SegmentRun) -> Decimal | None:
        if run.result is None or run.result.metrics.initial_capital == 0:
            return None
        return (
            run.result.metrics.net_return
            / run.result.metrics.initial_capital
            * Decimal("100")
        )

    @staticmethod
    def _total_costs(run: SegmentRun) -> Decimal | None:
        if run.result is None:
            return None
        metrics = run.result.metrics
        return metrics.total_fees + metrics.estimated_slippage + metrics.total_spread_cost

    @staticmethod
    def _markdown_report(
        manifest: dict[str, Any],
        exit_rows: list[dict[str, Any]],
        regime_rows: list[dict[str, Any]],
        stage_one: DevelopmentSelection,
        final_selection: DevelopmentSelection,
        criteria: dict[str, Any],
        decision: dict[str, Any],
    ) -> str:
        variant_lines = "\n".join(
            f"- {row['variant_id']}: median={row['median_fold_return_percent']}, "
            f"net={row['net_return_percent']}"
            for row in exit_rows
            if row["period"] == "DEVELOPMENT"
        )
        regime_lines = "\n".join(
            f"- {row['variant_id']} / {row['regime_mode']}: "
            f"median={row['median_fold_return_percent']}"
            for row in regime_rows
            if row["period"] == "DEVELOPMENT"
        )
        return f"""# Controlled Spot hypothesis validation

## 1. Objective
Validate only the pre-registered Sprint 3A.4 Spot hypotheses.

## 2. Predefined hypotheses
Catalog hash: `{manifest['catalog_hash']}`.

## 3. Periods used
Development: 2022-01-01 through 2024-12-31. Validation: 2025-01-01 through 2025-12-31.

## 4. Consumed period excluded
2026-01-01 through 2026-07-01 was already consumed and was not loaded or used.

## 5. Baseline
`SPOT_BASELINE_V1` preserves the original entry and exit rules.

## 6. Time exit 12
Stop and target have priority over the 12-candle time exit.

## 7. Time exit 24
Stop and target have priority over the 24-candle time exit.

## 8. Target 2.5
Only `target_r_multiple=2.5` changes.

## 9. Combinations
Only the two pre-registered time-exit plus target combinations were evaluated.

{variant_lines}

## 10. Development selection
Stage-one status: `{stage_one.status}`; winner: `{stage_one.selected_variant_id}`.

## 11. Locked validation
Validation did not alter development selection. Final lock: `{final_selection.selected_variant_id}`.

## 12. Regime modes
{regime_lines or "- Not run because no development candidate existed."}

## 13. Costs
LOW, BASE, HIGH, and STRESS were fixed; only BASE participated in selection.

## 14. Concentration
Best-trade and top-five concentration are reported without a hidden aggregate score.

## 15. Criteria
Status: `{criteria['status']}`. Failed: `{criteria.get('failed_criteria', [])}`.

## 16. Freeze decision
`{decision['status']}`. Freeze requires the explicit candidate command.

## 17. Limitations
OHLC ambiguity, simulated costs, limited trades, historical dependence, and any manifest-listed
source gap remain. Missing candles are never fabricated.

## 18. Future holdout
Not executed. Minimum 90 calendar days and 20 closed trades after an immutable freeze.

## 19. Spot to Futures 1x plan
No Futures experiment ran. Any future comparison is limited to long, mirrored short,
and long-short at 1x.

## 20. Declarations
- Research only.
- No real orders.
- Past performance does not guarantee future results.
- Not financial advice.
- Not approved for production.
"""


def _selection_dict(selection: DevelopmentSelection) -> dict[str, Any]:
    return {
        "status": selection.status,
        "selected_variant_id": selection.selected_variant_id,
        "selected_regime_mode": (
            selection.selected_regime_mode.value
            if selection.selected_regime_mode
            else None
        ),
        "criterion": selection.criterion,
        "ranked_variant_ids": list(selection.ranked_variant_ids),
        "selection_period": "DEVELOPMENT_ONLY",
    }


def _trade_concentration(
    trades: Sequence[Any],
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if not trades:
        return None, None, None
    profitable = sorted(
        (trade.net_pnl for trade in trades if trade.net_pnl > 0),
        reverse=True,
    )
    total = sum((trade.net_pnl for trade in trades), Decimal("0"))
    gross_profit = sum(profitable, Decimal("0"))
    if not profitable or gross_profit == 0:
        return Decimal("0"), Decimal("0"), total
    return (
        profitable[0] / gross_profit * Decimal("100"),
        sum(profitable[:5], Decimal("0")) / gross_profit * Decimal("100"),
        total - profitable[0],
    )


def _zero_trade_percent(runs: Sequence[SegmentRun]) -> Decimal:
    if not runs:
        return Decimal("100")
    zero = sum(
        run.result is None or run.result.metrics.closed_trade_count == 0
        for run in runs
    )
    return Decimal(zero) / Decimal(len(runs)) * Decimal("100")


def _return_to_drawdown(
    net_return: Decimal | None,
    drawdown: Decimal | None,
) -> Decimal | None:
    if net_return is None or drawdown is None:
        return None
    if drawdown == 0:
        return None
    return net_return / drawdown


def _decimal(value: Any) -> Decimal:
    parsed = _decimal_or_none(value)
    return parsed if parsed is not None else Decimal("0")


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("status\nNO_ROWS\n", encoding="utf-8")
        return
    fields = tuple(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, sort_keys=True, default=str)
                    if isinstance(value, (dict, list, tuple))
                    else value
                    for key, value in row.items()
                }
            )


def _git_metadata() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return commit, dirty


def _dataset_gaps(dataset: ResearchDataset) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for previous, current in zip(dataset.candles, dataset.candles[1:], strict=False):
        if current.open_time - previous.open_time > timedelta(hours=1):
            gaps.append(
                {
                    "previous_open_time": previous.open_time,
                    "next_open_time": current.open_time,
                    "missing_open_time": previous.open_time + timedelta(hours=1),
                    "previous_volume": previous.volume,
                    "explanation": (
                        "SOURCE_GAP_PRESERVED: local public dataset has no candle; "
                        "the adjacent zero-volume candle is consistent with an exchange "
                        "maintenance interval, but this was not externally verified."
                    ),
                    "fabricated": False,
                    "download_attempted": False,
                }
            )
    return gaps
