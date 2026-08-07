from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from adaptive_trader.research.trend_following_analysis import (
    COST_SCENARIOS,
    TrendFollowingLockedSelection,
    TrendFollowingOperationalStatus,
    TrendFollowingSelectionMetric,
    TrendFollowingValidationLock,
)
from adaptive_trader.research.trend_following_catalog import (
    SPOT_LONG,
    TrendFollowingPeriods,
    load_trend_following_catalog,
)


def _metric(variant_id: str, complexity: int, order: int) -> TrendFollowingSelectionMetric:
    return TrendFollowingSelectionMetric(
        market="SPOT",
        mode="LONG",
        variant_id=variant_id,
        operational_status=TrendFollowingOperationalStatus.OPERATIONALLY_VIABLE,
        median_walk_forward_net_return=Decimal("1"),
        positive_fold_percent=Decimal("50"),
        worst_drawdown_percent=Decimal("5"),
        top_three_concentration_percent=Decimal("30"),
        development_trade_count=8,
        exposure_percent=Decimal("20"),
        complexity_rank=complexity,
        catalog_order=order,
    )


def _create_lock(
    selections: tuple[TrendFollowingLockedSelection, ...],
    *,
    dataset_hashes: dict[str, str] | None = None,
    leverage: Decimal = Decimal("1"),
) -> TrendFollowingValidationLock:
    return TrendFollowingValidationLock.create(
        selections=selections,
        catalog=load_trend_following_catalog(),
        dataset_hashes=(
            dataset_hashes
            if dataset_hashes is not None
            else {
                "spot_hourly": "hourly-hash",
                "spot_daily": "daily-hash",
                "aggregation_config": "aggregation-hash",
            }
        ),
        periods=TrendFollowingPeriods.pre_registered(),
        git_commit="a889362",
        git_dirty=True,
        leverage=leverage,
        cost_parameters={
            "base_taker_fee_bps": Decimal("20"),
            "base_spread_bps": Decimal("2"),
            "base_slippage_bps": Decimal("5"),
        },
        risk_model="RISK_BASED_POSITION_SIZING_V1",
        selection_timestamp=datetime(2026, 7, 30, tzinfo=UTC),
    )


def _assert_unchanged(
    lock: TrendFollowingValidationLock,
    selections: tuple[TrendFollowingLockedSelection, ...],
    *,
    dataset_hashes: dict[str, str] | None = None,
) -> None:
    lock.assert_unchanged(
        selections=selections,
        catalog=load_trend_following_catalog(),
        dataset_hashes=(
            dataset_hashes
            if dataset_hashes is not None
            else {
                "spot_hourly": "hourly-hash",
                "spot_daily": "daily-hash",
                "aggregation_config": "aggregation-hash",
            }
        ),
        periods=TrendFollowingPeriods.pre_registered(),
        git_commit="a889362",
        git_dirty=True,
        leverage=Decimal("1"),
        cost_parameters={
            "base_taker_fee_bps": Decimal("20"),
            "base_spread_bps": Decimal("2"),
            "base_slippage_bps": Decimal("5"),
        },
        risk_model="RISK_BASED_POSITION_SIZING_V1",
        selection_timestamp=datetime(2026, 7, 30, tzinfo=UTC),
    )


def test_validation_lock_captures_selection_catalog_datasets_costs_and_periods() -> None:
    catalog = load_trend_following_catalog()
    hypothesis = catalog.hypotheses[0]
    selection = TrendFollowingLockedSelection(
        group=SPOT_LONG,
        hypothesis=hypothesis,
        development_metric=_metric(
            hypothesis.variant_id,
            hypothesis.complexity_rank,
            hypothesis.catalog_order,
        ),
    )
    lock = _create_lock((selection,))

    assert lock.selections == (selection,)
    assert lock.cost_scenarios == COST_SCENARIOS
    assert lock.leverage == Decimal("1")
    assert len(lock.development_fingerprint) == 64
    lock.assert_valid()
    _assert_unchanged(lock, (selection,))


def test_validation_lock_rejects_any_post_selection_change() -> None:
    catalog = load_trend_following_catalog()
    hypothesis = catalog.hypotheses[0]
    selection = TrendFollowingLockedSelection(
        group=SPOT_LONG,
        hypothesis=hypothesis,
        development_metric=_metric(
            hypothesis.variant_id,
            hypothesis.complexity_rank,
            hypothesis.catalog_order,
        ),
    )
    lock = _create_lock((selection,))

    with pytest.raises(ValueError, match="development lock"):
        _assert_unchanged(
            lock,
            (selection,),
            dataset_hashes={"spot_daily": "changed"},
        )
    with pytest.raises(ValueError, match="fingerprint"):
        replace(lock, catalog_hash="changed").assert_valid()
    with pytest.raises(ValueError, match="fingerprint"):
        replace(lock, cost_scenarios=()).assert_valid()


def test_validation_lock_is_deeply_immutable_and_supports_no_selection() -> None:
    lock = _create_lock(())
    field_name = "git_dirty"

    with pytest.raises(FrozenInstanceError):
        setattr(lock, field_name, False)
    assert lock.selections == ()


def test_lock_rejects_validation_metrics_and_leverage_above_one() -> None:
    catalog = load_trend_following_catalog()
    hypothesis = catalog.hypotheses[0]
    metric = _metric(
        hypothesis.variant_id,
        hypothesis.complexity_rank,
        hypothesis.catalog_order,
    )

    with pytest.raises(ValueError, match="development BASE"):
        TrendFollowingLockedSelection(
            group=SPOT_LONG,
            hypothesis=hypothesis,
            development_metric=replace(metric, source_period="VALIDATION"),
        )
    with pytest.raises(ValueError, match="leverage 1"):
        _create_lock((), leverage=Decimal("2"))
