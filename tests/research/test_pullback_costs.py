from adaptive_trader.research.pullback_analysis import cost_warning
from tests.research.pullback_helpers import run_with_return


def test_all_four_fixed_cost_scenarios_emit_registered_warnings() -> None:
    warnings = cost_warning(
        low=run_with_return("LOW", "1"),
        base=run_with_return(
            "BASE",
            "0",
            gross_pnl="0.5",
            total_costs="2",
            net_funding="3",
        ),
        high=run_with_return("HIGH", "-1"),
        stress=run_with_return("STRESS", "-2"),
    )

    assert "LOW_COST_ONLY_EDGE" in warnings
    assert "COST_DOMINATED" in warnings
    assert "FUNDING_DOMINATED_RESULT" in warnings


def test_stress_collapse_requires_positive_base_and_negative_stress() -> None:
    warnings = cost_warning(
        low=run_with_return("LOW", "2"),
        base=run_with_return("BASE", "1"),
        high=run_with_return("HIGH", "0"),
        stress=run_with_return("STRESS", "-1"),
    )

    assert "STRESS_COLLAPSE" in warnings
