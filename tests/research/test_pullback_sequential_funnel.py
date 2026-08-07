from dataclasses import replace

from adaptive_trader.research.pullback_calibration import (
    sequential_funnel_rows,
)
from tests.research.pullback_calibration_helpers import calibration_trace
from tests.research.pullback_helpers import run_with_return


def test_funnel_is_monotonic() -> None:
    run = replace(
        run_with_return("BASE", "1"),
        pullback_traces=(calibration_trace(),),
    )
    rows = sequential_funnel_rows(run)
    assert all(
        current["passed_count"] <= previous["passed_count"]
        for previous, current in zip(rows, rows[1:], strict=False)
    )
    assert all(row["failed_count"] >= 0 for row in rows)
