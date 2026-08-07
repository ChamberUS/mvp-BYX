from datetime import UTC, datetime

from adaptive_trader.futures.temporal_robustness import (
    _boundary_rows,
    predefined_temporal_boundaries,
)
from tests.futures.temporal_helpers import make_result, make_trade


def test_temporal_boundaries_are_predefined_and_exclude_2026() -> None:
    rows = predefined_temporal_boundaries()
    assert tuple(row["boundary"] for row in rows) == ("A", "B", "C", "D")
    assert all(row["validation_end"].year <= 2025 for row in rows)


def test_boundary_warning_detects_validation_direction_change() -> None:
    result = make_result(
        (
            make_trade(datetime(2022, 6, 1, tzinfo=UTC), "100"),
            make_trade(datetime(2024, 8, 1, tzinfo=UTC), "-150"),
            make_trade(datetime(2025, 1, 1, tzinfo=UTC), "20"),
        )
    )

    rows = _boundary_rows("TEST", result, ())

    assert {row["boundary_classification"] for row in rows} == {"SIGN_CHANGE"}
    assert {row["warning"] for row in rows} == {"BOUNDARY_SENSITIVE"}
