from datetime import UTC, datetime
from decimal import Decimal

from adaptive_trader.futures.temporal_robustness import _funding_impact_rows
from tests.futures.temporal_helpers import make_result, make_trade


def test_funding_is_attributed_by_period_and_can_dominate() -> None:
    exit_time = datetime(2025, 5, 1, tzinfo=UTC)
    enabled = make_result((make_trade(exit_time, "10", funding="8"),))
    disabled = make_result((make_trade(exit_time, "2"),))
    rows = _funding_impact_rows(
        "TEST",
        enabled,
        disabled,
        threshold_percent=Decimal("50"),
    )
    year = next(
        row
        for row in rows
        if row["period_type"] == "YEAR" and row["period"] == "2025"
    )
    assert year["difference"] == 8
    assert year["warning"] == "FUNDING_DOMINATED_RESULT"
