from datetime import UTC, datetime

from adaptive_trader.futures.temporal_robustness import _cost_impact_rows
from tests.futures.temporal_helpers import make_result, make_trade


def test_temporal_costs_detect_low_cost_only_edge() -> None:
    exit_time = datetime(2025, 5, 1, tzinfo=UTC)
    results = {
        "LOW_COST": make_result((make_trade(exit_time, "10"),)),
        "BASE_COST": make_result((make_trade(exit_time, "-1"),)),
        "HIGH_COST": make_result((make_trade(exit_time, "-5"),)),
        "STRESS_COST": make_result((make_trade(exit_time, "-10"),)),
    }
    rows = _cost_impact_rows("TEST", results, (), ())
    year = tuple(
        row
        for row in rows
        if row["period_type"] == "YEAR" and row["period"] == "2025"
    )
    assert all("LOW_COST_ONLY_EDGE" in str(row["warning"]) for row in year)
