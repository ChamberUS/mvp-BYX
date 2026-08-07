from adaptive_trader.research.pullback_calibration import (
    all_failure_codes,
    first_failure_code,
    logic_audit_rows,
)
from tests.research.pullback_calibration_helpers import calibration_trace


def test_detects_first_and_multiple_rule_failures() -> None:
    trace = calibration_trace(
        entry_extension_valid=False,
        volume_valid=False,
    )
    assert first_failure_code(trace) == "PRICE_OVEREXTENDED"
    assert all_failure_codes(trace) == (
        "PRICE_OVEREXTENDED",
        "VOLUME_REJECTED",
    )
    audit = logic_audit_rows((trace,))
    assert [row["order"] for row in audit] == list(range(1, 15))
