from decimal import Decimal

from adaptive_trader.research.datasets import holdout_split, validate_dataset
from adaptive_trader.research.experiment import ResearchExperimentRunner
from adaptive_trader.research.robustness import consolidate_runs, diagnose


def test_robustness_summary_handles_empty_trades_and_failed_folds(
    daily_candles, research_config
) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )
    runner = ResearchExperimentRunner()
    runs = runner.run_segments((split.train, split.validation, split.test), research_config)
    summary = consolidate_runs(runs)
    diagnostics = diagnose(runs[0].result, runs[1].result, runs)

    assert summary.fold_count == 3
    assert summary.completed_fold_count == 3
    assert "TOO_FEW_TRADES" in summary.warnings
    assert diagnostics.positive_fold_percent == Decimal("0")
