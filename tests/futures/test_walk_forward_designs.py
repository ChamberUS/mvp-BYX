from datetime import UTC, datetime

from adaptive_trader.futures.temporal_robustness import walk_forward_design_periods


def test_walk_forward_designs_are_fixed_and_forward_only() -> None:
    rows = walk_forward_design_periods(
        datetime(2022, 1, 1, tzinfo=UTC),
        datetime(2025, 12, 31, 23, tzinfo=UTC),
    )
    assert {row["design"] for row in rows} == {
        "ROLLING_365_90_90",
        "EXPANDING_365_90_90",
        "ROLLING_730_90_90",
        "ROLLING_365_180_90",
    }
    assert all(row["validation_start"] > row["train_end"] for row in rows)
