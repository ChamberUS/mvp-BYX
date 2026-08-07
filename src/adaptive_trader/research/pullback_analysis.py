"""Pure metrics and decision rules for the pre-registered pullback experiment."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from adaptive_trader.strategy.pullback import PullbackDecisionTrace

ZERO: Final = Decimal("0")
HUNDRED: Final = Decimal("100")


class PullbackClassification(StrEnum):
    PROMISING_FOR_FUTURE_HOLDOUT = "PROMISING_FOR_FUTURE_HOLDOUT"
    NOT_PROMISING = "NOT_PROMISING"
    INCONCLUSIVE = "INCONCLUSIVE"
    NO_DEVELOPMENT_HYPOTHESIS = "NO_DEVELOPMENT_HYPOTHESIS"


class BootstrapStatus(StrEnum):
    POSITIVE_UNCERTAIN = "POSITIVE_UNCERTAIN"
    NEGATIVE_UNCERTAIN = "NEGATIVE_UNCERTAIN"
    INCLUDES_ZERO = "INCLUDES_ZERO"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


@dataclass(frozen=True, slots=True)
class PullbackClosedTrade:
    market: str
    mode: str
    variant_id: str
    period: str
    scenario: str
    side: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    gross_pnl: Decimal
    fees: Decimal
    execution_costs: Decimal
    funding_paid: Decimal
    funding_received: Decimal
    net_funding: Decimal
    liquidation_fee: Decimal
    net_pnl: Decimal
    holding_candles: int
    exit_reason: str
    liquidated: bool


@dataclass(frozen=True, slots=True)
class PullbackRun:
    market: str
    mode: str
    variant_id: str
    period: str
    scenario: str
    evaluation_start: datetime
    evaluation_end: datetime
    initial_capital: Decimal
    final_capital: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    net_return_percent: Decimal
    maximum_drawdown_percent: Decimal
    total_costs: Decimal
    fees: Decimal
    funding_paid: Decimal
    funding_received: Decimal
    net_funding: Decimal
    liquidation_count: int
    evaluated_candles: int
    entry_count: int
    approvals: int
    executions: int
    trend_detected: int
    persistence_accepted: int
    pullbacks_detected: int
    pullbacks_valid: int
    resumptions: int
    long_signals: int
    short_signals: int
    buy_and_hold_return_percent: Decimal | None
    long_pnl: Decimal
    short_pnl: Decimal
    trades: tuple[PullbackClosedTrade, ...]
    pullback_traces: tuple[PullbackDecisionTrace, ...]
    reason_counts: tuple[tuple[str, int], ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PullbackFold:
    fold: int
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    run: PullbackRun


@dataclass(frozen=True, slots=True)
class WalkForwardSummary:
    market: str
    mode: str
    variant_id: str
    period: str
    scenario: str
    fold_count: int
    positive_fold_count: int
    positive_fold_percent: Decimal
    zero_trade_fold_count: int
    zero_trade_fold_percent: Decimal
    median_return_percent: Decimal
    mean_return_percent: Decimal
    worst_fold_return_percent: Decimal
    best_fold_return_percent: Decimal
    trades: int
    maximum_drawdown_percent: Decimal
    total_costs: Decimal
    net_funding: Decimal
    best_trade_concentration_percent: Decimal
    net_pnl_without_top_three: Decimal
    long_pnl: Decimal
    short_pnl: Decimal


@dataclass(frozen=True, slots=True)
class DevelopmentSelection:
    market: str
    mode: str
    status: PullbackClassification
    selected_variant_ids: tuple[str, ...]
    ranked_variant_ids: tuple[str, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    lower: Decimal
    upper: Decimal


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    market: str
    mode: str
    variant_id: str
    period: str
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


@dataclass(frozen=True, slots=True)
class CandidateAssessment:
    market: str
    mode: str
    variant_id: str | None
    classification: PullbackClassification
    criteria: tuple[tuple[str, bool], ...]
    failures: tuple[str, ...]
    rationale: str


def median_decimal(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        return ZERO
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def concentration_metrics(
    trades: tuple[PullbackClosedTrade, ...],
) -> dict[str, Decimal]:
    ranked = tuple(sorted((trade.net_pnl for trade in trades), reverse=True))
    positive_total = sum((value for value in ranked if value > ZERO), ZERO)

    def percentage(count: int) -> Decimal:
        if positive_total <= ZERO:
            return ZERO
        positive_top = sum(
            (max(value, ZERO) for value in ranked[:count]),
            ZERO,
        )
        return positive_top / positive_total * HUNDRED

    return {
        "top_1_percent": percentage(1),
        "top_3_percent": percentage(3),
        "top_5_percent": percentage(5),
        "net_pnl_without_top_1": sum(ranked[1:], ZERO),
        "net_pnl_without_top_3": sum(ranked[3:], ZERO),
        "net_pnl_without_top_5": sum(ranked[5:], ZERO),
    }


def summarize_folds(folds: tuple[PullbackFold, ...]) -> WalkForwardSummary:
    if not folds:
        raise ValueError("walk-forward summary requires folds")
    first = folds[0].run
    if any(
        (
            fold.run.market,
            fold.run.mode,
            fold.run.variant_id,
            fold.run.period,
            fold.run.scenario,
        )
        != (
            first.market,
            first.mode,
            first.variant_id,
            first.period,
            first.scenario,
        )
        for fold in folds
    ):
        raise ValueError("walk-forward folds must share one configuration")
    returns = tuple(fold.run.net_return_percent for fold in folds)
    trades = tuple(
        trade
        for fold in folds
        for trade in fold.run.trades
    )
    fold_count = len(folds)
    positive = sum(value > ZERO for value in returns)
    zero_trade = sum(not fold.run.trades for fold in folds)
    concentration = concentration_metrics(trades)
    return WalkForwardSummary(
        market=first.market,
        mode=first.mode,
        variant_id=first.variant_id,
        period=first.period,
        scenario=first.scenario,
        fold_count=fold_count,
        positive_fold_count=positive,
        positive_fold_percent=Decimal(positive) / Decimal(fold_count) * HUNDRED,
        zero_trade_fold_count=zero_trade,
        zero_trade_fold_percent=Decimal(zero_trade) / Decimal(fold_count) * HUNDRED,
        median_return_percent=median_decimal(returns),
        mean_return_percent=sum(returns, ZERO) / Decimal(fold_count),
        worst_fold_return_percent=min(returns),
        best_fold_return_percent=max(returns),
        trades=len(trades),
        maximum_drawdown_percent=max(
            fold.run.maximum_drawdown_percent for fold in folds
        ),
        total_costs=sum((fold.run.total_costs for fold in folds), ZERO),
        net_funding=sum((fold.run.net_funding for fold in folds), ZERO),
        best_trade_concentration_percent=concentration["top_1_percent"],
        net_pnl_without_top_three=concentration["net_pnl_without_top_3"],
        long_pnl=sum((fold.run.long_pnl for fold in folds), ZERO),
        short_pnl=sum((fold.run.short_pnl for fold in folds), ZERO),
    )


def select_development_hypotheses(
    summaries: tuple[WalkForwardSummary, ...],
    *,
    complexity_by_variant: dict[str, int],
    maximum_selected: int = 2,
) -> DevelopmentSelection:
    if not summaries:
        raise ValueError("development selection requires summaries")
    if maximum_selected < 1:
        raise ValueError("maximum_selected must be positive")
    market = summaries[0].market
    mode = summaries[0].mode
    if any(
        summary.market != market
        or summary.mode != mode
        or summary.period != "DEVELOPMENT"
        or summary.scenario != "BASE"
        for summary in summaries
    ):
        raise ValueError("selection accepts one development BASE market/mode only")
    candidates = tuple(
        summary
        for summary in summaries
        if summary.variant_id != "ORIGINAL_BASELINE"
        and summary.median_return_percent >= ZERO
        and summary.positive_fold_percent >= Decimal("50")
    )
    ranked = tuple(
        sorted(
            candidates,
            key=lambda summary: (
                -summary.median_return_percent,
                -summary.positive_fold_percent,
                summary.maximum_drawdown_percent,
                summary.zero_trade_fold_percent,
                summary.best_trade_concentration_percent,
                -summary.trades,
                complexity_by_variant[summary.variant_id],
                summary.variant_id,
            ),
        )
    )
    if not ranked:
        return DevelopmentSelection(
            market=market,
            mode=mode,
            status=PullbackClassification.NO_DEVELOPMENT_HYPOTHESIS,
            selected_variant_ids=(),
            ranked_variant_ids=(),
            rationale=(
                "No pullback variant had non-negative development median return "
                "and at least 50% positive BASE walk-forward folds."
            ),
        )
    return DevelopmentSelection(
        market=market,
        mode=mode,
        status=PullbackClassification.INCONCLUSIVE,
        selected_variant_ids=tuple(
            summary.variant_id for summary in ranked[:maximum_selected]
        ),
        ranked_variant_ids=tuple(summary.variant_id for summary in ranked),
        rationale=(
            "Selected only from 2022-2023 BASE folds using the pre-registered "
            "primary metric and tie-break order."
        ),
    )


def bootstrap_trades(
    *,
    market: str,
    mode: str,
    variant_id: str,
    period: str,
    trades: tuple[PullbackClosedTrade, ...],
    seed: int = 42,
    iterations: int = 2000,
    confidence_percent: Decimal = Decimal("95"),
) -> BootstrapResult:
    if iterations < 1:
        raise ValueError("bootstrap iterations must be positive")
    if not ZERO < confidence_percent < HUNDRED:
        raise ValueError("bootstrap confidence must be between zero and 100")
    pnl = tuple(trade.net_pnl for trade in trades)
    observed_total = sum(pnl, ZERO)
    observed_mean = (
        observed_total / Decimal(len(pnl))
        if pnl
        else None
    )
    observed_median = median_decimal(pnl) if pnl else None
    observed_win_rate = (
        Decimal(sum(value > ZERO for value in pnl)) / Decimal(len(pnl)) * HUNDRED
        if pnl
        else None
    )
    if len(pnl) < 5:
        return BootstrapResult(
            market=market,
            mode=mode,
            variant_id=variant_id,
            period=period,
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
    means: list[Decimal] = []
    medians: list[Decimal] = []
    totals: list[Decimal] = []
    win_rates: list[Decimal] = []
    sample_size = len(pnl)
    for _ in range(iterations):
        sample = tuple(
            pnl[generator.randrange(sample_size)]
            for _ in range(sample_size)
        )
        total = sum(sample, ZERO)
        mean = total / Decimal(sample_size)
        totals.append(total)
        means.append(mean)
        medians.append(median_decimal(sample))
        win_rates.append(
            Decimal(sum(value > ZERO for value in sample))
            / Decimal(sample_size)
            * HUNDRED
        )
    lower_tail = (HUNDRED - confidence_percent) / Decimal("2")
    upper_tail = HUNDRED - lower_tail
    total_interval = _interval(tuple(totals), lower_tail, upper_tail)
    if total_interval.lower > ZERO:
        status = BootstrapStatus.POSITIVE_UNCERTAIN
    elif total_interval.upper < ZERO:
        status = BootstrapStatus.NEGATIVE_UNCERTAIN
    else:
        status = BootstrapStatus.INCLUDES_ZERO
    return BootstrapResult(
        market=market,
        mode=mode,
        variant_id=variant_id,
        period=period,
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
        mean_trade_pnl_interval=_interval(tuple(means), lower_tail, upper_tail),
        median_trade_pnl_interval=_interval(
            tuple(medians), lower_tail, upper_tail
        ),
        total_pnl_interval=total_interval,
        expectancy_interval=_interval(tuple(means), lower_tail, upper_tail),
        win_rate_percent_interval=_interval(
            tuple(win_rates), lower_tail, upper_tail
        ),
    )


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


def _percentile(
    ordered: tuple[Decimal, ...],
    percent: Decimal,
) -> Decimal:
    if not ordered:
        raise ValueError("percentile requires values")
    scaled = Decimal(len(ordered) - 1) * percent / HUNDRED
    index = int(scaled)
    return ordered[index]


def cost_warning(
    *,
    low: PullbackRun,
    base: PullbackRun,
    high: PullbackRun,
    stress: PullbackRun,
) -> tuple[str, ...]:
    identity = (
        base.market,
        base.mode,
        base.variant_id,
        base.period,
    )
    if any(
        (
            run.market,
            run.mode,
            run.variant_id,
            run.period,
        )
        != identity
        for run in (low, high, stress)
    ):
        raise ValueError("cost warning runs must share one configuration")
    warnings: list[str] = []
    if low.net_return_percent > ZERO and base.net_return_percent <= ZERO:
        warnings.append("LOW_COST_ONLY_EDGE")
    if base.net_return_percent > ZERO and stress.net_return_percent < ZERO:
        warnings.append("STRESS_COLLAPSE")
    if (
        base.total_costs > abs(base.gross_pnl)
        and base.total_costs > ZERO
    ):
        warnings.append("COST_DOMINATED")
    if (
        abs(base.net_funding) > abs(base.net_pnl)
        and base.net_funding != ZERO
    ):
        warnings.append("FUNDING_DOMINATED_RESULT")
    return tuple(warnings)


def assess_candidate(
    *,
    market: str,
    mode: str,
    variant_id: str,
    development: WalkForwardSummary,
    validation: WalkForwardSummary,
    validation_stress: WalkForwardSummary,
    validation_run: PullbackRun,
    concentration: dict[str, Decimal],
    bootstrap: BootstrapResult,
    total_trade_count: int,
    consumed_period_used: bool,
    validation_lock_unchanged: bool,
) -> CandidateAssessment:
    strongly_negative_floor = -validation_run.initial_capital / HUNDRED
    criteria = (
        ("minimum_30_total_trades", total_trade_count >= 30),
        (
            "development_median_non_negative",
            development.median_return_percent >= ZERO,
        ),
        (
            "validation_median_non_negative",
            validation.median_return_percent >= ZERO,
        ),
        (
            "development_positive_folds_at_least_50_percent",
            development.positive_fold_percent >= Decimal("50"),
        ),
        (
            "validation_positive_folds_at_least_50_percent",
            validation.positive_fold_percent >= Decimal("50"),
        ),
        ("validation_net_return_non_negative", validation_run.net_return_percent >= ZERO),
        (
            "worst_drawdown_at_most_10_percent",
            validation_run.maximum_drawdown_percent <= Decimal("10"),
        ),
        (
            "zero_trade_folds_at_most_25_percent",
            max(
                development.zero_trade_fold_percent,
                validation.zero_trade_fold_percent,
            )
            <= Decimal("25"),
        ),
        (
            "stress_positive_folds_at_least_30_percent",
            validation_stress.positive_fold_percent >= Decimal("30"),
        ),
        (
            "best_trade_concentration_at_most_50_percent",
            concentration["top_1_percent"] <= Decimal("50"),
        ),
        (
            "without_top_three_not_strongly_negative",
            concentration["net_pnl_without_top_3"] >= strongly_negative_floor,
        ),
        (
            "bootstrap_not_strongly_negative",
            bootstrap.status
            not in {
                BootstrapStatus.NEGATIVE_UNCERTAIN,
                BootstrapStatus.INSUFFICIENT_SAMPLE,
            },
        ),
        ("no_liquidation", validation_run.liquidation_count == 0),
        ("consumed_period_excluded", not consumed_period_used),
        ("validation_lock_unchanged", validation_lock_unchanged),
    )
    failures = tuple(name for name, passed in criteria if not passed)
    if not failures:
        classification = PullbackClassification.PROMISING_FOR_FUTURE_HOLDOUT
        rationale = "All pre-registered continuation criteria passed."
    else:
        inconclusive_failures = {
            "minimum_30_total_trades",
            "bootstrap_not_strongly_negative",
        }
        if set(failures).issubset(inconclusive_failures):
            classification = PullbackClassification.INCONCLUSIVE
            rationale = "Evidence is insufficient for the pre-registered decision."
        else:
            classification = PullbackClassification.NOT_PROMISING
            rationale = "One or more substantive pre-registered criteria failed."
    return CandidateAssessment(
        market=market,
        mode=mode,
        variant_id=variant_id,
        classification=classification,
        criteria=criteria,
        failures=failures,
        rationale=rationale,
    )


def no_development_assessment(
    *,
    market: str,
    mode: str,
) -> CandidateAssessment:
    return CandidateAssessment(
        market=market,
        mode=mode,
        variant_id=None,
        classification=PullbackClassification.NO_DEVELOPMENT_HYPOTHESIS,
        criteria=(),
        failures=("development_selection_threshold",),
        rationale=(
            "No pullback hypothesis qualified in development; validation ran "
            "the original baseline only."
        ),
    )


def build_future_holdout_plan(
    assessments: tuple[CandidateAssessment, ...],
) -> dict[str, object]:
    promising = tuple(
        assessment
        for assessment in assessments
        if assessment.classification
        is PullbackClassification.PROMISING_FOR_FUTURE_HOLDOUT
    )
    if not promising:
        return {
            "status": "NO_HOLDOUT_PLAN",
            "candidate_created": False,
            "execution_started": False,
            "reason": "No configuration met all pre-registered continuation criteria.",
        }
    return {
        "status": "PLAN_ONLY",
        "candidate_created": False,
        "execution_started": False,
        "data_after": "2026-07-01T00:00:00Z",
        "minimum_calendar_days": 90,
        "minimum_closed_trades": 20,
        "configuration_immutable": True,
        "adjustments_allowed": False,
        "version_reset_on_change": True,
        "configurations": [
            {
                "market": assessment.market,
                "mode": assessment.mode,
                "variant_id": assessment.variant_id,
            }
            for assessment in promising
        ],
    }
