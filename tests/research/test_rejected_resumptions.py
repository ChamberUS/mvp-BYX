from dataclasses import replace

from adaptive_trader.research.pullback_calibration import (
    rejected_resumption_rows,
)
from tests.research.pullback_calibration_helpers import calibration_trace
from tests.research.pullback_helpers import run_with_return


def test_rejected_resumption_contains_first_and_all_failures() -> None:
    run = replace(
        run_with_return("BASE", "1"),
        pullback_traces=(calibration_trace(),),
    )
    row = rejected_resumption_rows(run)[0]
    assert row["first_failure_code"] == "VOLUME_REJECTED"
    assert row["hypothetical_signal_without_failed_rule"] is True
