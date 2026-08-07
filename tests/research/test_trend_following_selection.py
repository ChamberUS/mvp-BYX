from dataclasses import replace
from decimal import Decimal

import pytest

from adaptive_trader.research.trend_following_analysis import (
    CostScenario,
    TrendFollowingOperationalStatus,
    TrendFollowingSelectionMetric,
    TrendFollowingSelectionStatus,
    select_development_hypothesis,
)


def _metric(
    variant_id: str,
    *,
    median: str = "1",
    positive: str = "50",
    drawdown: str = "5",
    concentration: str = "30",
    trades: int = 8,
    exposure: str = "20",
    complexity: int = 1,
    order: int = 0,
) -> TrendFollowingSelectionMetric:
    return TrendFollowingSelectionMetric(
        market="SPOT",
        mode="LONG",
        variant_id=variant_id,
        operational_status=TrendFollowingOperationalStatus.OPERATIONALLY_VIABLE,
        median_walk_forward_net_return=Decimal(median),
        positive_fold_percent=Decimal(positive),
        worst_drawdown_percent=Decimal(drawdown),
        top_three_concentration_percent=Decimal(concentration),
        development_trade_count=trades,
        exposure_percent=Decimal(exposure),
        complexity_rank=complexity,
        catalog_order=order,
    )


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("median_walk_forward_net_return", Decimal("2")),
        ("positive_fold_percent", Decimal("75")),
        ("worst_drawdown_percent", Decimal("4")),
        ("top_three_concentration_percent", Decimal("20")),
        ("development_trade_count", 9),
        ("exposure_percent", Decimal("10")),
        ("complexity_rank", 0),
        ("catalog_order", 0),
    ],
)
def test_selection_uses_each_pre_registered_tie_break_in_order(
    change: str,
    value: object,
) -> None:
    first = replace(_metric("A", complexity=2, order=1), catalog_order=1)
    second = replace(_metric("B", complexity=2, order=2), **{change: value})
    if change == "catalog_order":
        first = replace(first, catalog_order=2)

    selected = select_development_hypothesis((first, second))

    assert selected.selected_variant_id == "B"
    assert len(selected.ranked_variant_ids) == 2


def test_selection_uses_only_operationally_viable_development_base_rows() -> None:
    viable = _metric("VIABLE")
    restrictive = replace(
        _metric("RESTRICTIVE", median="100"),
        operational_status=TrendFollowingOperationalStatus.TOO_RESTRICTIVE,
    )

    selected = select_development_hypothesis((restrictive, viable))

    assert selected.status is TrendFollowingSelectionStatus.SELECTED_FOR_VALIDATION
    assert selected.selected_variant_id == "VIABLE"

    with pytest.raises(ValueError, match="development BASE"):
        select_development_hypothesis((replace(viable, source_period="VALIDATION"),))
    with pytest.raises(ValueError, match="development BASE"):
        select_development_hypothesis((replace(viable, cost_scenario=CostScenario.LOW),))


@pytest.mark.parametrize(
    "metric",
    [
        _metric("NEGATIVE", median="-0.01"),
        _metric("TOO_FEW_POSITIVE_FOLDS", positive="49.99"),
    ],
)
def test_selection_does_not_choose_the_least_bad_configuration(
    metric: TrendFollowingSelectionMetric,
) -> None:
    selected = select_development_hypothesis((metric,))

    assert selected.status is TrendFollowingSelectionStatus.NO_DEVELOPMENT_HYPOTHESIS
    assert selected.selected_variant_id is None
    assert selected.ranked_variant_ids == ()


def test_selection_rejects_mixed_groups_and_duplicate_variants() -> None:
    metric = _metric("A")

    with pytest.raises(ValueError, match="one market group"):
        select_development_hypothesis((metric, replace(metric, mode="SHORT")))
    with pytest.raises(ValueError, match="duplicate"):
        select_development_hypothesis((metric, metric))
