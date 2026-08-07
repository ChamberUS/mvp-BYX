from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trader.research.pullback_analysis import (
    PullbackFold,
    concentration_metrics,
    summarize_folds,
)
from adaptive_trader.research.pullback_catalog import PullbackExperimentPeriods
from adaptive_trader.research.pullback_experiment import (
    build_pullback_walk_forward_windows,
)
from tests.research.pullback_helpers import closed_trade, run_with_return


def test_development_walk_forward_is_fixed_rolling_365_90_90() -> None:
    windows = build_pullback_walk_forward_windows(
        period="DEVELOPMENT",
        periods=PullbackExperimentPeriods.pre_registered(),
    )

    assert len(windows) == 4
    assert windows[0].train_start == datetime(2022, 1, 1, tzinfo=UTC)
    assert windows[0].validation_start == datetime(2023, 1, 1, tzinfo=UTC)
    assert (windows[0].validation_end - windows[0].validation_start).days == 89
    assert windows[1].validation_start > windows[0].validation_start


def test_validation_windows_are_locked_inside_2024() -> None:
    periods = PullbackExperimentPeriods.pre_registered()
    windows = build_pullback_walk_forward_windows(
        period="VALIDATION",
        periods=periods,
    )

    assert len(windows) == 4
    assert all(window.train_start == periods.development_start for window in windows)
    assert all(window.train_end == periods.development_end for window in windows)
    assert all(window.validation_start.year == 2024 for window in windows)
    assert all(window.validation_end < periods.consumed_start for window in windows)


def test_walk_forward_summary_aggregates_returns_trades_and_costs() -> None:
    first_run = replace(
        run_with_return("BASE", "2"),
        period="DEVELOPMENT",
        trades=(closed_trade("2", period="DEVELOPMENT"),),
        long_pnl=Decimal("2"),
    )
    second_run = replace(
        first_run,
        net_return_percent=Decimal("-1"),
        maximum_drawdown_percent=Decimal("3"),
        total_costs=Decimal("2"),
        net_funding=Decimal("-0.5"),
        trades=(),
        long_pnl=Decimal("-1"),
    )
    first_start = datetime(2023, 1, 1, tzinfo=UTC)
    folds = (
        PullbackFold(
            fold=1,
            train_start=datetime(2022, 1, 1, tzinfo=UTC),
            train_end=first_start - timedelta(hours=1),
            validation_start=first_start,
            validation_end=first_start + timedelta(days=90) - timedelta(hours=1),
            run=first_run,
        ),
        PullbackFold(
            fold=2,
            train_start=datetime(2022, 4, 1, tzinfo=UTC),
            train_end=first_start + timedelta(days=90) - timedelta(hours=1),
            validation_start=first_start + timedelta(days=90),
            validation_end=first_start + timedelta(days=180) - timedelta(hours=1),
            run=second_run,
        ),
    )

    summary = summarize_folds(folds)

    assert summary.fold_count == 2
    assert summary.positive_fold_percent == Decimal("50")
    assert summary.zero_trade_fold_percent == Decimal("50")
    assert summary.median_return_percent == Decimal("0.5")
    assert summary.maximum_drawdown_percent == Decimal("3")
    assert summary.total_costs == Decimal("3")
    assert summary.net_funding == Decimal("-0.5")
    assert summary.trades == 1


def test_walk_forward_summary_rejects_empty_or_mixed_folds() -> None:
    with pytest.raises(ValueError, match="requires folds"):
        summarize_folds(())

    run = replace(run_with_return("BASE", "1"), period="DEVELOPMENT")
    fold = PullbackFold(
        fold=1,
        train_start=datetime(2022, 1, 1, tzinfo=UTC),
        train_end=datetime(2022, 12, 31, 23, tzinfo=UTC),
        validation_start=datetime(2023, 1, 1, tzinfo=UTC),
        validation_end=datetime(2023, 3, 31, 23, tzinfo=UTC),
        run=run,
    )

    with pytest.raises(ValueError, match="share one configuration"):
        summarize_folds((fold, replace(fold, run=replace(run, mode="SHORT"))))


def test_concentration_handles_no_positive_pnl() -> None:
    metrics = concentration_metrics(
        (closed_trade("-2"), closed_trade("-1"))
    )

    assert metrics["top_1_percent"] == Decimal("0")
    assert metrics["net_pnl_without_top_1"] == Decimal("-2")
