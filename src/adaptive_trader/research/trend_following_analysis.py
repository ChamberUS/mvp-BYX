"""Pure analysis and locking rules for Sprint 3C.1 trend following."""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from adaptive_trader.research.datasets import canonical_hash
from adaptive_trader.research.trend_following_catalog import (
    TrendFollowingCatalog,
    TrendFollowingHypothesis,
    TrendFollowingMarketGroup,
    TrendFollowingPeriods,
    TrendFollowingRiskModel,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")

MINIMUM_DEVELOPMENT_TRADES = 8
MINIMUM_FOLDS_WITH_TRADES_PERCENT = Decimal("50")
MAXIMUM_ZERO_TRADE_FOLD_PERCENT = Decimal("50")
MAXIMUM_EXPOSURE_PERCENT = Decimal("90")

SELECTION_CRITERIA = (
    "median_walk_forward_net_return",
    "positive_fold_percent",
    "worst_drawdown_percent",
    "top_three_concentration_percent",
    "development_trade_count",
    "exposure_percent",
    "complexity_rank",
    "catalog_order",
)


class CostScenario(StrEnum):
    LOW = "LOW"
    BASE = "BASE"
    HIGH = "HIGH"
    STRESS = "STRESS"


COST_SCENARIOS = (
    CostScenario.LOW,
    CostScenario.BASE,
    CostScenario.HIGH,
    CostScenario.STRESS,
)


class TrendFollowingOperationalStatus(StrEnum):
    OPERATIONALLY_VIABLE = "OPERATIONALLY_VIABLE"
    TOO_RESTRICTIVE = "TOO_RESTRICTIVE"
    TOO_PERMISSIVE = "TOO_PERMISSIVE"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


@dataclass(frozen=True, slots=True)
class TrendFollowingOperationalMetrics:
    market: str
    mode: str
    variant_id: str
    development_trade_count: int
    fold_count: int
    folds_with_trades: int
    exposure_percent: Decimal
    source_period: str = "DEVELOPMENT"

    def __post_init__(self) -> None:
        if self.source_period != "DEVELOPMENT":
            raise ValueError("operational viability accepts development metrics only")
        if self.development_trade_count < 0:
            raise ValueError("development_trade_count must not be negative")
        if self.fold_count < 0:
            raise ValueError("fold_count must not be negative")
        if not 0 <= self.folds_with_trades <= self.fold_count:
            raise ValueError("folds_with_trades must remain inside fold_count")
        _validate_percent(self.exposure_percent, "exposure_percent")

    @property
    def folds_with_trades_percent(self) -> Decimal:
        if self.fold_count == 0:
            return ZERO
        return Decimal(self.folds_with_trades) / Decimal(self.fold_count) * HUNDRED

    @property
    def zero_trade_fold_percent(self) -> Decimal:
        if self.fold_count == 0:
            return HUNDRED
        return HUNDRED - self.folds_with_trades_percent


@dataclass(frozen=True, slots=True)
class TrendFollowingOperationalAssessment:
    market: str
    mode: str
    variant_id: str
    status: TrendFollowingOperationalStatus
    development_trade_count: int
    fold_count: int
    folds_with_trades: int
    folds_with_trades_percent: Decimal
    zero_trade_fold_percent: Decimal
    exposure_percent: Decimal
    criteria: tuple[tuple[str, bool], ...]

    @property
    def viable(self) -> bool:
        return self.status is TrendFollowingOperationalStatus.OPERATIONALLY_VIABLE


def assess_operational_viability(
    metrics: TrendFollowingOperationalMetrics,
) -> TrendFollowingOperationalAssessment:
    criteria = (
        (
            "minimum_development_trades",
            metrics.development_trade_count >= MINIMUM_DEVELOPMENT_TRADES,
        ),
        (
            "minimum_folds_with_trades_percent",
            metrics.folds_with_trades_percent >= MINIMUM_FOLDS_WITH_TRADES_PERCENT,
        ),
        (
            "maximum_zero_trade_fold_percent",
            metrics.zero_trade_fold_percent <= MAXIMUM_ZERO_TRADE_FOLD_PERCENT,
        ),
        (
            "maximum_exposure_percent",
            metrics.exposure_percent <= MAXIMUM_EXPOSURE_PERCENT,
        ),
    )
    if metrics.exposure_percent > MAXIMUM_EXPOSURE_PERCENT:
        status = TrendFollowingOperationalStatus.TOO_PERMISSIVE
    elif metrics.fold_count == 0 or metrics.development_trade_count < MINIMUM_DEVELOPMENT_TRADES:
        status = TrendFollowingOperationalStatus.INSUFFICIENT_SAMPLE
    elif (
        metrics.folds_with_trades_percent < MINIMUM_FOLDS_WITH_TRADES_PERCENT
        or metrics.zero_trade_fold_percent > MAXIMUM_ZERO_TRADE_FOLD_PERCENT
    ):
        status = TrendFollowingOperationalStatus.TOO_RESTRICTIVE
    else:
        status = TrendFollowingOperationalStatus.OPERATIONALLY_VIABLE
    return TrendFollowingOperationalAssessment(
        market=metrics.market,
        mode=metrics.mode,
        variant_id=metrics.variant_id,
        status=status,
        development_trade_count=metrics.development_trade_count,
        fold_count=metrics.fold_count,
        folds_with_trades=metrics.folds_with_trades,
        folds_with_trades_percent=metrics.folds_with_trades_percent,
        zero_trade_fold_percent=metrics.zero_trade_fold_percent,
        exposure_percent=metrics.exposure_percent,
        criteria=criteria,
    )


class TrendFollowingSelectionStatus(StrEnum):
    SELECTED_FOR_VALIDATION = "SELECTED_FOR_VALIDATION"
    NO_DEVELOPMENT_HYPOTHESIS = "NO_DEVELOPMENT_HYPOTHESIS"


@dataclass(frozen=True, slots=True)
class TrendFollowingSelectionMetric:
    market: str
    mode: str
    variant_id: str
    operational_status: TrendFollowingOperationalStatus
    median_walk_forward_net_return: Decimal
    positive_fold_percent: Decimal
    worst_drawdown_percent: Decimal
    top_three_concentration_percent: Decimal
    development_trade_count: int
    exposure_percent: Decimal
    complexity_rank: int
    catalog_order: int
    source_period: str = "DEVELOPMENT"
    cost_scenario: CostScenario = CostScenario.BASE

    def __post_init__(self) -> None:
        if self.development_trade_count < 0:
            raise ValueError("development_trade_count must not be negative")
        if self.complexity_rank < 0 or self.catalog_order < 0:
            raise ValueError("complexity and catalog order must not be negative")
        for name in (
            "positive_fold_percent",
            "worst_drawdown_percent",
            "top_three_concentration_percent",
            "exposure_percent",
        ):
            _validate_percent(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class TrendFollowingDevelopmentSelection:
    market: str
    mode: str
    status: TrendFollowingSelectionStatus
    selected_variant_id: str | None
    ranked_variant_ids: tuple[str, ...]
    criterion: str
    rationale: str


def select_development_hypothesis(
    metrics: tuple[TrendFollowingSelectionMetric, ...],
) -> TrendFollowingDevelopmentSelection:
    if not metrics:
        raise ValueError("development selection requires metrics for one market group")
    market = metrics[0].market
    mode = metrics[0].mode
    if any(metric.market != market or metric.mode != mode for metric in metrics):
        raise ValueError("development selection accepts one market group at a time")
    if len({metric.variant_id for metric in metrics}) != len(metrics):
        raise ValueError("development selection rejects duplicate variants")
    if any(
        metric.source_period != "DEVELOPMENT" or metric.cost_scenario is not CostScenario.BASE
        for metric in metrics
    ):
        raise ValueError("selection accepts development BASE metrics only")
    eligible = tuple(
        metric
        for metric in metrics
        if metric.operational_status is TrendFollowingOperationalStatus.OPERATIONALLY_VIABLE
        and metric.median_walk_forward_net_return >= ZERO
        and metric.positive_fold_percent >= Decimal("50")
    )
    ranked = tuple(
        sorted(
            eligible,
            key=lambda metric: (
                -metric.median_walk_forward_net_return,
                -metric.positive_fold_percent,
                metric.worst_drawdown_percent,
                metric.top_three_concentration_percent,
                -metric.development_trade_count,
                metric.exposure_percent,
                metric.complexity_rank,
                metric.catalog_order,
            ),
        )
    )
    if not ranked:
        return TrendFollowingDevelopmentSelection(
            market=market,
            mode=mode,
            status=TrendFollowingSelectionStatus.NO_DEVELOPMENT_HYPOTHESIS,
            selected_variant_id=None,
            ranked_variant_ids=(),
            criterion=SELECTION_CRITERIA[0],
            rationale=(
                "No operationally viable development configuration had a non-negative "
                "median walk-forward return and at least 50% positive folds."
            ),
        )
    return TrendFollowingDevelopmentSelection(
        market=market,
        mode=mode,
        status=TrendFollowingSelectionStatus.SELECTED_FOR_VALIDATION,
        selected_variant_id=ranked[0].variant_id,
        ranked_variant_ids=tuple(metric.variant_id for metric in ranked),
        criterion=SELECTION_CRITERIA[0],
        rationale=(
            "Selected at most one configuration from 2022-2023 BASE results using "
            "the pre-registered metric and tie-break order."
        ),
    )


@dataclass(frozen=True, slots=True)
class TrendFollowingLockedSelection:
    group: TrendFollowingMarketGroup
    hypothesis: TrendFollowingHypothesis
    development_metric: TrendFollowingSelectionMetric

    def __post_init__(self) -> None:
        metric = self.development_metric
        if (metric.market, metric.mode) != (self.group.market, self.group.mode):
            raise ValueError("locked metric market group differs from selection")
        if metric.variant_id != self.hypothesis.variant_id:
            raise ValueError("locked metric variant differs from hypothesis")
        if metric.source_period != "DEVELOPMENT" or metric.cost_scenario is not CostScenario.BASE:
            raise ValueError("validation lock accepts development BASE metrics only")
        if metric.operational_status is not TrendFollowingOperationalStatus.OPERATIONALLY_VIABLE:
            raise ValueError("validation lock requires an operationally viable configuration")
        if metric.median_walk_forward_net_return < ZERO or metric.positive_fold_percent < Decimal(
            "50"
        ):
            raise ValueError("validation lock requires a development-selected configuration")
        if (
            metric.complexity_rank != self.hypothesis.complexity_rank
            or metric.catalog_order != self.hypothesis.catalog_order
        ):
            raise ValueError("locked selection metadata differs from the catalog")
        if not self.hypothesis.is_applicable_to(self.group):
            raise ValueError("hypothesis is not applicable to the locked market group")


type LockedScalar = str | int | bool | None


@dataclass(frozen=True, slots=True)
class TrendFollowingValidationLock:
    lock_version: int
    selections: tuple[TrendFollowingLockedSelection, ...]
    catalog_hash: str
    catalog_file_sha256: str
    dataset_hashes: tuple[tuple[str, str], ...]
    periods: TrendFollowingPeriods
    selection_criteria: tuple[str, ...]
    git_commit: str
    git_dirty: bool
    leverage: Decimal
    cost_scenarios: tuple[CostScenario, ...]
    cost_parameters: tuple[tuple[str, LockedScalar], ...]
    risk_model: str
    selection_timestamp: datetime
    development_fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        selections: tuple[TrendFollowingLockedSelection, ...],
        catalog: TrendFollowingCatalog,
        dataset_hashes: Mapping[str, str],
        periods: TrendFollowingPeriods,
        git_commit: str,
        git_dirty: bool,
        leverage: Decimal,
        cost_parameters: Mapping[str, object],
        risk_model: str,
        selection_timestamp: datetime,
    ) -> TrendFollowingValidationLock:
        periods.assert_pre_registered()
        _assert_utc(selection_timestamp, "selection_timestamp")
        if leverage != Decimal("1"):
            raise ValueError("trend-following validation lock permits leverage 1 only")
        if not git_commit:
            raise ValueError("validation lock requires a git commit")
        if not risk_model:
            raise ValueError("validation lock requires a risk model")
        if not dataset_hashes or any(not key or not value for key, value in dataset_hashes.items()):
            raise ValueError("validation lock requires non-empty dataset hashes")
        groups = tuple(selection.group for selection in selections)
        if len(groups) != len(set(groups)):
            raise ValueError("validation lock permits at most one selection per market group")
        if any(
            catalog.by_id(selection.hypothesis.variant_id) != selection.hypothesis
            for selection in selections
        ):
            raise ValueError("validation lock selection differs from the catalog")
        normalized_datasets = tuple(sorted(dataset_hashes.items()))
        normalized_costs = _lock_mapping(cost_parameters)
        payload = _lock_payload(
            lock_version=1,
            selections=selections,
            catalog_hash=catalog.canonical_hash,
            catalog_file_sha256=catalog.file_sha256,
            dataset_hashes=normalized_datasets,
            periods=periods,
            selection_criteria=SELECTION_CRITERIA,
            git_commit=git_commit,
            git_dirty=git_dirty,
            leverage=leverage,
            cost_scenarios=COST_SCENARIOS,
            cost_parameters=normalized_costs,
            risk_model=risk_model,
            selection_timestamp=selection_timestamp,
        )
        return cls(
            lock_version=1,
            selections=selections,
            catalog_hash=catalog.canonical_hash,
            catalog_file_sha256=catalog.file_sha256,
            dataset_hashes=normalized_datasets,
            periods=periods,
            selection_criteria=SELECTION_CRITERIA,
            git_commit=git_commit,
            git_dirty=git_dirty,
            leverage=leverage,
            cost_scenarios=COST_SCENARIOS,
            cost_parameters=normalized_costs,
            risk_model=risk_model,
            selection_timestamp=selection_timestamp,
            development_fingerprint=canonical_hash(payload),
        )

    def assert_valid(self) -> None:
        payload = _lock_payload(
            lock_version=self.lock_version,
            selections=self.selections,
            catalog_hash=self.catalog_hash,
            catalog_file_sha256=self.catalog_file_sha256,
            dataset_hashes=self.dataset_hashes,
            periods=self.periods,
            selection_criteria=self.selection_criteria,
            git_commit=self.git_commit,
            git_dirty=self.git_dirty,
            leverage=self.leverage,
            cost_scenarios=self.cost_scenarios,
            cost_parameters=self.cost_parameters,
            risk_model=self.risk_model,
            selection_timestamp=self.selection_timestamp,
        )
        if self.development_fingerprint != canonical_hash(payload):
            raise ValueError("validation lock differs from its development fingerprint")

    def assert_unchanged(
        self,
        *,
        selections: tuple[TrendFollowingLockedSelection, ...],
        catalog: TrendFollowingCatalog,
        dataset_hashes: Mapping[str, str],
        periods: TrendFollowingPeriods,
        git_commit: str,
        git_dirty: bool,
        leverage: Decimal,
        cost_parameters: Mapping[str, object],
        risk_model: str,
        selection_timestamp: datetime,
    ) -> None:
        self.assert_valid()
        candidate = self.create(
            selections=selections,
            catalog=catalog,
            dataset_hashes=dataset_hashes,
            periods=periods,
            git_commit=git_commit,
            git_dirty=git_dirty,
            leverage=leverage,
            cost_parameters=cost_parameters,
            risk_model=risk_model,
            selection_timestamp=selection_timestamp,
        )
        if candidate.development_fingerprint != self.development_fingerprint:
            raise ValueError("validation configuration differs from the development lock")


class BootstrapStatus(StrEnum):
    POSITIVE_UNCERTAIN = "POSITIVE_UNCERTAIN"
    NEGATIVE_UNCERTAIN = "NEGATIVE_UNCERTAIN"
    INCLUDES_ZERO = "INCLUDES_ZERO"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    lower: Decimal
    upper: Decimal


@dataclass(frozen=True, slots=True)
class TrendFollowingBootstrapResult:
    seed: int
    iterations: int
    confidence_percent: Decimal
    sample_size: int
    status: BootstrapStatus
    observed_mean_trade_pnl: Decimal | None
    observed_median_trade_pnl: Decimal | None
    observed_total_pnl: Decimal
    observed_expectancy: Decimal | None
    observed_win_rate_percent: Decimal | None
    mean_trade_pnl_interval: BootstrapInterval | None
    median_trade_pnl_interval: BootstrapInterval | None
    total_pnl_interval: BootstrapInterval | None
    expectancy_interval: BootstrapInterval | None
    win_rate_percent_interval: BootstrapInterval | None


def bootstrap_trade_pnls(
    pnl: tuple[Decimal, ...],
    *,
    seed: int = 42,
    iterations: int = 2000,
    confidence_percent: Decimal = Decimal("95"),
) -> TrendFollowingBootstrapResult:
    if iterations < 1:
        raise ValueError("bootstrap iterations must be positive")
    if not ZERO < confidence_percent < HUNDRED:
        raise ValueError("bootstrap confidence must be between zero and 100")
    observed_total = sum(pnl, ZERO)
    observed_mean = observed_total / Decimal(len(pnl)) if pnl else None
    observed_median = _median(pnl) if pnl else None
    observed_win_rate = (
        Decimal(sum(value > ZERO for value in pnl)) / Decimal(len(pnl)) * HUNDRED if pnl else None
    )
    if len(pnl) < 5:
        return TrendFollowingBootstrapResult(
            seed=seed,
            iterations=iterations,
            confidence_percent=confidence_percent,
            sample_size=len(pnl),
            status=BootstrapStatus.INSUFFICIENT_SAMPLE,
            observed_mean_trade_pnl=observed_mean,
            observed_median_trade_pnl=observed_median,
            observed_total_pnl=observed_total,
            observed_expectancy=observed_mean,
            observed_win_rate_percent=observed_win_rate,
            mean_trade_pnl_interval=None,
            median_trade_pnl_interval=None,
            total_pnl_interval=None,
            expectancy_interval=None,
            win_rate_percent_interval=None,
        )
    generator = random.Random(seed)
    sample_size = len(pnl)
    means: list[Decimal] = []
    medians: list[Decimal] = []
    totals: list[Decimal] = []
    win_rates: list[Decimal] = []
    for _ in range(iterations):
        sample = tuple(pnl[generator.randrange(sample_size)] for _ in range(sample_size))
        total = sum(sample, ZERO)
        mean = total / Decimal(sample_size)
        totals.append(total)
        means.append(mean)
        medians.append(_median(sample))
        win_rates.append(
            Decimal(sum(value > ZERO for value in sample)) / Decimal(sample_size) * HUNDRED
        )
    tail = (HUNDRED - confidence_percent) / Decimal("2")
    total_interval = _interval(tuple(totals), tail, HUNDRED - tail)
    if total_interval.lower > ZERO:
        status = BootstrapStatus.POSITIVE_UNCERTAIN
    elif total_interval.upper < ZERO:
        status = BootstrapStatus.NEGATIVE_UNCERTAIN
    else:
        status = BootstrapStatus.INCLUDES_ZERO
    return TrendFollowingBootstrapResult(
        seed=seed,
        iterations=iterations,
        confidence_percent=confidence_percent,
        sample_size=sample_size,
        status=status,
        observed_mean_trade_pnl=observed_mean,
        observed_median_trade_pnl=observed_median,
        observed_total_pnl=observed_total,
        observed_expectancy=observed_mean,
        observed_win_rate_percent=observed_win_rate,
        mean_trade_pnl_interval=_interval(tuple(means), tail, HUNDRED - tail),
        median_trade_pnl_interval=_interval(tuple(medians), tail, HUNDRED - tail),
        total_pnl_interval=total_interval,
        expectancy_interval=_interval(tuple(means), tail, HUNDRED - tail),
        win_rate_percent_interval=_interval(tuple(win_rates), tail, HUNDRED - tail),
    )


class DefensiveComparisonClassification(StrEnum):
    DRAWDOWN_IMPROVED = "DRAWDOWN_IMPROVED"
    RETURN_REDUCED = "RETURN_REDUCED"
    RECOVERY_DELAYED = "RECOVERY_DELAYED"
    NO_MATERIAL_EFFECT = "NO_MATERIAL_EFFECT"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


@dataclass(frozen=True, slots=True)
class RiskProfileMetrics:
    market: str
    mode: str
    period: str
    variant_id: str
    exit_period_days: int
    risk_model: TrendFollowingRiskModel
    trade_count: int
    net_return_percent: Decimal
    maximum_drawdown_percent: Decimal
    volatility_percent: Decimal
    maximum_loss_percent: Decimal
    recovery_duration_days: Decimal | None
    defensive_activations: int
    defensive_period_percent: Decimal
    half_risk_trade_count: int

    def __post_init__(self) -> None:
        if self.exit_period_days not in {10, 20}:
            raise ValueError("defensive comparison accepts Donchian exits 10 or 20 only")
        if self.trade_count < 0 or self.defensive_activations < 0 or self.half_risk_trade_count < 0:
            raise ValueError("risk comparison counters must not be negative")
        for name in (
            "maximum_drawdown_percent",
            "volatility_percent",
            "maximum_loss_percent",
            "defensive_period_percent",
        ):
            _validate_percent(getattr(self, name), name)
        if self.recovery_duration_days is not None and self.recovery_duration_days < ZERO:
            raise ValueError("recovery_duration_days must not be negative")


@dataclass(frozen=True, slots=True)
class DefensiveRiskComparison:
    market: str
    mode: str
    period: str
    exit_period_days: int
    fixed_variant_id: str
    defensive_variant_id: str
    return_difference_percent: Decimal
    drawdown_difference_percent: Decimal
    volatility_difference_percent: Decimal
    maximum_loss_difference_percent: Decimal
    recovery_duration_difference_days: Decimal | None
    defensive_activations: int
    defensive_period_percent: Decimal
    half_risk_trade_count: int
    upside_sacrificed_percent: Decimal
    downside_avoided_percent: Decimal
    classifications: tuple[DefensiveComparisonClassification, ...]


def compare_defensive_risk(
    fixed: RiskProfileMetrics,
    defensive: RiskProfileMetrics,
) -> DefensiveRiskComparison:
    identity = (fixed.market, fixed.mode, fixed.period, fixed.exit_period_days)
    if (defensive.market, defensive.mode, defensive.period, defensive.exit_period_days) != identity:
        raise ValueError("defensive comparison requires equivalent market, mode, period, and exit")
    if fixed.risk_model is not TrendFollowingRiskModel.FIXED:
        raise ValueError("fixed comparison input must use FIXED risk")
    if defensive.risk_model is not TrendFollowingRiskModel.DEFENSIVE:
        raise ValueError("defensive comparison input must use DEFENSIVE risk")
    recovery_difference = (
        defensive.recovery_duration_days - fixed.recovery_duration_days
        if defensive.recovery_duration_days is not None and fixed.recovery_duration_days is not None
        else None
    )
    classifications: list[DefensiveComparisonClassification] = []
    if min(fixed.trade_count, defensive.trade_count) < MINIMUM_DEVELOPMENT_TRADES:
        classifications.append(DefensiveComparisonClassification.INSUFFICIENT_SAMPLE)
    else:
        if defensive.maximum_drawdown_percent < fixed.maximum_drawdown_percent:
            classifications.append(DefensiveComparisonClassification.DRAWDOWN_IMPROVED)
        if defensive.net_return_percent < fixed.net_return_percent:
            classifications.append(DefensiveComparisonClassification.RETURN_REDUCED)
        if recovery_difference is not None and recovery_difference > ZERO:
            classifications.append(DefensiveComparisonClassification.RECOVERY_DELAYED)
        if not classifications:
            classifications.append(DefensiveComparisonClassification.NO_MATERIAL_EFFECT)
    return DefensiveRiskComparison(
        market=fixed.market,
        mode=fixed.mode,
        period=fixed.period,
        exit_period_days=fixed.exit_period_days,
        fixed_variant_id=fixed.variant_id,
        defensive_variant_id=defensive.variant_id,
        return_difference_percent=(defensive.net_return_percent - fixed.net_return_percent),
        drawdown_difference_percent=(
            defensive.maximum_drawdown_percent - fixed.maximum_drawdown_percent
        ),
        volatility_difference_percent=(defensive.volatility_percent - fixed.volatility_percent),
        maximum_loss_difference_percent=(
            defensive.maximum_loss_percent - fixed.maximum_loss_percent
        ),
        recovery_duration_difference_days=recovery_difference,
        defensive_activations=defensive.defensive_activations,
        defensive_period_percent=defensive.defensive_period_percent,
        half_risk_trade_count=defensive.half_risk_trade_count,
        upside_sacrificed_percent=max(
            ZERO,
            fixed.net_return_percent - defensive.net_return_percent,
        ),
        downside_avoided_percent=max(
            ZERO,
            fixed.maximum_loss_percent - defensive.maximum_loss_percent,
        ),
        classifications=tuple(classifications),
    )


def _lock_payload(
    *,
    lock_version: int,
    selections: tuple[TrendFollowingLockedSelection, ...],
    catalog_hash: str,
    catalog_file_sha256: str,
    dataset_hashes: tuple[tuple[str, str], ...],
    periods: TrendFollowingPeriods,
    selection_criteria: tuple[str, ...],
    git_commit: str,
    git_dirty: bool,
    leverage: Decimal,
    cost_scenarios: tuple[CostScenario, ...],
    cost_parameters: tuple[tuple[str, LockedScalar], ...],
    risk_model: str,
    selection_timestamp: datetime,
) -> dict[str, object]:
    return {
        "lock_version": lock_version,
        "selections": [asdict(selection) for selection in selections],
        "catalog_hash": catalog_hash,
        "catalog_file_sha256": catalog_file_sha256,
        "dataset_hashes": dataset_hashes,
        "periods": periods,
        "selection_criteria": selection_criteria,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "leverage": leverage,
        "cost_scenarios": cost_scenarios,
        "cost_parameters": cost_parameters,
        "risk_model": risk_model,
        "selection_timestamp": selection_timestamp,
    }


def _lock_mapping(values: Mapping[str, object]) -> tuple[tuple[str, LockedScalar], ...]:
    normalized: list[tuple[str, LockedScalar]] = []
    for key, value in sorted(values.items()):
        if not key:
            raise ValueError("locked parameter names must not be empty")
        if isinstance(value, Decimal):
            normalized.append((key, str(value)))
        elif value is None or isinstance(value, (str, int, bool)):
            normalized.append((key, value))
        else:
            raise TypeError(f"unsupported locked parameter type: {type(value).__name__}")
    return tuple(normalized)


def _validate_percent(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not ZERO <= value <= HUNDRED:
        raise ValueError(f"{name} must be between zero and 100")


def _assert_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must use UTC")


def _median(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("median requires values")
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _interval(
    values: tuple[Decimal, ...],
    lower_percent: Decimal,
    upper_percent: Decimal,
) -> BootstrapInterval:
    ordered = tuple(sorted(values))
    return BootstrapInterval(
        lower=_percentile(ordered, lower_percent),
        upper=_percentile(ordered, upper_percent),
    )


def _percentile(ordered: tuple[Decimal, ...], percent: Decimal) -> Decimal:
    if not ordered:
        raise ValueError("percentile requires values")
    scaled = Decimal(len(ordered) - 1) * percent / HUNDRED
    return ordered[int(scaled)]
