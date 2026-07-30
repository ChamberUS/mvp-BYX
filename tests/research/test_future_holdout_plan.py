from datetime import UTC, datetime

from adaptive_trader.research.candidate_freeze import build_future_holdout_plan


def test_future_holdout_plan_has_minimums_and_forbids_parameter_changes() -> None:
    frozen_at = datetime(2026, 8, 1, tzinfo=UTC)
    plan = build_future_holdout_plan("candidate-v1", frozen_at)

    assert plan.minimum_calendar_days == 90
    assert plan.minimum_closed_trades == 20
    assert "PARAMETER_CHANGES" in plan.forbidden_until_complete
    assert plan.paper_trading_enabled is False


def test_candidate_change_creates_a_new_holdout_identity() -> None:
    frozen_at = datetime(2026, 8, 1, tzinfo=UTC)

    first = build_future_holdout_plan("candidate-v1", frozen_at)
    second = build_future_holdout_plan("candidate-v2", frozen_at)

    assert first.candidate_id != second.candidate_id
