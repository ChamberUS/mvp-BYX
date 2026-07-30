from decimal import Decimal

from adaptive_trader.research.datasets import holdout_split, validate_dataset
from adaptive_trader.research.diagnostics import hold_reason_rows
from adaptive_trader.research.experiment import ResearchExperimentRunner


def test_hold_analysis_marks_future_values_as_post_event(daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )
    run = ResearchExperimentRunner().run_segment(split.train, research_config)
    rows = hold_reason_rows((run,), horizons=(1,))

    assert all(row["post_event_only"] is True for row in rows)
    assert all("future_return_mean" in row for row in rows)
    assert sum(row["percent"] for row in rows) == Decimal("100")
