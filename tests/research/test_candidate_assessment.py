from decimal import Decimal

from adaptive_trader.research.datasets import holdout_split, validate_dataset
from adaptive_trader.research.diagnostics import candidate_assessment
from adaptive_trader.research.experiment import ResearchExperimentRunner


def test_candidate_assessment_has_explanations(daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )
    run = ResearchExperimentRunner().run_segment(split.validation, research_config)
    assessment = candidate_assessment((run,), minimum_closed_trades=1)

    assert assessment["status"] in {"CANDIDATE", "NOT_CANDIDATE", "INCONCLUSIVE"}
    assert "checks" in assessment
    assert assessment["uses_consumed_test_period"] is False
