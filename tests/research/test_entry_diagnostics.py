from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from adaptive_trader.research.datasets import holdout_split, validate_dataset
from adaptive_trader.research.diagnostics import _candle_index, entry_diagnostic_rows
from adaptive_trader.research.experiment import ResearchExperimentRunner


def test_entry_diagnostics_is_available_without_float_conversion(
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
    run = ResearchExperimentRunner().run_segment(split.train, research_config)
    rows = entry_diagnostic_rows((run,))

    for row in rows:
        assert isinstance(row["net_pnl"], Decimal)
        assert "excursion_efficiency" in row


def test_candle_lookup_accepts_trade_exit_close_time(daily_candles) -> None:
    candle = replace(
        daily_candles[0],
        close_time=daily_candles[0].open_time + timedelta(hours=23),
    )

    assert _candle_index((candle,), candle.close_time) == 0
