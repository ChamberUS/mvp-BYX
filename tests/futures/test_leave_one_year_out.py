from datetime import UTC, datetime

from adaptive_trader.futures.temporal_robustness import leave_one_year_out
from tests.futures.temporal_helpers import make_result, make_trade


def test_leave_one_year_out_detects_direction_change() -> None:
    result = make_result(
        (
            make_trade(datetime(2022, 6, 1, tzinfo=UTC), "-20"),
            make_trade(datetime(2025, 6, 1, tzinfo=UTC), "100"),
        )
    )
    rows = leave_one_year_out("TEST", result)
    held_out_2025 = next(row for row in rows if row["held_out_year"] == 2025)
    assert held_out_2025["direction_change"] is True
    assert held_out_2025["warning"] == "SINGLE_YEAR_DEPENDENCE"
