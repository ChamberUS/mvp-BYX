from decimal import Decimal

from adaptive_trader.research.costs import run_cost_scenarios_by_fold
from adaptive_trader.research.datasets import holdout_split, validate_dataset
from adaptive_trader.research.experiment import ResearchExperimentRunner


def test_cost_scenarios_include_each_fold_and_consolidated(daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )
    runner = ResearchExperimentRunner()
    run = runner.run_segment(split.validation, research_config)
    rows = run_cost_scenarios_by_fold((run,), research_config, runner)

    assert {row["scenario"] for row in rows} >= {
        "LOW_COST",
        "BASE_COST",
        "HIGH_COST",
        "STRESS_COST",
    }
    assert any(row["fold"] == "CONSOLIDATED" for row in rows)
