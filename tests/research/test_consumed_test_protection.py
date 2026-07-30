from datetime import UTC, datetime

import pytest

from adaptive_trader.research.periods import ConsumedTestError, ResearchPeriods


def test_consumed_test_cannot_be_used_for_selection() -> None:
    periods = ResearchPeriods(
        development_start=datetime(2022, 1, 1, tzinfo=UTC),
        development_end=datetime(2024, 12, 31, 23, tzinfo=UTC),
        validation_start=datetime(2025, 1, 1, tzinfo=UTC),
        validation_end=datetime(2025, 12, 31, 23, tzinfo=UTC),
        consumed_test_start=datetime(2026, 1, 1, tzinfo=UTC),
        consumed_test_end=datetime(2026, 7, 1, tzinfo=UTC),
    )

    with pytest.raises(ConsumedTestError):
        periods.assert_not_consumed(
            datetime(2025, 12, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            "sensitivity",
        )
