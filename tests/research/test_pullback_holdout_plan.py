from dataclasses import replace
from decimal import Decimal

from adaptive_trader.research.pullback_analysis import (
    BootstrapStatus,
    CandidateAssessment,
    PullbackClassification,
    assess_candidate,
    bootstrap_trades,
    build_future_holdout_plan,
)
from tests.research.pullback_helpers import (
    closed_trade,
    fold_summary,
    run_with_return,
)


def assessment(
    classification: PullbackClassification,
) -> CandidateAssessment:
    return CandidateAssessment(
        market="SPOT",
        mode="LONG",
        variant_id="PULLBACK_BASE",
        classification=classification,
        criteria=(),
        failures=(),
        rationale="fixture",
    )


def test_no_promising_configuration_creates_no_holdout_plan() -> None:
    plan = build_future_holdout_plan(
        (assessment(PullbackClassification.NOT_PROMISING),)
    )

    assert plan["status"] == "NO_HOLDOUT_PLAN"
    assert plan["candidate_created"] is False
    assert plan["execution_started"] is False


def test_promising_result_only_creates_a_future_plan() -> None:
    plan = build_future_holdout_plan(
        (
            assessment(
                PullbackClassification.PROMISING_FOR_FUTURE_HOLDOUT
            ),
        )
    )

    assert plan["status"] == "PLAN_ONLY"
    assert plan["data_after"] == "2026-07-01T00:00:00Z"
    assert plan["minimum_calendar_days"] == 90
    assert plan["minimum_closed_trades"] == 20
    assert plan["execution_started"] is False


def test_candidate_passes_every_pre_registered_continuity_criterion() -> None:
    development = fold_summary("PULLBACK_BASE", "1", "75")
    validation = fold_summary(
        "PULLBACK_BASE",
        "1",
        "75",
        period="VALIDATION",
    )
    stress = replace(validation, scenario="STRESS", positive_fold_percent=Decimal("50"))
    validation_run = run_with_return("BASE", "1")
    bootstrap = bootstrap_trades(
        market="SPOT",
        mode="LONG",
        variant_id="PULLBACK_BASE",
        period="VALIDATION",
        trades=tuple(closed_trade("1") for _ in range(5)),
    )

    result = assess_candidate(
        market="SPOT",
        mode="LONG",
        variant_id="PULLBACK_BASE",
        development=development,
        validation=validation,
        validation_stress=stress,
        validation_run=validation_run,
        concentration={
            "top_1_percent": Decimal("40"),
            "net_pnl_without_top_3": Decimal("1"),
        },
        bootstrap=bootstrap,
        total_trade_count=30,
        consumed_period_used=False,
        validation_lock_unchanged=True,
    )

    assert bootstrap.status is BootstrapStatus.POSITIVE_UNCERTAIN
    assert result.classification is PullbackClassification.PROMISING_FOR_FUTURE_HOLDOUT
    assert result.failures == ()


def test_candidate_is_inconclusive_only_for_sample_uncertainty() -> None:
    development = fold_summary("PULLBACK_BASE", "1", "75")
    validation = fold_summary(
        "PULLBACK_BASE",
        "1",
        "75",
        period="VALIDATION",
    )
    bootstrap = bootstrap_trades(
        market="SPOT",
        mode="LONG",
        variant_id="PULLBACK_BASE",
        period="VALIDATION",
        trades=(closed_trade("1"),),
    )

    result = assess_candidate(
        market="SPOT",
        mode="LONG",
        variant_id="PULLBACK_BASE",
        development=development,
        validation=validation,
        validation_stress=replace(
            validation,
            scenario="STRESS",
            positive_fold_percent=Decimal("50"),
        ),
        validation_run=run_with_return("BASE", "1"),
        concentration={
            "top_1_percent": Decimal("40"),
            "net_pnl_without_top_3": Decimal("1"),
        },
        bootstrap=bootstrap,
        total_trade_count=1,
        consumed_period_used=False,
        validation_lock_unchanged=True,
    )

    assert result.classification is PullbackClassification.INCONCLUSIVE
    assert set(result.failures) == {
        "minimum_30_total_trades",
        "bootstrap_not_strongly_negative",
    }


def test_consumed_period_use_makes_candidate_not_promising() -> None:
    development = fold_summary("PULLBACK_BASE", "1", "75")
    validation = fold_summary(
        "PULLBACK_BASE",
        "1",
        "75",
        period="VALIDATION",
    )
    bootstrap = bootstrap_trades(
        market="SPOT",
        mode="LONG",
        variant_id="PULLBACK_BASE",
        period="VALIDATION",
        trades=tuple(closed_trade("1") for _ in range(5)),
    )

    result = assess_candidate(
        market="SPOT",
        mode="LONG",
        variant_id="PULLBACK_BASE",
        development=development,
        validation=validation,
        validation_stress=replace(
            validation,
            scenario="STRESS",
            positive_fold_percent=Decimal("50"),
        ),
        validation_run=run_with_return("BASE", "1"),
        concentration={
            "top_1_percent": Decimal("40"),
            "net_pnl_without_top_3": Decimal("1"),
        },
        bootstrap=bootstrap,
        total_trade_count=30,
        consumed_period_used=True,
        validation_lock_unchanged=True,
    )

    assert result.classification is PullbackClassification.NOT_PROMISING
    assert "consumed_period_excluded" in result.failures
