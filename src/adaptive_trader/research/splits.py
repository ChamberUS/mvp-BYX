"""Strictly chronological walk-forward plans."""

from __future__ import annotations

from datetime import timedelta

from adaptive_trader.research.datasets import DatasetValidationError, _segment
from adaptive_trader.research.models import (
    DatasetSegment,
    ResearchDataset,
    WalkForwardFold,
    WalkForwardMode,
    WalkForwardPlan,
)


def build_walk_forward_plan(
    dataset: ResearchDataset,
    *,
    train_days: int,
    validation_days: int,
    step_days: int,
    warmup_candles: int,
    mode: WalkForwardMode = WalkForwardMode.ROLLING,
) -> WalkForwardPlan:
    if min(train_days, validation_days, step_days) < 1:
        raise DatasetValidationError("walk-forward windows and step must be positive")
    if warmup_candles < 0:
        raise DatasetValidationError("warmup_candles must not be negative")
    if warmup_candles >= len(dataset.candles):
        raise DatasetValidationError("walk-forward window is smaller than warmup")
    first = dataset.start_time
    final = dataset.end_time + timedelta(microseconds=1)
    folds: list[WalkForwardFold] = []
    fold_number = 1
    validation_start = first + timedelta(days=train_days)
    while validation_start < final:
        validation_end = validation_start + timedelta(days=validation_days)
        if validation_end > final:
            break
        train_start = first
        if mode is WalkForwardMode.ROLLING:
            train_start = validation_start - timedelta(days=train_days)
        train = _segment(
            dataset,
            name=f"fold-{fold_number}-train",
            evaluation_start=train_start,
            evaluation_end=validation_start,
            warmup_candles=0,
        )
        validation = _segment(
            dataset,
            name=f"fold-{fold_number}-validation",
            evaluation_start=validation_start,
            evaluation_end=validation_end,
            warmup_candles=warmup_candles,
        )
        folds.append(
            WalkForwardFold(
                fold_id=f"fold-{fold_number}",
                train=train,
                validation=validation,
            )
        )
        fold_number += 1
        validation_start += timedelta(days=step_days)
    if not folds:
        raise DatasetValidationError("dataset is too short for the walk-forward plan")
    return WalkForwardPlan(
        plan_id=f"{mode.value.lower()}-{train_days}d-{validation_days}d-{step_days}d",
        mode=mode,
        folds=tuple(folds),
        train_days=train_days,
        validation_days=validation_days,
        step_days=step_days,
        warmup_candles=warmup_candles,
    )


def segment_to_dict(segment: DatasetSegment) -> dict[str, object]:
    return {
        "name": segment.name,
        "start_time": segment.start_time.isoformat(),
        "end_time": segment.end_time.isoformat(),
        "candle_count": segment.candle_count,
        "content_hash": segment.content_hash,
        "warmup_start_time": segment.warmup_start_time.isoformat(),
        "evaluation_start_time": segment.evaluation_start_time.isoformat(),
        "warmup_candle_count": segment.warmup_candle_count,
    }
