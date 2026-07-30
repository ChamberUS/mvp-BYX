"""Filesystem reports for reproducible research experiments."""

from __future__ import annotations

import csv
import json
from dataclasses import fields, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

from adaptive_trader.research.datasets import dataset_to_dict
from adaptive_trader.research.manifest import manifest_to_dict
from adaptive_trader.research.models import (
    BenchmarkResult,
    ExperimentManifest,
    ResearchDataset,
    ResearchSummary,
    RobustnessDiagnostics,
    SegmentRun,
)


def _value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_value(item) for item in value]
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(_value(value), indent=2, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]], fieldnames: tuple[str, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _fold_rows(runs: tuple[SegmentRun, ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for run in runs:
        result = run.result
        rows.append(
            {
                "segment": run.segment.name,
                "start_time": run.segment.start_time.isoformat(),
                "end_time": run.segment.end_time.isoformat(),
                "input_start_time": run.segment.candles[0].open_time.isoformat(),
                "requested_evaluation_start_time": (
                    run.segment.requested_evaluation_start_time.isoformat()
                ),
                "effective_evaluation_start_time": (
                    run.segment.effective_evaluation_start_time.isoformat()
                ),
                "input_candle_count": run.segment.input_candle_count,
                "candle_count": run.segment.candle_count,
                "warmup_candle_count": run.segment.warmup_candle_count,
                "evaluated_candle_count": run.segment.evaluated_candle_count,
                "segment_hash": run.segment.content_hash,
                "warnings": ";".join(run.segment.warnings),
                "failed": run.failed,
                "error": run.error or "",
                "entry_count": result.metrics.entry_count if result else "",
                "closed_trade_count": result.metrics.closed_trade_count if result else "",
                "net_return": (
                    result.metrics.net_return / result.metrics.initial_capital * Decimal("100")
                    if result
                    else ""
                ),
                "maximum_drawdown_percent": (
                    result.metrics.maximum_drawdown_percent if result else ""
                ),
            }
        )
    return rows


class ResearchReportWriter:
    def write(
        self,
        *,
        output_dir: Path,
        manifest: ExperimentManifest,
        dataset: ResearchDataset,
        runs: tuple[SegmentRun, ...],
        summary: ResearchSummary,
        diagnostics: RobustnessDiagnostics,
        benchmarks: tuple[BenchmarkResult, ...] = (),
        regime_metrics: tuple[object, ...] = (),
        sensitivity_rows: tuple[dict[str, object], ...] = (),
        cost_rows: tuple[dict[str, object], ...] = (),
    ) -> tuple[str, ...]:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_json(output_dir / "manifest.json", manifest_to_dict(manifest))
        _write_json(output_dir / "dataset.json", dataset_to_dict(dataset))
        _write_json(output_dir / "summary.json", {"summary": summary, "diagnostics": diagnostics})
        _write_csv(
            output_dir / "summary.csv",
            [cast(dict[str, object], _value(summary))],
            tuple(str(field.name) for field in fields(summary)),
        )
        _write_csv(
            output_dir / "folds.csv",
            _fold_rows(runs),
            (
                "segment",
                "start_time",
                "end_time",
                "input_start_time",
                "requested_evaluation_start_time",
                "effective_evaluation_start_time",
                "input_candle_count",
                "candle_count",
                "warmup_candle_count",
                "evaluated_candle_count",
                "segment_hash",
                "warnings",
                "failed",
                "error",
                "entry_count",
                "closed_trade_count",
                "net_return",
                "maximum_drawdown_percent",
            ),
        )
        _write_csv(output_dir / "parameter_results.csv", [], ("parameters", "net_return", "source"))
        _write_csv(
            output_dir / "sensitivity.csv",
            list(sensitivity_rows),
            ("parameters", "net_return", "warning"),
        )
        _write_csv(
            output_dir / "cost_scenarios.csv",
            list(cost_rows),
            ("scenario", "net_return", "gross_return", "total_costs", "warning"),
        )
        _write_csv(
            output_dir / "regime_metrics.csv",
            [cast(dict[str, object], _value(item)) for item in regime_metrics],
            (
                "regime",
                "candle_count",
                "entry_count",
                "closed_trade_count",
                "net_return",
                "win_rate",
                "profit_factor",
                "expectancy",
                "maximum_drawdown_percent",
                "exposure_percent",
                "total_costs",
            ),
        )
        _write_json(
            output_dir / "warnings.json",
            {"warnings": list(manifest.warnings) + list(diagnostics.warnings)},
        )
        _write_json(output_dir / "benchmarks.json", benchmarks)
        trades = tuple(trade for run in runs if run.result for trade in run.result.trades)
        _write_json(output_dir / "trades.json", trades)
        report = self._markdown(manifest, dataset, summary, diagnostics, benchmarks)
        (output_dir / "report.md").write_text(report, encoding="utf-8")
        (output_dir / "README.txt").write_text(
            "Research-only report. No real orders were sent. "
            "Past results do not guarantee future results.\n",
            encoding="utf-8",
        )
        return tuple(path.name for path in sorted(output_dir.iterdir()) if path.is_file())

    @staticmethod
    def _markdown(
        manifest: ExperimentManifest,
        dataset: ResearchDataset,
        summary: ResearchSummary,
        diagnostics: RobustnessDiagnostics,
        benchmarks: tuple[BenchmarkResult, ...],
    ) -> str:
        benchmark_lines = "\n".join(
            f"- {item.name}: net_return={item.net_return_percent}% costs={item.total_costs}"
            for item in benchmarks
        )
        warning_lines = "\n".join(
            f"- {warning}" for warning in (*manifest.warnings, *diagnostics.warnings)
        )
        return f"""# Research report

## Identification

- Experiment: `{manifest.experiment_name}` (`{manifest.experiment_id}`)
- Dataset: `{dataset.dataset_id}`
- Dataset hash: `{dataset.content_hash}`
- Reproducibility hash: `{manifest.reproducibility_hash}`

## Dataset and methodology

- Candles: {dataset.candle_count}
- Period: {dataset.start_time.isoformat()} -> {dataset.end_time.isoformat()}
- Gap policy: {manifest.gap_policy}
- Each segment reports input, warmup, requested evaluation, and effective evaluation candles.
- Warmup is used only for indicators; it creates no trades, snapshots, or evaluated metrics.
- Equity curves, exposure, and benchmarks start at the effective evaluation start.
- A reduced first segment can shift its effective start when prior history is unavailable.
- The time series is never shuffled; this is a backtest, not a production approval.

## Results

- Completed folds: {summary.completed_fold_count}/{summary.fold_count}
- Entries: {summary.total_entries}; closed trades: {summary.total_closed_trades}
- Mean net return: {summary.mean_net_return}%
- Mean maximum drawdown: {summary.mean_max_drawdown}%
- Positive folds: {summary.positive_fold_percent}%

## Benchmarks

{benchmark_lines}

## Diagnostics

- Train/validation return gap: {diagnostics.train_validation_return_gap}
- Best-trade concentration: {diagnostics.best_trade_profit_percent}%
- Top-five concentration: {diagnostics.top_five_trade_profit_percent}%
- Best-day concentration: {diagnostics.best_day_profit_percent}%
- Positive/negative months: {diagnostics.positive_month_count}/{diagnostics.negative_month_count}
- Longest period without a new top: {diagnostics.longest_period_without_new_top_days} days

## Warnings

{warning_lines or '- None'}

## Limitations

This is research-only backtest output. No real or authenticated orders were sent.
Results are not financial advice and past results do not guarantee future results.
Diagnostics are not proof of profitability, safety, or statistical significance.
"""
