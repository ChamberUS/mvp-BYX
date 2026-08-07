from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from adaptive_trader.research.daily_aggregation import DailyCandleAggregator
from tests.research.test_daily_aggregation import _spot_hours


def test_daily_dataset_hash_includes_every_used_hour_and_configuration() -> None:
    source = _spot_hours(datetime(2023, 1, 2, tzinfo=UTC))
    changed_source = (
        *source[:5],
        replace(source[5], high=Decimal("108")),
        *source[6:],
    )

    baseline = DailyCandleAggregator().aggregate_spot(source)
    changed = DailyCandleAggregator().aggregate_spot(changed_source)

    assert baseline.candles == changed.candles
    assert baseline.daily_rows_hash == changed.daily_rows_hash
    assert baseline.source_hourly_hash != changed.source_hourly_hash
    assert baseline.daily_candle_hash != changed.daily_candle_hash
    assert baseline.aggregation_config_hash
