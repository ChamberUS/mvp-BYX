from dataclasses import replace
from decimal import Decimal

import pytest

from adaptive_trader.research.trend_following_analysis import (
    DefensiveComparisonClassification,
    RiskProfileMetrics,
    compare_defensive_risk,
)
from adaptive_trader.research.trend_following_catalog import TrendFollowingRiskModel


def _metrics(
    variant_id: str,
    risk_model: TrendFollowingRiskModel,
) -> RiskProfileMetrics:
    return RiskProfileMetrics(
        market="SPOT",
        mode="LONG",
        period="DEVELOPMENT",
        variant_id=variant_id,
        exit_period_days=20,
        risk_model=risk_model,
        trade_count=10,
        net_return_percent=Decimal("8"),
        maximum_drawdown_percent=Decimal("10"),
        volatility_percent=Decimal("12"),
        maximum_loss_percent=Decimal("3"),
        recovery_duration_days=Decimal("40"),
        defensive_activations=0,
        defensive_period_percent=Decimal("0"),
        half_risk_trade_count=0,
    )


def test_defensive_comparison_reports_drawdown_return_recovery_and_tradeoffs() -> None:
    fixed = _metrics("FIXED", TrendFollowingRiskModel.FIXED)
    defensive = replace(
        _metrics("DEFENSIVE", TrendFollowingRiskModel.DEFENSIVE),
        net_return_percent=Decimal("6"),
        maximum_drawdown_percent=Decimal("7"),
        volatility_percent=Decimal("9"),
        maximum_loss_percent=Decimal("2"),
        recovery_duration_days=Decimal("50"),
        defensive_activations=2,
        defensive_period_percent=Decimal("25"),
        half_risk_trade_count=4,
    )

    comparison = compare_defensive_risk(fixed, defensive)

    assert comparison.return_difference_percent == Decimal("-2")
    assert comparison.drawdown_difference_percent == Decimal("-3")
    assert comparison.volatility_difference_percent == Decimal("-3")
    assert comparison.maximum_loss_difference_percent == Decimal("-1")
    assert comparison.recovery_duration_difference_days == Decimal("10")
    assert comparison.upside_sacrificed_percent == Decimal("2")
    assert comparison.downside_avoided_percent == Decimal("1")
    assert comparison.classifications == (
        DefensiveComparisonClassification.DRAWDOWN_IMPROVED,
        DefensiveComparisonClassification.RETURN_REDUCED,
        DefensiveComparisonClassification.RECOVERY_DELAYED,
    )


def test_defensive_comparison_marks_small_samples_and_no_material_effect() -> None:
    fixed = _metrics("FIXED", TrendFollowingRiskModel.FIXED)
    defensive = _metrics("DEFENSIVE", TrendFollowingRiskModel.DEFENSIVE)

    unchanged = compare_defensive_risk(fixed, defensive)
    insufficient = compare_defensive_risk(
        replace(fixed, trade_count=7),
        replace(defensive, trade_count=7),
    )

    assert unchanged.classifications == (DefensiveComparisonClassification.NO_MATERIAL_EFFECT,)
    assert insufficient.classifications == (DefensiveComparisonClassification.INSUFFICIENT_SAMPLE,)


def test_defensive_comparison_rejects_non_equivalent_pairs() -> None:
    fixed = _metrics("FIXED", TrendFollowingRiskModel.FIXED)
    defensive = _metrics("DEFENSIVE", TrendFollowingRiskModel.DEFENSIVE)

    with pytest.raises(ValueError, match="equivalent"):
        compare_defensive_risk(fixed, replace(defensive, exit_period_days=10))
    with pytest.raises(ValueError, match="FIXED"):
        compare_defensive_risk(
            replace(fixed, risk_model=TrendFollowingRiskModel.DEFENSIVE),
            defensive,
        )
