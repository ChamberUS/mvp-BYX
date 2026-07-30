"""Reproducibility manifests without requiring Git or external services."""

from __future__ import annotations

import json
import platform
import subprocess
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import SerializedValue
from adaptive_trader.research.datasets import canonical_hash
from adaptive_trader.research.models import (
    DatasetSegment,
    ExperimentManifest,
    ResearchDataset,
)


def _git_metadata() -> tuple[str | None, bool | None, tuple[str, ...]]:
    warnings: list[str] = []
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
            ).stdout.strip()
        )
        return commit, dirty, tuple(warnings)
    except (OSError, subprocess.CalledProcessError):
        warnings.append("GIT_METADATA_UNAVAILABLE")
        return None, None, tuple(warnings)


def config_hash(config: TradingConfig) -> str:
    values = {key: value for key, value in config.as_dict().items() if key != "database_path"}
    return canonical_hash(values)


def reproducibility_hash(
    *,
    dataset_hash: str,
    configuration: dict[str, SerializedValue],
    segment_hashes: dict[str, str],
    strategy_name: str,
    strategy_version: str,
) -> str:
    stable_configuration = {
        key: value for key, value in configuration.items() if key != "database_path"
    }
    return canonical_hash(
        {
            "dataset_hash": dataset_hash,
            "configuration": stable_configuration,
            "segment_hashes": segment_hashes,
            "strategy_name": strategy_name,
            "strategy_version": strategy_version,
        }
    )


def build_manifest(
    *,
    experiment_id: str,
    experiment_name: str,
    dataset: ResearchDataset,
    segments: tuple[DatasetSegment, ...],
    config: TradingConfig,
    output_files: tuple[str, ...],
    gap_policy: str,
    split: dict[str, SerializedValue],
    warnings: tuple[str, ...] = (),
    executed_at: datetime | None = None,
) -> ExperimentManifest:
    try:
        project_version = version("adaptive-trader")
    except PackageNotFoundError:
        project_version = "unknown"
    git_commit, git_dirty, git_warnings = _git_metadata()
    all_warnings = tuple(dict.fromkeys((*dataset.warnings, *git_warnings, *warnings)))
    configuration = config.as_dict()
    segment_hashes = {segment.name: segment.content_hash for segment in segments}
    stable_hash = reproducibility_hash(
        dataset_hash=dataset.content_hash,
        configuration=configuration,
        segment_hashes=segment_hashes,
        strategy_name="deterministic-ema-atr-volume",
        strategy_version="deterministic-ema-atr-volume-v1",
    )
    return ExperimentManifest(
        experiment_id=experiment_id,
        experiment_name=experiment_name,
        executed_at=executed_at or datetime.now(tz=UTC),
        project_version=project_version,
        git_commit=git_commit,
        git_dirty=git_dirty,
        python_version=platform.python_version(),
        operating_system=platform.platform(),
        dataset_id=dataset.dataset_id,
        dataset_hash=dataset.content_hash,
        strategy_name="deterministic-ema-atr-volume",
        strategy_version="deterministic-ema-atr-volume-v1",
        report_version="2",
        configuration=configuration,
        strategy_parameters={
            key: configuration[key]
            for key in (
                "short_ema_period",
                "long_ema_period",
                "stop_atr_multiple",
                "target_r_multiple",
            )
        },
        risk_parameters={
            key: configuration[key]
            for key in (
                "maximum_open_positions",
                "maximum_position_percent",
                "maximum_daily_loss_percent",
                "maximum_trades_per_day",
            )
        },
        execution_parameters={
            key: configuration[key]
            for key in ("latency_candles", "force_close_at_end", "execute_on_next_candle_open")
        },
        cost_parameters={
            key: configuration[key]
            for key in ("taker_fee_bps", "spread_bps", "slippage_bps")
        },
        intrabar_policy=config.ambiguous_intrabar_policy,
        gap_policy=gap_policy,
        split=split,
        segment_hashes=segment_hashes,
        output_files=output_files,
        warnings=all_warnings,
        config_hash=config_hash(config),
        reproducibility_hash=stable_hash,
    )


def manifest_to_dict(manifest: ExperimentManifest) -> dict[str, Any]:
    return {
        "experiment_id": manifest.experiment_id,
        "experiment_name": manifest.experiment_name,
        "executed_at": manifest.executed_at.isoformat(),
        "project_version": manifest.project_version,
        "git_commit": manifest.git_commit,
        "git_dirty": manifest.git_dirty,
        "python_version": manifest.python_version,
        "operating_system": manifest.operating_system,
        "dataset_id": manifest.dataset_id,
        "dataset_hash": manifest.dataset_hash,
        "strategy_name": manifest.strategy_name,
        "strategy_version": manifest.strategy_version,
        "report_version": manifest.report_version,
        "configuration": manifest.configuration,
        "strategy_parameters": manifest.strategy_parameters,
        "risk_parameters": manifest.risk_parameters,
        "execution_parameters": manifest.execution_parameters,
        "cost_parameters": manifest.cost_parameters,
        "intrabar_policy": manifest.intrabar_policy,
        "gap_policy": manifest.gap_policy,
        "split": manifest.split,
        "segment_hashes": manifest.segment_hashes,
        "output_files": list(manifest.output_files),
        "warnings": list(manifest.warnings),
        "config_hash": manifest.config_hash,
        "reproducibility_hash": manifest.reproducibility_hash,
    }


def write_manifest(path: Path, manifest: ExperimentManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest_to_dict(manifest), indent=2, sort_keys=True), encoding="utf-8"
    )
