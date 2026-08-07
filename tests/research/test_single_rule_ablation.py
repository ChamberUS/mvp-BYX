from dataclasses import replace

from adaptive_trader.research.pullback_calibration import (
    ABLATION_RULES,
    single_rule_ablation_rows,
)
from tests.research.pullback_calibration_helpers import calibration_trace
from tests.research.pullback_helpers import run_with_return


def test_ablation_removes_one_rule_at_a_time_without_signals() -> None:
    run = replace(
        run_with_return("BASE", "1"),
        pullback_traces=(calibration_trace(),),
    )
    rows = single_rule_ablation_rows(run)
    assert len(rows) == len(ABLATION_RULES)
    assert all(row["rules_removed_count"] == 1 for row in rows)
    assert all(row["signals_executed"] == 0 for row in rows)
