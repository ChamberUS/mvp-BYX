"""Pre-registered Spot hypotheses and temporal selection invariants."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from adaptive_trader.research.datasets import canonical_hash
from adaptive_trader.research.periods import ConsumedTestError, ResearchPeriods
from adaptive_trader.strategy.regime import SpotRegimeMode

CATALOG_FILE = Path("spot-hypotheses-v1.toml")

DEVELOPMENT_START = datetime(2022, 1, 1, tzinfo=UTC)
DEVELOPMENT_END = datetime(2024, 12, 31, 23, tzinfo=UTC)
VALIDATION_START = datetime(2025, 1, 1, tzinfo=UTC)
VALIDATION_END = datetime(2025, 12, 31, 23, tzinfo=UTC)
CONSUMED_TEST_START = datetime(2026, 1, 1, tzinfo=UTC)
CONSUMED_TEST_END = datetime(2026, 7, 1, tzinfo=UTC)

EXACT_REGIME_MODES = tuple(SpotRegimeMode)
EXACT_VARIANT_KEYS = (
    "baseline",
    "time_exit_12",
    "time_exit_24",
    "target_r_2_5",
    "time_exit_12_target_r_2_5",
    "time_exit_24_target_r_2_5",
)
EXACT_VARIANT_IDS = (
    "SPOT_BASELINE_V1",
    "SPOT_TIME_EXIT_12_V1",
    "SPOT_TIME_EXIT_24_V1",
    "SPOT_TARGET_R_2_5_V1",
    "SPOT_TIME_EXIT_12_TARGET_R_2_5_V1",
    "SPOT_TIME_EXIT_24_TARGET_R_2_5_V1",
)
_EXPECTED_VALUES = (
    ("CURRENT", 0),
    ("CURRENT", 12),
    ("CURRENT", 24),
    ("2.5", 0),
    ("2.5", 12),
    ("2.5", 24),
)
_COMPLEXITY = (1, 3, 3, 2, 4, 4)


@dataclass(frozen=True, slots=True)
class SpotHypothesis:
    variant_id: str
    catalog_key: str
    target_r_multiple: str
    time_exit_candles: int | None
    complexity_rank: int

    def resolved_target(self, current: Decimal) -> Decimal:
        return current if self.target_r_multiple == "CURRENT" else Decimal(self.target_r_multiple)


@dataclass(frozen=True, slots=True)
class SpotHypothesisCatalog:
    version: int
    path: Path
    hypotheses: tuple[SpotHypothesis, ...]
    regime_modes: tuple[SpotRegimeMode, ...]
    content_hash: str

    def by_id(self, variant_id: str) -> SpotHypothesis:
        match = next(
            (hypothesis for hypothesis in self.hypotheses if hypothesis.variant_id == variant_id),
            None,
        )
        if match is None:
            raise ValueError(f"unknown pre-registered Spot variant: {variant_id}")
        return match


@dataclass(frozen=True, slots=True)
class SpotExperimentPeriods:
    development_start: datetime
    development_end: datetime
    validation_start: datetime
    validation_end: datetime
    consumed_test_start: datetime
    consumed_test_end: datetime

    def __post_init__(self) -> None:
        ResearchPeriods(
            development_start=self.development_start,
            development_end=self.development_end,
            validation_start=self.validation_start,
            validation_end=self.validation_end,
            consumed_test_start=self.consumed_test_start,
            consumed_test_end=self.consumed_test_end,
        )

    @classmethod
    def pre_registered(cls) -> SpotExperimentPeriods:
        return cls(
            development_start=DEVELOPMENT_START,
            development_end=DEVELOPMENT_END,
            validation_start=VALIDATION_START,
            validation_end=VALIDATION_END,
            consumed_test_start=CONSUMED_TEST_START,
            consumed_test_end=CONSUMED_TEST_END,
        )

    def assert_pre_registered(self) -> None:
        if self != self.pre_registered():
            raise ValueError("Spot hypothesis periods must match the pre-registered Sprint 3A.4")

    def assert_selection_range(self, start: datetime, end: datetime, operation: str) -> None:
        ResearchPeriods(
            development_start=self.development_start,
            development_end=self.development_end,
            validation_start=self.validation_start,
            validation_end=self.validation_end,
            consumed_test_start=self.consumed_test_start,
            consumed_test_end=self.consumed_test_end,
        ).assert_not_consumed(start, end, operation)
        if start < self.development_start or end > self.validation_end:
            raise ConsumedTestError(f"{operation} must remain inside development and validation")


@dataclass(frozen=True, slots=True)
class DevelopmentSelectionMetric:
    variant_id: str
    regime_mode: SpotRegimeMode
    median_walk_forward_net_return: Decimal | None
    positive_fold_percent: Decimal
    worst_drawdown_percent: Decimal | None
    zero_trade_fold_percent: Decimal
    cost_sensitivity: Decimal | None
    closed_trade_count: int
    complexity_rank: int
    fold_count: int
    source_period: str = "DEVELOPMENT"


@dataclass(frozen=True, slots=True)
class DevelopmentSelection:
    status: str
    selected_variant_id: str | None
    selected_regime_mode: SpotRegimeMode | None
    criterion: str
    ranked_variant_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidationLock:
    variant_id: str
    regime_mode: SpotRegimeMode
    target_r_multiple: Decimal
    time_exit_candles: int | None
    development_fingerprint: str

    @classmethod
    def create(
        cls,
        hypothesis: SpotHypothesis,
        regime_mode: SpotRegimeMode,
        current_target: Decimal,
    ) -> ValidationLock:
        target = hypothesis.resolved_target(current_target)
        fingerprint = canonical_hash(
            {
                "variant_id": hypothesis.variant_id,
                "regime_mode": regime_mode.value,
                "target_r_multiple": target,
                "time_exit_candles": hypothesis.time_exit_candles,
            }
        )
        return cls(
            variant_id=hypothesis.variant_id,
            regime_mode=regime_mode,
            target_r_multiple=target,
            time_exit_candles=hypothesis.time_exit_candles,
            development_fingerprint=fingerprint,
        )

    def assert_unchanged(
        self,
        *,
        variant_id: str,
        regime_mode: SpotRegimeMode,
        target_r_multiple: Decimal,
        time_exit_candles: int | None,
    ) -> None:
        fingerprint = canonical_hash(
            {
                "variant_id": variant_id,
                "regime_mode": regime_mode.value,
                "target_r_multiple": target_r_multiple,
                "time_exit_candles": time_exit_candles,
            }
        )
        if fingerprint != self.development_fingerprint:
            raise ValueError("validation configuration differs from the development lock")


def load_spot_hypothesis_catalog(path: Path = CATALOG_FILE) -> SpotHypothesisCatalog:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    hypotheses_raw = raw.get("hypotheses")
    regimes_raw = raw.get("regime_modes")
    if not isinstance(hypotheses_raw, dict) or not isinstance(regimes_raw, dict):
        raise ValueError("Spot hypothesis catalog has invalid sections")
    if tuple(hypotheses_raw) != EXACT_VARIANT_KEYS:
        raise ValueError("Spot hypothesis catalog must contain the six exact ordered variants")
    hypotheses: list[SpotHypothesis] = []
    normalized: dict[str, Any] = {"hypotheses": {}, "regime_modes": []}
    for index, key in enumerate(EXACT_VARIANT_KEYS):
        values = hypotheses_raw.get(key)
        if not isinstance(values, dict):
            raise ValueError(f"invalid Spot hypothesis section: {key}")
        target = values.get("target_r_multiple")
        time_exit = values.get("time_exit_candles")
        if (target, time_exit) != _EXPECTED_VALUES[index]:
            raise ValueError(f"Spot hypothesis {key} differs from its pre-registration")
        if not isinstance(time_exit, int):
            raise ValueError(f"Spot hypothesis {key} has invalid time_exit_candles")
        hypotheses.append(
            SpotHypothesis(
                variant_id=EXACT_VARIANT_IDS[index],
                catalog_key=key,
                target_r_multiple=str(target),
                time_exit_candles=int(time_exit) or None,
                complexity_rank=_COMPLEXITY[index],
            )
        )
        normalized["hypotheses"][key] = {
            "target_r_multiple": target,
            "time_exit_candles": time_exit,
        }
    mode_values = regimes_raw.get("values")
    expected_modes = [mode.value for mode in EXACT_REGIME_MODES]
    if mode_values != expected_modes:
        raise ValueError("Spot hypothesis catalog must contain the four exact regime modes")
    normalized["regime_modes"] = expected_modes
    return SpotHypothesisCatalog(
        version=1,
        path=path,
        hypotheses=tuple(hypotheses),
        regime_modes=EXACT_REGIME_MODES,
        content_hash=canonical_hash(normalized),
    )


def select_development_candidate(
    metrics: tuple[DevelopmentSelectionMetric, ...],
) -> DevelopmentSelection:
    if any(metric.source_period != "DEVELOPMENT" for metric in metrics):
        raise ValueError("selection accepts development metrics only")
    if any(metric.regime_mode.diagnostic_only for metric in metrics):
        metrics = tuple(metric for metric in metrics if not metric.regime_mode.diagnostic_only)
    eligible = tuple(
        metric
        for metric in metrics
        if metric.fold_count > 0
        and metric.median_walk_forward_net_return is not None
        and metric.median_walk_forward_net_return >= 0
        and metric.worst_drawdown_percent is not None
        and metric.cost_sensitivity is not None
    )
    if not eligible:
        return DevelopmentSelection(
            status="NO_DEVELOPMENT_CANDIDATE",
            selected_variant_id=None,
            selected_regime_mode=None,
            criterion="median_walk_forward_net_return",
            ranked_variant_ids=(),
        )

    def rank(metric: DevelopmentSelectionMetric) -> tuple[Decimal | int, ...]:
        assert metric.median_walk_forward_net_return is not None
        assert metric.worst_drawdown_percent is not None
        assert metric.cost_sensitivity is not None
        return (
            metric.median_walk_forward_net_return,
            metric.positive_fold_percent,
            -metric.worst_drawdown_percent,
            -metric.zero_trade_fold_percent,
            -metric.cost_sensitivity,
            metric.closed_trade_count,
            -metric.complexity_rank,
        )

    ordered = tuple(sorted(eligible, key=rank, reverse=True))
    selected = ordered[0]
    return DevelopmentSelection(
        status="SELECTED_FOR_VALIDATION",
        selected_variant_id=selected.variant_id,
        selected_regime_mode=selected.regime_mode,
        criterion="median_walk_forward_net_return",
        ranked_variant_ids=tuple(metric.variant_id for metric in ordered),
    )
