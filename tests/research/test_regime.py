from decimal import Decimal

from adaptive_trader.research.datasets import holdout_split, validate_dataset
from adaptive_trader.research.experiment import ResearchExperimentRunner
from adaptive_trader.research.regime_analysis import analyze_regimes


def test_regime_analysis_is_point_in_time(daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )
    run = ResearchExperimentRunner().run_segment(split.test, research_config)

    assert run.result is not None
    metrics = analyze_regimes(
        split.test,
        run.result,
        short_period=2,
        long_period=3,
        maximum_atr_relative=research_config.maximum_atr_relative,
    )

    assert sum(item.candle_count for item in metrics) == split.test.candle_count
