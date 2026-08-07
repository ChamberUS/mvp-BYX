from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from adaptive_trader.research.periods import ConsumedTestError
from adaptive_trader.research.trend_following_catalog import TrendFollowingPeriods


def test_periods_are_the_exact_pre_registered_development_validation_and_consumed_ranges() -> None:
    periods = TrendFollowingPeriods.pre_registered()

    assert periods.development_start == datetime(2022, 1, 1, tzinfo=UTC)
    assert periods.development_end == datetime(2023, 12, 31, 23, tzinfo=UTC)
    assert periods.validation_start == datetime(2024, 1, 1, tzinfo=UTC)
    assert periods.validation_end == datetime(2024, 12, 31, 23, tzinfo=UTC)
    assert periods.consumed_start == datetime(2025, 1, 1, tzinfo=UTC)
    assert periods.consumed_end == datetime(2026, 7, 1, tzinfo=UTC)
    periods.assert_pre_registered()


def test_changed_pre_registered_period_is_rejected() -> None:
    periods = replace(
        TrendFollowingPeriods.pre_registered(),
        development_start=datetime(2022, 1, 2, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="pre-registration"):
        periods.assert_pre_registered()


@pytest.mark.parametrize("year", [2025, 2026])
def test_consumed_2025_and_2026_ranges_are_rejected(year: int) -> None:
    periods = TrendFollowingPeriods.pre_registered()
    value = datetime(year, 1, 1, tzinfo=UTC)

    with pytest.raises(ConsumedTestError, match="2025-2026"):
        periods.assert_research_range(value, value, "selection")


def test_development_and_validation_cannot_cross_their_locked_boundaries() -> None:
    periods = TrendFollowingPeriods.pre_registered()

    periods.assert_development_range(periods.development_start, periods.development_end)
    periods.assert_validation_range(periods.validation_start, periods.validation_end)
    with pytest.raises(ValueError, match="2022-2023"):
        periods.assert_development_range(periods.development_start, periods.validation_start)
    with pytest.raises(ValueError, match="locked 2024"):
        periods.assert_validation_range(periods.development_end, periods.validation_end)


def test_periods_reject_non_utc_timestamps() -> None:
    local = timezone(timedelta(hours=-3))

    with pytest.raises(ValueError, match="UTC"):
        replace(
            TrendFollowingPeriods.pre_registered(),
            development_start=datetime(2022, 1, 1, tzinfo=local),
        )
