from decimal import Decimal

from adaptive_trader.research.trend_following_analysis import (
    TrendFollowingOperationalMetrics,
    TrendFollowingOperationalStatus,
    assess_operational_viability,
)


def _metrics(
    *,
    trades: int = 8,
    folds: int = 4,
    folds_with_trades: int = 2,
    exposure: str = "90",
) -> TrendFollowingOperationalMetrics:
    return TrendFollowingOperationalMetrics(
        market="SPOT",
        mode="LONG",
        variant_id="TF_DONCHIAN_20_FIXED_RISK",
        development_trade_count=trades,
        fold_count=folds,
        folds_with_trades=folds_with_trades,
        exposure_percent=Decimal(exposure),
    )


def test_operational_viability_accepts_all_exact_threshold_boundaries() -> None:
    result = assess_operational_viability(_metrics())

    assert result.status is TrendFollowingOperationalStatus.OPERATIONALLY_VIABLE
    assert result.folds_with_trades_percent == Decimal("50")
    assert result.zero_trade_fold_percent == Decimal("50")
    assert all(passed for _, passed in result.criteria)


def test_trade_count_below_eight_is_insufficient_sample() -> None:
    result = assess_operational_viability(_metrics(trades=7))

    assert result.status is TrendFollowingOperationalStatus.INSUFFICIENT_SAMPLE


def test_sparse_fold_distribution_is_too_restrictive() -> None:
    result = assess_operational_viability(_metrics(folds_with_trades=1))

    assert result.status is TrendFollowingOperationalStatus.TOO_RESTRICTIVE
    assert result.zero_trade_fold_percent == Decimal("75")


def test_exposure_above_ninety_percent_is_too_permissive() -> None:
    result = assess_operational_viability(_metrics(exposure="90.01"))

    assert result.status is TrendFollowingOperationalStatus.TOO_PERMISSIVE


def test_no_walk_forward_folds_is_insufficient_sample() -> None:
    result = assess_operational_viability(_metrics(trades=8, folds=0, folds_with_trades=0))

    assert result.status is TrendFollowingOperationalStatus.INSUFFICIENT_SAMPLE
