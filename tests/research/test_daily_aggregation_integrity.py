from datetime import UTC, datetime

from adaptive_trader.research.daily_aggregation import (
    DailyAggregationAction,
    DailyCandleAggregator,
)
from tests.research.test_daily_aggregation import _spot_hours


def test_daily_aggregation_integrity_excludes_and_audits_missing_utc_hour() -> None:
    source = _spot_hours(datetime(2023, 3, 24, tzinfo=UTC))
    source_without_13h = source[:13] + source[14:]

    result = DailyCandleAggregator().aggregate_spot(source_without_13h)

    assert result.candles == ()
    assert result.integrity.source_candle_count == 23
    assert result.integrity.source_day_count == 1
    assert result.integrity.incomplete_day_count == 1
    assert result.integrity.excluded_day_count == 1
    assert result.audits[0].action is DailyAggregationAction.EXCLUDED
    assert result.audits[0].missing_open_times == (
        datetime(2023, 3, 24, 13, tzinfo=UTC),
    )
    assert "MISSING_HOURLY_CANDLES" in result.audits[0].issues
