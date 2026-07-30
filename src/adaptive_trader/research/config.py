"""Standard-library TOML loader for research experiment definitions."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from adaptive_trader.research.models import (
    GapPolicy,
    SelectionCriterion,
    SelectionMode,
    WalkForwardMode,
)
from adaptive_trader.research.periods import ResearchPeriods


class ResearchConfigError(ValueError):
    """Raised when a research TOML file is invalid or contains secrets."""


@dataclass(frozen=True, slots=True)
class ResearchFileConfig:
    experiment_name: str
    mode: str
    output_dir: Path
    symbol: str
    interval: str
    start: datetime
    end: datetime
    gap_policy: GapPolicy
    train_percent: Decimal
    validation_percent: Decimal
    test_percent: Decimal
    walk_mode: WalkForwardMode
    train_days: int
    validation_days: int
    step_days: int
    warmup_candles: int
    selection_mode: SelectionMode
    criterion: SelectionCriterion
    maximum_parameter_combinations: int
    minimum_closed_trades: int


@dataclass(frozen=True, slots=True)
class DiagnosticsFileConfig:
    periods: ResearchPeriods
    future_return_horizons: tuple[int, ...]
    maximum_parameter_combinations: int
    minimum_closed_trades: int
    candidate: dict[str, Decimal]


def _datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ResearchConfigError(f"{name} must be an ISO datetime string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ResearchConfigError(f"{name} must be a valid ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResearchConfigError(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except ArithmeticError as exc:
        raise ResearchConfigError(f"{name} must be Decimal-compatible") from exc
    if not result.is_finite():
        raise ResearchConfigError(f"{name} must be finite")
    return result


def _find_secret_keys(value: object, path: str = "") -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key)
            if "API_KEY" in name.upper() or "SECRET" in name.upper() or "PASSWORD" in name.upper():
                found.append(f"{path}.{name}" if path else name)
            found.extend(_find_secret_keys(child, f"{path}.{name}" if path else name))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_secret_keys(child, f"{path}[{index}]"))
    return tuple(found)


def load_experiment_toml(path: Path) -> ResearchFileConfig:
    try:
        with path.open("rb") as file:
            raw: dict[str, Any] = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ResearchConfigError(f"could not read research TOML: {path}") from exc
    secret_keys = _find_secret_keys(raw)
    if secret_keys:
        raise ResearchConfigError(f"research TOML contains forbidden secret fields: {secret_keys}")
    experiment = raw.get("experiment", {})
    dataset = raw.get("dataset", {})
    walk = raw.get("walk_forward", {})
    selection = raw.get("selection", {})
    if not all(isinstance(section, dict) for section in (experiment, dataset, walk, selection)):
        raise ResearchConfigError("experiment, dataset, walk_forward and selection must be tables")
    return ResearchFileConfig(
        experiment_name=str(experiment.get("name", "research")),
        mode=str(experiment.get("mode", "holdout")),
        output_dir=Path(str(experiment.get("output_dir", "reports/research"))),
        symbol=str(dataset.get("symbol", "ETHUSDT")),
        interval=str(dataset.get("interval", "1m")),
        start=_datetime(dataset.get("start"), "dataset.start"),
        end=_datetime(dataset.get("end"), "dataset.end"),
        gap_policy=GapPolicy(str(dataset.get("gap_policy", "WARN")).upper()),
        train_percent=_decimal(dataset.get("train_percent", "60"), "train_percent"),
        validation_percent=_decimal(dataset.get("validation_percent", "20"), "validation_percent"),
        test_percent=_decimal(dataset.get("test_percent", "20"), "test_percent"),
        walk_mode=WalkForwardMode(str(walk.get("mode", "ROLLING")).upper()),
        train_days=int(walk.get("train_days", 90)),
        validation_days=int(walk.get("validation_days", 30)),
        step_days=int(walk.get("step_days", 30)),
        warmup_candles=int(walk.get("warmup_candles", 100)),
        selection_mode=SelectionMode(str(selection.get("mode", "FIXED_PARAMETERS")).upper()),
        criterion=SelectionCriterion(str(selection.get("criterion", "return_to_drawdown"))),
        maximum_parameter_combinations=int(selection.get("maximum_parameter_combinations", 100)),
        minimum_closed_trades=int(selection.get("minimum_closed_trades", 10)),
    )


def load_diagnostics_toml(path: Path) -> DiagnosticsFileConfig:
    try:
        with path.open("rb") as file:
            raw: dict[str, Any] = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ResearchConfigError(f"could not read diagnostics TOML: {path}") from exc
    secret_keys = _find_secret_keys(raw)
    if secret_keys:
        raise ResearchConfigError(
            f"diagnostics TOML contains forbidden secret fields: {secret_keys}"
        )
    periods = raw.get("periods", {})
    diagnostics = raw.get("diagnostics", {})
    candidate = raw.get("candidate", {})
    if not all(isinstance(section, dict) for section in (periods, diagnostics, candidate)):
        raise ResearchConfigError("periods, diagnostics and candidate must be tables")
    research_periods = ResearchPeriods(
        development_start=_datetime(periods.get("development_start"), "periods.development_start"),
        development_end=_datetime(periods.get("development_end"), "periods.development_end"),
        validation_start=_datetime(periods.get("validation_start"), "periods.validation_start"),
        validation_end=_datetime(periods.get("validation_end"), "periods.validation_end"),
        consumed_test_start=_datetime(
            periods.get("consumed_test_start"), "periods.consumed_test_start"
        ),
        consumed_test_end=_datetime(periods.get("consumed_test_end"), "periods.consumed_test_end"),
    )
    horizons_raw = diagnostics.get("future_return_horizons", [1, 3, 6, 12, 24])
    if not isinstance(horizons_raw, list) or not all(
        isinstance(item, int) for item in horizons_raw
    ):
        raise ResearchConfigError("diagnostics.future_return_horizons must be integer values")
    horizons = tuple(int(item) for item in horizons_raw)
    if not horizons or any(item < 1 for item in horizons):
        raise ResearchConfigError("future return horizons must be positive")
    return DiagnosticsFileConfig(
        periods=research_periods,
        future_return_horizons=horizons,
        maximum_parameter_combinations=int(diagnostics.get("maximum_parameter_combinations", 60)),
        minimum_closed_trades=int(diagnostics.get("minimum_closed_trades", 30)),
        candidate={key: _decimal(value, f"candidate.{key}") for key, value in candidate.items()},
    )
