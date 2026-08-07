"""Bounded-recorder runtime and local processing latency diagnostics."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RecorderRuntimeHealth:
    queue_depth: int
    queue_high_watermark: int
    queue_capacity: int
    maximum_processing_backlog: int
    events_received: int
    events_processed: int
    events_pending: int
    dropped_events: int
    processing_lag_ms: Decimal
    persistence_lag_ms: Decimal
    loop_stall_count: int
    loop_stall_max_ms: Decimal
    backlog_unrecovered: bool
    status: str
    threshold_source: str = "ENGINEERING_ASSUMPTION"


class RecorderRuntimeMonitor:
    """Measure one bounded recorder pipeline without blocking the hot path."""

    def __init__(
        self,
        *,
        queue_capacity: int = 100_000,
        maximum_processing_backlog: int = 5_000,
        loop_stall_threshold_ms: int = 250,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue capacity must be positive")
        if not 0 < maximum_processing_backlog < queue_capacity:
            raise ValueError("processing backlog threshold must be within queue capacity")
        if loop_stall_threshold_ms <= 0:
            raise ValueError("loop stall threshold must be positive")
        self.queue_capacity = queue_capacity
        self.maximum_processing_backlog = maximum_processing_backlog
        self.loop_stall_threshold_ms = loop_stall_threshold_ms
        self.events_received = 0
        self.events_processed = 0
        self.dropped_events = 0
        self.queue_depth = 0
        self.queue_high_watermark = 0
        self.loop_stall_count = 0
        self.loop_stall_max_ms = Decimal(0)
        self._last_loop_observation_ns: int | None = None
        self._latencies: dict[str, list[Decimal]] = {
            "parsing_latency_ms": [],
            "local_book_update_latency_ms": [],
            "enqueue_latency_ms": [],
            "persistence_lag_ms": [],
            "total_local_processing_latency_ms": [],
        }

    def received(
        self,
        *,
        receive_monotonic_ns: int,
        parsing_completed_ns: int,
        persistence_queued_ns: int,
        queue_depth: int,
    ) -> None:
        self.events_received += 1
        self.observe_queue(queue_depth)
        self._record(
            "parsing_latency_ms", parsing_completed_ns - receive_monotonic_ns
        )
        self._record(
            "enqueue_latency_ms", persistence_queued_ns - parsing_completed_ns
        )

    def processed(
        self,
        *,
        receive_monotonic_ns: int,
        processing_started_ns: int,
        book_update_completed_ns: int,
        persistence_started_ns: int,
        persistence_completed_ns: int,
        queue_depth: int,
    ) -> None:
        self.events_processed += 1
        self.observe_queue(queue_depth)
        self._record(
            "local_book_update_latency_ms",
            book_update_completed_ns - processing_started_ns,
        )
        self._record(
            "persistence_lag_ms", persistence_completed_ns - persistence_started_ns
        )
        self._record(
            "total_local_processing_latency_ms",
            persistence_completed_ns - receive_monotonic_ns,
        )

    def dropped(self, count: int = 1) -> None:
        if count <= 0:
            raise ValueError("dropped event count must be positive")
        self.dropped_events += count

    def observe_queue(self, queue_depth: int) -> None:
        if not 0 <= queue_depth <= self.queue_capacity:
            raise ValueError("queue depth must be inside configured capacity")
        self.queue_depth = queue_depth
        self.queue_high_watermark = max(self.queue_high_watermark, queue_depth)

    def observe_loop(self, now_ns: int, expected_interval_ms: int = 200) -> None:
        if now_ns < 0 or expected_interval_ms <= 0:
            raise ValueError("loop timing values must be positive")
        if self._last_loop_observation_ns is not None:
            elapsed_ms = _milliseconds(now_ns - self._last_loop_observation_ns)
            excess_ms = elapsed_ms - Decimal(expected_interval_ms)
            if excess_ms > Decimal(self.loop_stall_threshold_ms):
                self.loop_stall_count += 1
                self.loop_stall_max_ms = max(self.loop_stall_max_ms, excess_ms)
        self._last_loop_observation_ns = now_ns

    def summary(self) -> RecorderRuntimeHealth:
        pending = max(0, self.events_received - self.events_processed)
        backlog_unrecovered = (
            pending > self.maximum_processing_backlog
            or self.queue_depth > self.maximum_processing_backlog
        )
        total = self._latencies["total_local_processing_latency_ms"]
        persistence = self._latencies["persistence_lag_ms"]
        status = (
            "NOT_READY"
            if self.dropped_events
            else "DEGRADED"
            if backlog_unrecovered
            else "READY"
        )
        return RecorderRuntimeHealth(
            queue_depth=self.queue_depth,
            queue_high_watermark=self.queue_high_watermark,
            queue_capacity=self.queue_capacity,
            maximum_processing_backlog=self.maximum_processing_backlog,
            events_received=self.events_received,
            events_processed=self.events_processed,
            events_pending=pending,
            dropped_events=self.dropped_events,
            processing_lag_ms=total[-1] if total else Decimal(0),
            persistence_lag_ms=persistence[-1] if persistence else Decimal(0),
            loop_stall_count=self.loop_stall_count,
            loop_stall_max_ms=self.loop_stall_max_ms,
            backlog_unrecovered=backlog_unrecovered,
            status=status,
        )

    def summary_dict(self) -> dict[str, object]:
        return _plain_mapping(asdict(self.summary()))

    def latency_summary(self) -> dict[str, object]:
        return _plain_mapping({
            "measurement": (
                "local monotonic stage timestamps; separate from transport, strategy, "
                "and order latency"
            ),
            "fsync_per_event": False,
            "stages": {
                name: _distribution(values)
                for name, values in sorted(self._latencies.items())
            },
        })

    def has_processing_delay(self, receive_monotonic_ns: int, now_ns: int) -> bool:
        if now_ns < receive_monotonic_ns:
            return False
        lag_ms = _milliseconds(now_ns - receive_monotonic_ns)
        return (
            lag_ms > Decimal(self.loop_stall_threshold_ms)
            or self.queue_depth > self.maximum_processing_backlog
        )

    def _record(self, name: str, nanoseconds: int) -> None:
        if nanoseconds < 0:
            return
        self._latencies[name].append(_milliseconds(nanoseconds))


def _milliseconds(nanoseconds: int) -> Decimal:
    return Decimal(nanoseconds) / Decimal(1_000_000)


def _distribution(values: list[Decimal]) -> dict[str, object]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    mean = sum(ordered, Decimal(0)) / Decimal(len(ordered))
    variance = sum((item - mean) ** 2 for item in ordered) / Decimal(len(ordered))
    return {
        "count": len(ordered),
        "min_ms": ordered[0],
        "median_ms": _percentile(ordered, Decimal("50")),
        "p90_ms": _percentile(ordered, Decimal("90")),
        "p95_ms": _percentile(ordered, Decimal("95")),
        "p99_ms": _percentile(ordered, Decimal("99")),
        "p99_9_ms": _percentile(ordered, Decimal("99.9")),
        "max_ms": ordered[-1],
        "mean_ms": mean,
        "stddev_ms": Decimal(str(math.sqrt(float(variance)))),
    }


def _percentile(ordered: list[Decimal], percentile: Decimal) -> Decimal:
    if len(ordered) == 1:
        return ordered[0]
    position = (Decimal(len(ordered) - 1) * percentile) / Decimal(100)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def _plain_mapping(value: dict[str, object]) -> dict[str, object]:
    return {key: _plain_value(item) for key, item in value.items()}


def _plain_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    return value
