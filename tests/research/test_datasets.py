from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trader.research.datasets import DatasetValidationError, validate_dataset
from adaptive_trader.research.models import GapPolicy


def test_dataset_hash_is_deterministic_and_changes_with_candle(daily_candles) -> None:
    first = validate_dataset(daily_candles, created_at=datetime(2026, 2, 1, tzinfo=UTC))
    second = validate_dataset(daily_candles, created_at=datetime(2027, 2, 1, tzinfo=UTC))
    changed = validate_dataset(
        (*daily_candles[:-1], replace(daily_candles[-1], close=Decimal("112")))
    )

    assert first.content_hash == second.content_hash
    assert first.content_hash != changed.content_hash


def test_dataset_rejects_duplicates_open_and_mixed_data(daily_candles) -> None:
    with pytest.raises(DatasetValidationError):
        validate_dataset((daily_candles[0], daily_candles[0]))
    with pytest.raises(DatasetValidationError):
        validate_dataset((replace(daily_candles[0], is_closed=False),))
    with pytest.raises(DatasetValidationError):
        validate_dataset((daily_candles[0], replace(daily_candles[1], symbol="BTCUSDT")))


def test_gap_policy_warn_fail_and_allow(daily_candles) -> None:
    gapped = (daily_candles[0], daily_candles[2])

    warned = validate_dataset(gapped, gap_policy=GapPolicy.WARN)
    allowed = validate_dataset(gapped, gap_policy=GapPolicy.ALLOW)

    assert warned.gap_count == 1
    assert warned.missing_candle_count == 1
    assert allowed.content_hash == warned.content_hash
    with pytest.raises(DatasetValidationError):
        validate_dataset(gapped, gap_policy=GapPolicy.FAIL)


def test_dataset_rejects_out_of_order(daily_candles) -> None:
    with pytest.raises(DatasetValidationError):
        validate_dataset((daily_candles[1], daily_candles[0]))


def test_unknown_interval_does_not_invent_gap_logic(daily_candles) -> None:
    first = replace(daily_candles[0], interval="custom")
    second = replace(
        daily_candles[1],
        interval="custom",
        timestamp=first.timestamp + timedelta(days=2),
    )

    dataset = validate_dataset((first, second))

    assert dataset.gap_count == 0
