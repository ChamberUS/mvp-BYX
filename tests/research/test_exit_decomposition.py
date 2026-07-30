from decimal import Decimal

from adaptive_trader.research.datasets import holdout_split, validate_dataset
from adaptive_trader.research.diagnostics import entry_exit_decomposition_rows
from adaptive_trader.research.experiment import ResearchExperimentRunner


def test_exit_decomposition_keeps_current_entries(daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )
    rows = entry_exit_decomposition_rows(
        split.validation,
        research_config,
        ResearchExperimentRunner(),
    )

    assert {row["entry_rules"] for row in rows} == {"CURRENT"}
    assert "TIME_EXIT_6" in {row["scenario"] for row in rows}
