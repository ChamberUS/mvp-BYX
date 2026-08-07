from datetime import timedelta
from decimal import Decimal

from adaptive_trader.futures.integrity import inspect_funding
from adaptive_trader.futures.models import FundingRate


def test_funding_integrity_observes_rates_and_missing_windows(start_time) -> None:
    rates = (
        FundingRate("ETHUSDT", start_time, Decimal("0.0001")),
        FundingRate(
            "ETHUSDT",
            start_time + timedelta(hours=8),
            Decimal("-0.0002"),
        ),
        FundingRate(
            "ETHUSDT",
            start_time + timedelta(hours=24),
            Decimal("0"),
        ),
    )
    integrity = inspect_funding(
        rates,
        symbol="ETHUSDT",
        requested_start=start_time,
        requested_end=start_time + timedelta(hours=24),
    )
    assert integrity.positive_count == 1
    assert integrity.negative_count == 1
    assert integrity.zero_count == 1
    assert integrity.minimum_rate == Decimal("-0.0002")
    assert integrity.maximum_rate == Decimal("0.0001")
    assert integrity.largest_gap_seconds == 16 * 60 * 60
    assert integrity.missing_windows == 0
    assert len(integrity.content_hash) == 64


def test_empty_funding_is_explicitly_missing(start_time) -> None:
    integrity = inspect_funding(
        (),
        symbol="ETHUSDT",
        requested_start=start_time,
        requested_end=start_time + timedelta(days=1),
    )
    assert integrity.event_count == 0
    assert integrity.coverage_percent == 0
    assert "FUNDING_DATA_MISSING" in integrity.warnings
