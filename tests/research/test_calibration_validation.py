from datetime import UTC, datetime

import pytest

from adaptive_trader.research.pullback_catalog import (
    PullbackExperimentPeriods,
)


def test_consumed_years_are_rejected() -> None:
    periods = PullbackExperimentPeriods.pre_registered()
    for year in (2025, 2026):
        value = datetime(year, 1, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="consumed|2026"):
            periods.assert_research_range(value, value, "calibration")
