from datetime import UTC, datetime
from decimal import Decimal

import pytest

from adaptive_trader.research.datasets import (
    DatasetValidationError,
    explicit_split,
    holdout_split,
    validate_dataset,
)
from adaptive_trader.research.models import WalkForwardMode
from adaptive_trader.research.splits import build_walk_forward_plan


def test_holdout_is_chronological_and_warmup_is_before_evaluation(daily_candles) -> None:
    dataset = validate_dataset(daily_candles)

    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=2,
    )

    assert split.train.end_time <= split.validation.start_time
    assert split.validation.end_time <= split.test.start_time
    assert split.validation.warmup_candle_count == 2
    assert (
        split.validation.candles[-1].open_time
        == split.validation.evaluation_candles[-1].open_time
    )


def test_holdout_rejects_invalid_percentages_and_small_warmup(daily_candles) -> None:
    dataset = validate_dataset(daily_candles)
    with pytest.raises(DatasetValidationError):
        holdout_split(
            dataset,
            train_percent=Decimal("60"),
            validation_percent=Decimal("30"),
            test_percent=Decimal("30"),
            warmup_candles=1,
        )
    with pytest.raises(DatasetValidationError):
        holdout_split(
            dataset,
            train_percent=Decimal("10"),
            validation_percent=Decimal("45"),
            test_percent=Decimal("45"),
            warmup_candles=2,
        )


def test_explicit_dates_are_timezone_aware_and_ordered(daily_candles) -> None:
    dataset = validate_dataset(daily_candles)
    start = datetime(2026, 1, 1, tzinfo=UTC)

    split = explicit_split(
        dataset,
        train_start=start,
        train_end=datetime(2026, 1, 5, tzinfo=UTC),
        validation_start=datetime(2026, 1, 5, tzinfo=UTC),
        validation_end=datetime(2026, 1, 8, tzinfo=UTC),
        test_start=datetime(2026, 1, 8, tzinfo=UTC),
        test_end=datetime(2026, 1, 12, tzinfo=UTC),
        warmup_candles=1,
    )

    assert split.test.start_time == datetime(2026, 1, 8, tzinfo=UTC)


def test_walk_forward_rolling_and_expanding_have_no_future_fold(daily_candles) -> None:
    dataset = validate_dataset(daily_candles)
    rolling = build_walk_forward_plan(
        dataset,
        train_days=3,
        validation_days=2,
        step_days=2,
        warmup_candles=1,
        mode=WalkForwardMode.ROLLING,
    )
    expanding = build_walk_forward_plan(
        dataset,
        train_days=3,
        validation_days=2,
        step_days=2,
        warmup_candles=1,
        mode=WalkForwardMode.EXPANDING,
    )

    assert rolling.folds
    assert expanding.folds
    for fold in (*rolling.folds, *expanding.folds):
        assert fold.train.end_time <= fold.validation.start_time
        assert fold.validation.end_time <= dataset.end_time
