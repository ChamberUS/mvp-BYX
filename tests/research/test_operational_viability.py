from dataclasses import replace
from datetime import timedelta

from adaptive_trader.research.pullback_analysis import PullbackFold
from adaptive_trader.research.pullback_calibration import (
    OperationalStatus,
    operational_frequency,
)
from tests.research.pullback_helpers import START, closed_trade, run_with_return


def test_operational_viability_uses_frequency_thresholds() -> None:
    run = replace(
        run_with_return("BASE", "99"),
        variant_id="CALIBRATION_BASE",
        long_signals=12,
        trades=tuple(closed_trade("1") for _ in range(10)),
    )
    folds = tuple(
        PullbackFold(
            fold=index,
            train_start=START,
            train_end=START + timedelta(days=1),
            validation_start=START + timedelta(days=2),
            validation_end=START + timedelta(days=3),
            run=run,
        )
        for index in range(1, 5)
    )
    result = operational_frequency(
        run, folds, baseline_directional_trades=10
    )
    assert result.status is OperationalStatus.OPERATIONALLY_VIABLE
