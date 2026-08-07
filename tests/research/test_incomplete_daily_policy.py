from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trader.domain.models import Candle
from adaptive_trader.research.daily_aggregation import (
    DailyAggregationAction,
    DailyAggregationError,
    DailyCandleAggregator,
    IncompleteDayPolicy,
)


def _spot_hours() -> tuple[Candle, ...]:
    start = datetime(2023, 3, 24, tzinfo=UTC)
    return tuple(
        Candle(
            symbol="ETHUSDT",
            interval="1h",
            timestamp=start + timedelta(hours=index),
            close_time=start + timedelta(hours=index + 1) - timedelta(milliseconds=1),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1"),
            quote_volume=Decimal("100"),
            trades_count=1,
        )
        for index in range(24)
    )


def test_fail_policy_rejects_missing_hour() -> None:
    source = _spot_hours()
    missing_hour = source[:13] + source[14:]

    with pytest.raises(DailyAggregationError, match="2023-03-24"):
        DailyCandleAggregator(policy=IncompleteDayPolicy.FAIL).aggregate_spot(missing_hour)


def test_default_warn_and_exclude_preserves_missing_hour_audit() -> None:
    source = _spot_hours()
    missing_hour = source[:13] + source[14:]

    result = DailyCandleAggregator().aggregate_spot(missing_hour)

    assert result.candles == ()
    assert result.integrity.incomplete_day_count == 1
    assert result.integrity.excluded_day_count == 1
    assert result.integrity.warnings
    audit = result.audits[0]
    assert audit.action is DailyAggregationAction.EXCLUDED
    assert audit.missing_open_times == (datetime(2023, 3, 24, 13, tzinfo=UTC),)
    assert "MISSING_HOURLY_CANDLES" in audit.issues


def test_allow_documented_includes_only_an_explicit_open_daily_candle() -> None:
    source = _spot_hours()
    missing_hour = source[:13] + source[14:]
    aggregator = DailyCandleAggregator(policy=IncompleteDayPolicy.ALLOW_DOCUMENTED)

    result = aggregator.aggregate_spot(
        missing_hour,
        documented_incomplete_days=frozenset({datetime(2023, 3, 24).date()}),
    )

    assert len(result.candles) == 1
    assert not result.candles[0].is_closed
    assert (
        result.audits[0].action
        is DailyAggregationAction.INCLUDED_DOCUMENTED_INCOMPLETE
    )
    assert result.integrity.documented_incomplete_day_count == 1
    assert result.integrity.excluded_day_count == 0


def test_allow_documented_rejects_undocumented_incomplete_day() -> None:
    source = _spot_hours()
    missing_hour = source[:13] + source[14:]

    with pytest.raises(DailyAggregationError, match="explicitly documented"):
        DailyCandleAggregator(
            policy=IncompleteDayPolicy.ALLOW_DOCUMENTED
        ).aggregate_spot(missing_hour)


def test_open_hour_makes_day_incomplete_without_being_fabricated() -> None:
    source = _spot_hours()
    with_open_hour = (
        *source[:8],
        replace(source[8], is_closed=False),
        *source[9:],
    )

    result = DailyCandleAggregator().aggregate_spot(with_open_hour)

    assert result.candles == ()
    assert result.audits[0].open_source_candle_count == 1
    assert "OPEN_HOURLY_CANDLES" in result.audits[0].issues
