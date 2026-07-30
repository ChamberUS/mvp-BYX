from decimal import Decimal

from adaptive_trader.research.spot_hypotheses import (
    ValidationLock,
    load_spot_hypothesis_catalog,
)
from adaptive_trader.strategy.regime import SpotRegimeMode


def test_validation_lock_is_immutable_for_the_selected_parameters() -> None:
    hypothesis = load_spot_hypothesis_catalog().by_id("SPOT_TIME_EXIT_12_V1")
    lock = ValidationLock.create(
        hypothesis,
        SpotRegimeMode.STRICT_TRENDING_UP,
        Decimal("2"),
    )

    lock.assert_unchanged(
        variant_id=hypothesis.variant_id,
        regime_mode=SpotRegimeMode.STRICT_TRENDING_UP,
        target_r_multiple=Decimal("2"),
        time_exit_candles=12,
    )


def test_validation_parameter_change_requires_a_new_experiment() -> None:
    hypothesis = load_spot_hypothesis_catalog().by_id("SPOT_TIME_EXIT_12_V1")
    lock = ValidationLock.create(
        hypothesis,
        SpotRegimeMode.STRICT_TRENDING_UP,
        Decimal("2"),
    )

    try:
        lock.assert_unchanged(
            variant_id=hypothesis.variant_id,
            regime_mode=SpotRegimeMode.EMA_TREND_ONLY,
            target_r_multiple=Decimal("2"),
            time_exit_candles=12,
        )
    except ValueError as exc:
        assert "development lock" in str(exc)
    else:
        raise AssertionError("validation changed a locked parameter")
