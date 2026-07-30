"""Immutable dataset validation and deterministic hashing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from adaptive_trader.domain.models import Candle
from adaptive_trader.research.models import (
    DatasetSegment,
    GapPolicy,
    ResearchDataset,
    TemporalSplit,
)


class DatasetValidationError(ValueError):
    """Raised when a research dataset violates a temporal invariant."""


_INTERVALS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


def _canonical(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _canonical(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        _canonical(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_candle(candle: Candle) -> dict[str, object]:
    return {
        "exchange": candle.exchange,
        "symbol": candle.symbol,
        "interval": candle.interval,
        "open_time": candle.open_time.astimezone(UTC).isoformat(),
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
        "is_closed": candle.is_closed,
    }


def candles_hash(candles: tuple[Candle, ...]) -> str:
    return canonical_hash([canonical_candle(candle) for candle in candles])


def validate_dataset(
    candles: tuple[Candle, ...],
    *,
    source: str = "unknown",
    gap_policy: GapPolicy = GapPolicy.WARN,
    created_at: datetime | None = None,
) -> ResearchDataset:
    if not candles:
        raise DatasetValidationError("research dataset requires candles")
    first = candles[0]
    seen: set[datetime] = set()
    duplicate_count = 0
    gap_count = 0
    missing_count = 0
    warnings: list[str] = []
    expected_delta = _INTERVALS.get(first.interval)
    previous: Candle | None = None
    for candle in candles:
        if candle.open_time in seen:
            duplicate_count += 1
        seen.add(candle.open_time)
        if not candle.is_closed:
            raise DatasetValidationError("research dataset rejects open candles")
        if candle.exchange != first.exchange:
            raise DatasetValidationError("research dataset rejects mixed exchanges")
        if candle.symbol != first.symbol:
            raise DatasetValidationError("research dataset rejects mixed symbols")
        if candle.interval != first.interval:
            raise DatasetValidationError("research dataset rejects mixed intervals")
        if previous is not None:
            delta = candle.open_time - previous.open_time
            if delta <= timedelta(0):
                if delta == timedelta(0):
                    raise DatasetValidationError("research dataset rejects duplicate candles")
                raise DatasetValidationError("research dataset must be chronological")
            if expected_delta is not None and delta > expected_delta:
                gap_count += 1
                missing_count += max(0, delta // expected_delta - 1)
        previous = candle
    if duplicate_count:
        raise DatasetValidationError("research dataset rejects duplicate candles")
    if gap_count:
        warnings.append(f"GAPS_DETECTED: count={gap_count} missing={missing_count}")
        if gap_policy is GapPolicy.FAIL:
            raise DatasetValidationError(warnings[-1])
        if gap_policy is GapPolicy.WARN:
            warnings.append("gap policy WARN accepted missing candles without filling")
    content_hash = candles_hash(candles)
    created = created_at or datetime.now(tz=UTC)
    last = candles[-1]
    last_close = last.close_time or last.open_time
    return ResearchDataset(
        dataset_id=f"{first.exchange}-{first.symbol}-{first.interval}-{content_hash[:16]}",
        exchange=first.exchange,
        symbol=first.symbol,
        interval=first.interval,
        start_time=first.open_time,
        end_time=last_close,
        candle_count=len(candles),
        first_open_time=first.open_time,
        last_close_time=last_close,
        source=source,
        created_at=created,
        content_hash=content_hash,
        missing_candle_count=missing_count,
        duplicate_count=duplicate_count,
        gap_count=gap_count,
        warnings=tuple(warnings),
        candles=candles,
    )


def _segment(
    dataset: ResearchDataset,
    *,
    name: str,
    evaluation_start: datetime,
    evaluation_end: datetime,
    warmup_candles: int,
) -> DatasetSegment:
    requested_evaluation = tuple(
        candle
        for candle in dataset.candles
        if evaluation_start <= candle.open_time < evaluation_end
    )
    if not requested_evaluation:
        raise DatasetValidationError(f"segment {name} has no evaluation candles")
    first_index = dataset.candles.index(requested_evaluation[0])
    last_index = dataset.candles.index(requested_evaluation[-1])
    warmup_start_index = max(0, first_index - warmup_candles)
    selected = dataset.candles[warmup_start_index : last_index + 1]
    available_warmup = first_index - warmup_start_index
    effective_index = first_index + max(0, warmup_candles - available_warmup)
    if effective_index > last_index:
        raise DatasetValidationError(
            f"segment {name} has no candles after the required indicator warmup"
        )
    evaluation = dataset.candles[effective_index : last_index + 1]
    effective_start = evaluation[0].open_time
    warnings: tuple[str, ...] = ()
    if effective_index > first_index:
        warnings = (
            "WARMUP_REDUCED_EVALUATION_PERIOD: "
            f"requested={requested_evaluation[0].open_time.isoformat()} "
            f"effective={effective_start.isoformat()}",
        )
    segment_hash = canonical_hash(
        {
            "candles": [canonical_candle(candle) for candle in selected],
            "requested_evaluation_start_time": requested_evaluation[0].open_time,
            "effective_evaluation_start_time": effective_start,
            "warmup_candle_count": effective_index - warmup_start_index,
            "evaluated_candle_count": len(evaluation),
        }
    )
    return DatasetSegment(
        name=name,
        start_time=effective_start,
        end_time=evaluation[-1].close_time or evaluation[-1].open_time,
        candle_count=len(evaluation),
        content_hash=segment_hash,
        warmup_start_time=selected[0].open_time,
        evaluation_start_time=effective_start,
        requested_evaluation_start_time=requested_evaluation[0].open_time,
        effective_evaluation_start_time=effective_start,
        candles=selected,
        evaluation_candles=evaluation,
        warnings=warnings,
    )


def holdout_split(
    dataset: ResearchDataset,
    *,
    train_percent: Decimal,
    validation_percent: Decimal,
    test_percent: Decimal,
    warmup_candles: int,
) -> TemporalSplit:
    percentages = (train_percent, validation_percent, test_percent)
    if any(value <= 0 for value in percentages) or sum(percentages, Decimal("0")) != Decimal("100"):
        raise DatasetValidationError("holdout percentages must be positive and sum to 100")
    if warmup_candles < 0:
        raise DatasetValidationError("warmup_candles must not be negative")
    total = len(dataset.candles)
    train_end = total * int(train_percent) // 100
    validation_end = total * int(train_percent + validation_percent) // 100
    if min(train_end, validation_end - train_end, total - validation_end) < 1:
        raise DatasetValidationError("holdout segments require at least one candle each")
    if warmup_candles >= train_end:
        raise DatasetValidationError("holdout training window is smaller than warmup")
    boundaries = [
        dataset.candles[0].open_time,
        dataset.candles[train_end].open_time,
        dataset.candles[validation_end].open_time,
    ]
    final_end = dataset.end_time + _INTERVALS.get(dataset.interval, timedelta(0))
    return TemporalSplit(
        split_id=f"holdout-{train_percent}-{validation_percent}-{test_percent}",
        train=_segment(
            dataset,
            name="train",
            evaluation_start=boundaries[0],
            evaluation_end=boundaries[1],
            warmup_candles=warmup_candles,
        ),
        validation=_segment(
            dataset,
            name="validation",
            evaluation_start=boundaries[1],
            evaluation_end=boundaries[2],
            warmup_candles=warmup_candles,
        ),
        test=_segment(
            dataset,
            name="test",
            evaluation_start=boundaries[2],
            evaluation_end=final_end,
            warmup_candles=warmup_candles,
        ),
    )


def explicit_split(
    dataset: ResearchDataset,
    *,
    train_start: datetime,
    train_end: datetime,
    validation_start: datetime,
    validation_end: datetime,
    test_start: datetime,
    test_end: datetime,
    warmup_candles: int,
) -> TemporalSplit:
    ranges = (
        (train_start, train_end),
        (validation_start, validation_end),
        (test_start, test_end),
    )
    if any(start.tzinfo is None or end.tzinfo is None for start, end in ranges):
        raise DatasetValidationError("explicit split dates must be timezone-aware")
    if not (
        train_start < train_end <= validation_start < validation_end <= test_start < test_end
    ):
        raise DatasetValidationError(
            "explicit split dates must be chronological and non-overlapping"
        )
    return TemporalSplit(
        split_id="explicit",
        train=_segment(
            dataset,
            name="train",
            evaluation_start=train_start,
            evaluation_end=train_end,
            warmup_candles=warmup_candles,
        ),
        validation=_segment(
            dataset,
            name="validation",
            evaluation_start=validation_start,
            evaluation_end=validation_end,
            warmup_candles=warmup_candles,
        ),
        test=_segment(
            dataset,
            name="test",
            evaluation_start=test_start,
            evaluation_end=test_end,
            warmup_candles=warmup_candles,
        ),
    )


def dataset_to_dict(dataset: ResearchDataset) -> dict[str, Any]:
    return {
        "dataset_id": dataset.dataset_id,
        "exchange": dataset.exchange,
        "symbol": dataset.symbol,
        "interval": dataset.interval,
        "start_time": dataset.start_time.isoformat(),
        "end_time": dataset.end_time.isoformat(),
        "candle_count": dataset.candle_count,
        "first_open_time": dataset.first_open_time.isoformat(),
        "last_close_time": dataset.last_close_time.isoformat(),
        "source": dataset.source,
        "created_at": dataset.created_at.isoformat(),
        "content_hash": dataset.content_hash,
        "missing_candle_count": dataset.missing_candle_count,
        "duplicate_count": dataset.duplicate_count,
        "gap_count": dataset.gap_count,
        "warnings": list(dataset.warnings),
    }
