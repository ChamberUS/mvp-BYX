from decimal import Decimal

from adaptive_trader.research.datasets import holdout_split, validate_dataset
from adaptive_trader.research.diagnostics import decision_funnel_rows
from adaptive_trader.research.experiment import ResearchExperimentRunner


def test_decision_funnel_has_coherent_stages(daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )
    run = ResearchExperimentRunner().run_segment(split.train, research_config)
    rows = decision_funnel_rows((run,), research_config)

    row = rows[0]
    assert row["buy_signals"] <= row["eligible_after_warmup"]
    assert row["risk_approved"] <= row["buy_signals"]
    assert row["orders_executed"] <= row["risk_approved"]


def test_decision_funnel_aggregates_closed_trades(daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )
    runner = ResearchExperimentRunner()
    runs = (
        runner.run_segment(split.train, research_config),
        runner.run_segment(split.validation, research_config),
    )

    rows = decision_funnel_rows(runs, research_config)

    assert rows[0]["scope"] == "all_segments"
    assert rows[0]["closed_trades"] == sum(
        run.result.metrics.closed_trade_count for run in runs if run.result is not None
    )
