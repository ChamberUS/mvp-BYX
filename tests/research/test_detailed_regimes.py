from decimal import Decimal

from adaptive_trader.research.datasets import holdout_split, validate_dataset
from adaptive_trader.research.diagnostics import detailed_regime_rows
from adaptive_trader.research.experiment import ResearchExperimentRunner


def test_detailed_regimes_use_point_in_time_traces(daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )
    run = ResearchExperimentRunner().run_segment(split.train, research_config)
    rows = detailed_regime_rows((run,))

    assert all(row["candle_count"] == row["eligible_candle_count"] for row in rows)
