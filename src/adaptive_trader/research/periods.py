"""Temporal boundaries that protect already-consumed research data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


class ConsumedTestError(ValueError):
    """Raised when a diagnostic or selector tries to use consumed test data."""


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ResearchPeriods:
    development_start: datetime
    development_end: datetime
    validation_start: datetime
    validation_end: datetime
    consumed_test_start: datetime
    consumed_test_end: datetime
    future_holdout_start: datetime | None = None
    future_holdout_end: datetime | None = None

    def __post_init__(self) -> None:
        names = (
            "development_start",
            "development_end",
            "validation_start",
            "validation_end",
            "consumed_test_start",
            "consumed_test_end",
        )
        values = tuple(_aware(getattr(self, name), name) for name in names)
        if values != tuple(getattr(self, name) for name in names):
            for name, value in zip(names, values, strict=True):
                object.__setattr__(self, name, value)
        if not (
            self.development_start <= self.development_end < self.validation_start
            and self.validation_start <= self.validation_end < self.consumed_test_start
            and self.consumed_test_start <= self.consumed_test_end
        ):
            raise ValueError("research periods must be chronological and non-overlapping")
        if (self.future_holdout_start is None) != (self.future_holdout_end is None):
            raise ValueError("future holdout requires both start and end")
        if self.future_holdout_start is not None and self.future_holdout_end is not None:
            future_start = _aware(self.future_holdout_start, "future_holdout_start")
            future_end = _aware(self.future_holdout_end, "future_holdout_end")
            object.__setattr__(self, "future_holdout_start", future_start)
            object.__setattr__(self, "future_holdout_end", future_end)
            if future_start <= self.consumed_test_end or future_start > future_end:
                raise ValueError("future holdout must follow consumed test and be ordered")

    def overlaps_consumed(self, start: datetime, end: datetime) -> bool:
        normalized_start = _aware(start, "start")
        normalized_end = _aware(end, "end")
        if normalized_end < normalized_start:
            raise ValueError("end must not precede start")
        return (
            normalized_start <= self.consumed_test_end
            and normalized_end >= self.consumed_test_start
        )

    def assert_not_consumed(self, start: datetime, end: datetime, operation: str) -> None:
        if self.overlaps_consumed(start, end):
            raise ConsumedTestError(
                f"{operation} cannot use consumed_test_period "
                f"({self.consumed_test_start.isoformat()} -> {self.consumed_test_end.isoformat()})"
            )

    def as_dict(self) -> dict[str, str | None]:
        return {
            "development_start": self.development_start.isoformat(),
            "development_end": self.development_end.isoformat(),
            "validation_start": self.validation_start.isoformat(),
            "validation_end": self.validation_end.isoformat(),
            "consumed_test_start": self.consumed_test_start.isoformat(),
            "consumed_test_end": self.consumed_test_end.isoformat(),
            "future_holdout_start": (
                self.future_holdout_start.isoformat() if self.future_holdout_start else None
            ),
            "future_holdout_end": (
                self.future_holdout_end.isoformat() if self.future_holdout_end else None
            ),
        }


def filter_excluded_period(
    candles: tuple[object, ...],
    *,
    exclude_start: datetime,
    exclude_end: datetime,
) -> tuple[object, ...]:
    """Filter a dataset without mutating it; callers must validate the boundaries first."""

    start = _aware(exclude_start, "exclude_start")
    end = _aware(exclude_end, "exclude_end")
    if end < start:
        raise ValueError("exclude_end must not precede exclude_start")
    result: list[object] = []
    for item in candles:
        timestamp = getattr(item, "open_time", None)
        if not isinstance(timestamp, datetime):
            raise TypeError("excluded-period filtering requires candle-like objects")
        if not start <= timestamp.astimezone(UTC) <= end:
            result.append(item)
    return tuple(result)
