from dataclasses import replace

from adaptive_trader.research.pullback_calibration import (
    OperationalFrequency,
    OperationalStatus,
    select_by_frequency,
)


def test_selection_has_no_return_input_and_follows_fixed_ties() -> None:
    base = OperationalFrequency(
        market="SPOT",
        mode="LONG",
        variant_id="A",
        pullbacks=12,
        resumptions=12,
        signals=12,
        trades=10,
        trades_per_year=5,
        fold_count=4,
        folds_with_trades=3,
        folds_with_trades_percent=75,
        zero_trade_fold_percent=25,
        long_signals=12,
        short_signals=0,
        exposure_percent=10,
        status=OperationalStatus.OPERATIONALLY_VIABLE,
    )
    second = replace(base, variant_id="B", folds_with_trades=4)
    assert select_by_frequency(
        (base, second),
        catalog_order=("A", "B"),
        complexity={"A": 0, "B": 1},
    ) == ("B", "A")
