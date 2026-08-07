"""Per-stream liveness and offline capture-readiness diagnostics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.models import (
    MicrostructureEvent,
    MicrostructureStreamType,
    OrderBookStatus,
)
from adaptive_trader.microstructure.order_book import LocalOrderBook
from adaptive_trader.microstructure.replay import MicrostructureReplayEngine
from adaptive_trader.microstructure.sequence import GapClassification
from adaptive_trader.microstructure.storage import inspect_session


class StreamLivenessState(StrEnum):
    REQUESTED = "REQUESTED"
    CONNECTED = "CONNECTED"
    WAITING_FIRST_EVENT = "WAITING_FIRST_EVENT"
    LIVE = "LIVE"
    STALE = "STALE"
    FAILED = "FAILED"


class FeedHealthStatus(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"


class CaptureQualityStatus(StrEnum):
    CAPTURE_VALID = "CAPTURE_VALID"
    CAPTURE_VALID_WITH_WARNINGS = "CAPTURE_VALID_WITH_WARNINGS"
    CAPTURE_INVALID = "CAPTURE_INVALID"


class SessionQualityStatus(StrEnum):
    CLEAN = "CLEAN"
    VALID_WITH_WARNINGS = "VALID_WITH_WARNINGS"
    INVALID = "INVALID"


class LongCaptureReadinessStatus(StrEnum):
    READY_FOR_LONG_CAPTURE = "READY_FOR_LONG_CAPTURE"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    NOT_READY_FOR_LONG_CAPTURE = "NOT_READY_FOR_LONG_CAPTURE"


class LivenessMode(StrEnum):
    PERIODIC = "PERIODIC"
    CHANGE_DRIVEN = "CHANGE_DRIVEN"
    EVENT_DRIVEN = "EVENT_DRIVEN"


class LivenessIncidentState(StrEnum):
    DETECTED = "DETECTED"
    OBSERVING = "OBSERVING"
    RECOVERED = "RECOVERED"
    UNRESOLVED = "UNRESOLVED"
    ESCALATED = "ESCALATED"


class LivenessIncidentClassification(StrEnum):
    TRUE_FEED_STALL = "TRUE_FEED_STALL"
    LOCAL_PROCESSING_DELAY = "LOCAL_PROCESSING_DELAY"
    NETWORK_JITTER = "NETWORK_JITTER"
    THRESHOLD_TOO_STRICT = "THRESHOLD_TOO_STRICT"
    NORMAL_NO_UPDATE = "NORMAL_NO_UPDATE"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class StreamLivenessConfig:
    stream: str
    expected_cadence_ms: int | None
    first_event_timeout_ms: int
    stale_after_ms: int
    unresolved_after_ms: int = 60_000
    mode: LivenessMode = LivenessMode.EVENT_DRIVEN
    maximum_recovered_incident_ms: int = 10_000
    source: str = "ENGINEERING_ASSUMPTION"

    def __post_init__(self) -> None:
        if (
            self.first_event_timeout_ms <= 0
            or self.stale_after_ms <= 0
            or self.unresolved_after_ms <= self.stale_after_ms
            or self.maximum_recovered_incident_ms <= 0
        ):
            raise ValueError("stream liveness timeouts must be positive")
        if self.expected_cadence_ms is not None and self.expected_cadence_ms <= 0:
            raise ValueError("expected stream cadence must be positive")


@dataclass(slots=True)
class _StreamLiveness:
    config: StreamLivenessConfig
    connection_id: str
    state: StreamLivenessState = StreamLivenessState.REQUESTED
    connected_at_ns: int | None = None
    first_event_ns: int | None = None
    last_event_ns: int | None = None
    last_event_wall: datetime | None = None
    event_count: int = 0
    stale_count: int = 0
    recovery_count: int = 0
    restart_count: int = 0
    last_connection_sequence: int | None = None
    failure_reason: str | None = None
    active_incident_id: str | None = None


@dataclass(slots=True)
class LivenessIncident:
    incident_id: str
    stream: str
    route: str
    connection_id: str
    state: LivenessIncidentState
    start_monotonic_ns: int
    start: datetime | None
    recovery_monotonic_ns: int | None = None
    recovery: datetime | None = None
    duration_ms: Decimal | None = None
    evidence: dict[str, object] | None = None
    classification: LivenessIncidentClassification = (
        LivenessIncidentClassification.INCONCLUSIVE
    )
    caused_gap: bool = False
    caused_resync: bool = False
    caused_book_invalid: bool = False
    unresolved: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "incident_id": self.incident_id,
            "stream": self.stream,
            "route": self.route,
            "connection_id": self.connection_id,
            "state": self.state.value,
            "start": self.start.isoformat() if self.start is not None else None,
            "start_monotonic_ns": self.start_monotonic_ns,
            "recovery": (
                self.recovery.isoformat() if self.recovery is not None else None
            ),
            "recovery_monotonic_ns": self.recovery_monotonic_ns,
            "duration_ms": str(self.duration_ms) if self.duration_ms is not None else None,
            "evidence": self.evidence or {},
            "classification": self.classification.value,
            "caused_gap": self.caused_gap,
            "caused_resync": self.caused_resync,
            "caused_book_invalid": self.caused_book_invalid,
            "unresolved": self.unresolved,
        }


def default_liveness_config(stream: str) -> StreamLivenessConfig:
    configs = {
        "depth": StreamLivenessConfig(
            "depth", 100, 10_000, 2_000, 10_000, LivenessMode.CHANGE_DRIVEN
        ),
        "depth@100ms": StreamLivenessConfig(
            "depth@100ms", 100, 10_000, 2_000, 10_000, LivenessMode.CHANGE_DRIVEN
        ),
        "markPrice": StreamLivenessConfig(
            "markPrice", 1_000, 5_000, 2_500, 5_000, LivenessMode.PERIODIC
        ),
        "markPrice@1s": StreamLivenessConfig(
            "markPrice@1s", 1_000, 5_000, 2_500, 5_000, LivenessMode.PERIODIC
        ),
        "aggTrade": StreamLivenessConfig(
            "aggTrade", None, 30_000, 30_000, 60_000, LivenessMode.EVENT_DRIVEN
        ),
        "bookTicker": StreamLivenessConfig(
            "bookTicker", None, 30_000, 10_000, 30_000, LivenessMode.CHANGE_DRIVEN
        ),
    }
    try:
        return configs[stream]
    except KeyError as exc:
        raise ValueError(f"unsupported liveness stream: {stream}") from exc


class StreamLivenessMonitor:
    def __init__(
        self,
        subscriptions: tuple[tuple[str, str], ...],
    ) -> None:
        if not subscriptions:
            raise ValueError("liveness monitor requires subscriptions")
        self._streams = {
            stream: _StreamLiveness(default_liveness_config(stream), connection_id)
            for stream, connection_id in subscriptions
        }
        if len(self._streams) != len(subscriptions):
            raise ValueError("duplicate liveness stream")
        self._incidents: dict[str, LivenessIncident] = {}
        self._connection_alive: dict[str, bool] = {
            connection_id: False for _, connection_id in subscriptions
        }
        self._last_heartbeat_ns: dict[str, int | None] = {
            connection_id: None for _, connection_id in subscriptions
        }

    def connected(self, connection_id: str, now_ns: int) -> None:
        _monotonic(now_ns)
        for current in self._streams.values():
            if current.connection_id != connection_id:
                continue
            current.state = StreamLivenessState.CONNECTED
            current.connected_at_ns = now_ns
            current.state = StreamLivenessState.WAITING_FIRST_EVENT
            current.failure_reason = None
            self._connection_alive[connection_id] = True

    def observed(
        self,
        stream: str,
        *,
        connection_id: str,
        connection_sequence: int,
        now_ns: int,
        now_wall: datetime | None = None,
        sequence_continuous: bool | None = None,
        caused_gap: bool = False,
        caused_resync: bool = False,
        caused_book_invalid: bool = False,
        local_processing_delay: bool = False,
        queue_depth: int = 0,
    ) -> None:
        _monotonic(now_ns)
        try:
            current = self._streams[stream]
        except KeyError as exc:
            raise ValueError(f"unrequested liveness stream: {stream}") from exc
        if current.connection_id != connection_id:
            raise ValueError("stream arrived on the wrong routed connection")
        if connection_sequence < 0:
            raise ValueError("connection sequence must be non-negative")
        if (
            current.last_connection_sequence is not None
            and connection_sequence <= current.last_connection_sequence
        ):
            current.state = StreamLivenessState.FAILED
            current.failure_reason = GapClassification.OUT_OF_ORDER_EVENT.value
            return
        if current.active_incident_id is not None:
            current.recovery_count += 1
            self._recover(
                current,
                now_ns=now_ns,
                now_wall=now_wall,
                sequence_continuous=sequence_continuous,
                caused_gap=caused_gap,
                caused_resync=caused_resync,
                caused_book_invalid=caused_book_invalid,
                local_processing_delay=local_processing_delay,
                queue_depth=queue_depth,
            )
        current.first_event_ns = current.first_event_ns or now_ns
        current.last_event_ns = now_ns
        current.last_event_wall = (
            now_wall.astimezone(UTC) if now_wall is not None else None
        )
        current.last_connection_sequence = connection_sequence
        current.event_count += 1
        current.state = StreamLivenessState.LIVE
        current.failure_reason = None

    def evaluate(self, now_ns: int, now_wall: datetime | None = None) -> None:
        _monotonic(now_ns)
        for current in self._streams.values():
            if (
                current.state is StreamLivenessState.WAITING_FIRST_EVENT
                and current.connected_at_ns is not None
                and _milliseconds(now_ns - current.connected_at_ns)
                > current.config.first_event_timeout_ms
            ):
                current.state = StreamLivenessState.FAILED
                current.failure_reason = "FIRST_EVENT_TIMEOUT"
            elif (
                current.state in {StreamLivenessState.LIVE, StreamLivenessState.STALE}
                and current.last_event_ns is not None
                and _milliseconds(now_ns - current.last_event_ns)
                > current.config.stale_after_ms
            ):
                if current.active_incident_id is None:
                    self._detect(current, now_ns, now_wall)
                else:
                    self._observe_or_escalate(current, now_ns)

    def connection_restarted(self, connection_id: str, now_ns: int) -> None:
        _monotonic(now_ns)
        for current in self._streams.values():
            if current.connection_id != connection_id:
                continue
            current.restart_count += 1
            current.state = StreamLivenessState.REQUESTED
            current.connected_at_ns = None
            current.last_connection_sequence = None
            current.failure_reason = GapClassification.CONNECTION_RESTART.value
            self._connection_alive[connection_id] = False
            if current.active_incident_id is not None:
                incident = self._incidents[current.active_incident_id]
                incident.state = LivenessIncidentState.ESCALATED
                incident.unresolved = True
                incident.evidence = {
                    **(incident.evidence or {}),
                    "connection_restarted": True,
                }

    def failed(self, stream: str, reason: str) -> None:
        current = self._streams[stream]
        current.state = StreamLivenessState.FAILED
        current.failure_reason = reason

    def heartbeat(self, connection_id: str, now_ns: int) -> None:
        _monotonic(now_ns)
        if connection_id not in self._connection_alive:
            raise ValueError("heartbeat belongs to an unknown connection")
        self._connection_alive[connection_id] = True
        self._last_heartbeat_ns[connection_id] = now_ns

    def finalize(self, now_ns: int, now_wall: datetime | None = None) -> None:
        _monotonic(now_ns)
        for current in self._streams.values():
            if current.active_incident_id is None:
                continue
            incident = self._incidents[current.active_incident_id]
            cross_activity = self._cross_activity(incident)
            connection_alive = self._connection_alive[current.connection_id]
            if (
                current.config.mode is not LivenessMode.PERIODIC
                and not cross_activity
                and connection_alive
            ):
                self._recover(
                    current,
                    now_ns=now_ns,
                    now_wall=now_wall,
                    sequence_continuous=None,
                    caused_gap=False,
                    caused_resync=False,
                    caused_book_invalid=False,
                    local_processing_delay=False,
                    queue_depth=0,
                    forced_classification=LivenessIncidentClassification.NORMAL_NO_UPDATE,
                )
                continue
            duration_ms = _decimal_milliseconds(
                now_ns - incident.start_monotonic_ns
            )
            if (
                connection_alive
                and duration_ms <= Decimal(current.config.unresolved_after_ms)
            ):
                self._recover(
                    current,
                    now_ns=now_ns,
                    now_wall=now_wall,
                    sequence_continuous=None,
                    caused_gap=False,
                    caused_resync=False,
                    caused_book_invalid=False,
                    local_processing_delay=False,
                    queue_depth=0,
                    forced_classification=(
                        LivenessIncidentClassification.INCONCLUSIVE
                    ),
                )
                continue
            incident.state = LivenessIncidentState.UNRESOLVED
            incident.unresolved = True
            incident.duration_ms = duration_ms
            incident.evidence = {
                **(incident.evidence or {}),
                "connection_alive_at_end": connection_alive,
                "cross_stream_activity": cross_activity,
            }
            current.state = StreamLivenessState.FAILED
            current.failure_reason = "UNRESOLVED_LIVENESS_INCIDENT"

    @property
    def incidents(self) -> tuple[LivenessIncident, ...]:
        return tuple(self._incidents[key] for key in sorted(self._incidents))

    def config_summary(self) -> dict[str, object]:
        return {
            stream: {
                "mode": current.config.mode.value,
                "expected_cadence_ms": current.config.expected_cadence_ms,
                "first_event_timeout_ms": current.config.first_event_timeout_ms,
                "silence_observation_ms": current.config.stale_after_ms,
                "unresolved_after_ms": current.config.unresolved_after_ms,
                "maximum_recovered_incident_ms": (
                    current.config.maximum_recovered_incident_ms
                ),
                "source": current.config.source,
                "update_speed_is_heartbeat": False,
            }
            for stream, current in sorted(self._streams.items())
        }

    def summary(self) -> dict[str, object]:
        return {
            stream: {
                "connection_id": current.connection_id,
                "state": current.state.value,
                "expected_cadence_ms": current.config.expected_cadence_ms,
                "first_event_timeout_ms": current.config.first_event_timeout_ms,
                "stale_after_ms": current.config.stale_after_ms,
                "mode": current.config.mode.value,
                "unresolved_after_ms": current.config.unresolved_after_ms,
                "event_count": current.event_count,
                "stale_count": current.stale_count,
                "recovery_count": current.recovery_count,
                "restart_count": current.restart_count,
                "failure_reason": current.failure_reason,
                "unresolved_incident_count": sum(
                    incident.unresolved
                    for incident in self._incidents.values()
                    if incident.stream == stream
                ),
            }
            for stream, current in sorted(self._streams.items())
        }

    def _detect(
        self,
        current: _StreamLiveness,
        now_ns: int,
        now_wall: datetime | None,
    ) -> None:
        incident_id = f"{current.config.stream}-{now_ns}"
        evidence: dict[str, object] = {
            "threshold_ms": current.config.stale_after_ms,
            "mode": current.config.mode.value,
            "event_counts_at_detection": {
                name: observed.event_count for name, observed in self._streams.items()
            },
            "connection_alive": self._connection_alive[current.connection_id],
            "last_heartbeat_monotonic_ns": self._last_heartbeat_ns[
                current.connection_id
            ],
        }
        self._incidents[incident_id] = LivenessIncident(
            incident_id=incident_id,
            stream=current.config.stream,
            route=_route_name(current.connection_id),
            connection_id=current.connection_id,
            state=LivenessIncidentState.DETECTED,
            start_monotonic_ns=current.last_event_ns or now_ns,
            start=current.last_event_wall
            or (now_wall.astimezone(UTC) if now_wall is not None else None),
            evidence=evidence,
        )
        current.active_incident_id = incident_id
        current.state = StreamLivenessState.STALE
        current.stale_count += 1
        current.failure_reason = GapClassification.STALE_EVENT.value

    def _observe_or_escalate(self, current: _StreamLiveness, now_ns: int) -> None:
        if current.active_incident_id is None:
            return
        incident = self._incidents[current.active_incident_id]
        duration_ms = _milliseconds(now_ns - incident.start_monotonic_ns)
        if incident.state is LivenessIncidentState.DETECTED:
            incident.state = LivenessIncidentState.OBSERVING
        if duration_ms <= current.config.unresolved_after_ms:
            return
        cross_activity = self._cross_activity(incident)
        should_escalate = (
            current.config.mode is LivenessMode.PERIODIC
            or cross_activity
            or not self._connection_alive[current.connection_id]
        )
        if should_escalate:
            incident.state = LivenessIncidentState.ESCALATED
            incident.unresolved = True
            incident.duration_ms = Decimal(str(duration_ms))

    def _recover(
        self,
        current: _StreamLiveness,
        *,
        now_ns: int,
        now_wall: datetime | None,
        sequence_continuous: bool | None,
        caused_gap: bool,
        caused_resync: bool,
        caused_book_invalid: bool,
        local_processing_delay: bool,
        queue_depth: int,
        forced_classification: LivenessIncidentClassification | None = None,
    ) -> None:
        if current.active_incident_id is None:
            return
        incident = self._incidents[current.active_incident_id]
        duration_ms = _decimal_milliseconds(now_ns - incident.start_monotonic_ns)
        cross_activity = self._cross_activity(incident)
        if forced_classification is not None:
            classification = forced_classification
        elif caused_gap or caused_resync or caused_book_invalid or sequence_continuous is False:
            classification = LivenessIncidentClassification.TRUE_FEED_STALL
        elif local_processing_delay:
            classification = LivenessIncidentClassification.LOCAL_PROCESSING_DELAY
        elif not cross_activity:
            classification = LivenessIncidentClassification.NORMAL_NO_UPDATE
        elif duration_ms <= Decimal(current.config.maximum_recovered_incident_ms):
            classification = LivenessIncidentClassification.THRESHOLD_TOO_STRICT
        else:
            classification = LivenessIncidentClassification.INCONCLUSIVE
        incident.state = LivenessIncidentState.RECOVERED
        incident.recovery_monotonic_ns = now_ns
        incident.recovery = now_wall.astimezone(UTC) if now_wall is not None else None
        incident.duration_ms = duration_ms
        incident.classification = classification
        incident.caused_gap = caused_gap
        incident.caused_resync = caused_resync
        incident.caused_book_invalid = caused_book_invalid
        incident.unresolved = False
        incident.evidence = {
            **(incident.evidence or {}),
            "event_counts_at_recovery": {
                name: observed.event_count for name, observed in self._streams.items()
            },
            "cross_stream_activity": cross_activity,
            "connection_alive_at_recovery": self._connection_alive[
                current.connection_id
            ],
            "sequence_continuous": sequence_continuous,
            "local_processing_delay": local_processing_delay,
            "queue_depth": queue_depth,
        }
        current.active_incident_id = None

    def _cross_activity(self, incident: LivenessIncident) -> bool:
        evidence = incident.evidence or {}
        baseline = evidence.get("event_counts_at_detection")
        if not isinstance(baseline, dict):
            return False
        relevant = {
            "depth": ("bookTicker", "aggTrade"),
            "depth@100ms": ("bookTicker", "aggTrade"),
            "bookTicker": ("depth", "aggTrade"),
            "aggTrade": ("bookTicker", "depth"),
            "markPrice": ("bookTicker", "depth", "aggTrade"),
            "markPrice@1s": ("bookTicker", "depth", "aggTrade"),
        }[incident.stream]
        return any(
            isinstance(baseline.get(key), int)
            and current.event_count > baseline[key]
            for name in relevant
            for key, current in self._streams.items()
            if key.split("@", 1)[0] == name
        )


@dataclass(frozen=True, slots=True)
class MicrostructureFeedHealth:
    status: FeedHealthStatus
    reasons: tuple[str, ...]
    market: str
    symbol: str
    required_streams: tuple[str, ...]
    delivered_streams: tuple[str, ...]
    missing_streams: tuple[str, ...]
    event_counts: dict[str, int]
    real_sequence_gaps: int
    parser_errors: int
    connection_restarts: int
    stale_incidents: int
    liveness_recoveries: int
    unresolved_incidents: int
    dropped_events: int
    queue_high_watermark: int
    events_pending: int
    final_order_book_status: str
    capture_duration_seconds: Decimal


@dataclass(frozen=True, slots=True)
class CaptureQualityScorecard:
    status: CaptureQualityStatus
    reasons: tuple[str, ...]
    hashes_valid: bool
    complete: bool
    all_required_streams_delivered: bool
    parser_clean: bool
    sequence_clean: bool
    book_synchronized: bool
    liveness_stable: bool


class FeedHealthAnalyzer:
    """Reconstruct a session without network access and decide data readiness."""

    _EVENT_NAMES = {
        MicrostructureStreamType.AGG_TRADE: "aggTrade",
        MicrostructureStreamType.BOOK_TICKER: "bookTicker",
        MicrostructureStreamType.DEPTH_UPDATE: "depth",
        MicrostructureStreamType.MARK_PRICE: "markPrice",
    }

    def analyze(
        self,
        session_path: Path,
    ) -> tuple[MicrostructureFeedHealth, CaptureQualityScorecard, dict[str, object]]:
        manifest = inspect_session(session_path)
        market = MarketType(_required_string(manifest, "market"))
        symbol = _required_string(manifest, "symbol")
        events = MicrostructureReplayEngine(seed=42).load_events(session_path)
        counts: Counter[str] = Counter()
        restart_count = 0
        parser_errors = _non_negative_int(manifest.get("parser_errors", 0), "parser_errors")
        first_ns: int | None = None
        last_ns: int | None = None
        for event in events:
            stream = self._EVENT_NAMES.get(event.stream_type)
            if stream is not None:
                counts[stream] += 1
            if (
                event.stream_type is MicrostructureStreamType.CONNECTION_STATE
                and event.connection_state == "DISCONNECTED"
            ):
                restart_count += 1
            first_ns = event.receive_monotonic_ns if first_ns is None else min(
                first_ns, event.receive_monotonic_ns
            )
            last_ns = event.receive_monotonic_ns if last_ns is None else max(
                last_ns, event.receive_monotonic_ns
            )
        required = (
            ("aggTrade", "bookTicker", "depth", "markPrice")
            if market is MarketType.USD_M_FUTURES
            else ("aggTrade", "bookTicker", "depth")
        )
        delivered = tuple(item for item in required if counts[item] > 0)
        missing = tuple(item for item in required if counts[item] == 0)
        book, classifications = self._reconstruct(events, market, symbol)
        real_gaps = classifications[GapClassification.REAL_SEQUENCE_GAP.value]
        (
            stale_incidents,
            liveness_recoveries,
            unresolved_incidents,
            liveness_failures,
        ) = self._liveness(
            manifest,
            required,
        )
        runtime = _runtime_health(manifest)
        dropped_events = _non_negative_int(
            runtime.get("dropped_events", 0), "dropped_events"
        )
        queue_high_watermark = _non_negative_int(
            runtime.get("queue_high_watermark", 0), "queue_high_watermark"
        )
        events_pending = _non_negative_int(
            runtime.get("events_pending", 0), "events_pending"
        )
        backlog_unrecovered = bool(runtime.get("backlog_unrecovered", False))
        classifications[GapClassification.STALE_EVENT.value] += stale_incidents
        classifications[GapClassification.CONNECTION_RESTART.value] += restart_count
        classifications[GapClassification.PARSER_ERROR.value] += parser_errors
        duration = (
            Decimal(last_ns - first_ns) / Decimal("1000000000")
            if first_ns is not None and last_ns is not None
            else Decimal("0")
        )
        reasons: list[str] = []
        if manifest.get("hashes_valid") is not True:
            reasons.append("SESSION_HASH_MISMATCH")
        if manifest.get("completeness") != "COMPLETE":
            reasons.append("CAPTURE_INCOMPLETE")
        if missing:
            reasons.append(f"MISSING_STREAMS:{','.join(missing)}")
        if parser_errors:
            reasons.append(f"PARSER_ERRORS:{parser_errors}")
        if book.status is not OrderBookStatus.SYNCHRONIZED:
            reasons.append(f"ORDER_BOOK_{book.status.value}")
        if liveness_failures:
            reasons.append(f"LIVENESS_NOT_LIVE:{','.join(liveness_failures)}")
        if unresolved_incidents:
            reasons.append(f"UNRESOLVED_LIVENESS_INCIDENTS:{unresolved_incidents}")
        if dropped_events:
            reasons.append(f"DROPPED_EVENTS:{dropped_events}")
        if events_pending or backlog_unrecovered:
            reasons.append(f"RUNTIME_BACKLOG_UNRECOVERED:{events_pending}")
        if real_gaps:
            reasons.append(f"REAL_SEQUENCE_GAPS:{real_gaps}")
        hard_failure = bool(reasons)
        if hard_failure:
            status = FeedHealthStatus.NOT_READY
        elif restart_count:
            status = FeedHealthStatus.DEGRADED
            if restart_count:
                reasons.append(f"CONNECTION_RESTARTS:{restart_count}")
        else:
            status = FeedHealthStatus.READY
            reasons.append("ALL_REQUIRED_PUBLIC_STREAMS_HEALTHY")
        health = MicrostructureFeedHealth(
            status=status,
            reasons=tuple(reasons),
            market=market.value,
            symbol=symbol,
            required_streams=required,
            delivered_streams=delivered,
            missing_streams=missing,
            event_counts=dict(sorted(counts.items())),
            real_sequence_gaps=real_gaps,
            parser_errors=parser_errors,
            connection_restarts=restart_count,
            stale_incidents=stale_incidents,
            liveness_recoveries=liveness_recoveries,
            unresolved_incidents=unresolved_incidents,
            dropped_events=dropped_events,
            queue_high_watermark=queue_high_watermark,
            events_pending=events_pending,
            final_order_book_status=book.status.value,
            capture_duration_seconds=duration,
        )
        warning_count = stale_incidents + liveness_recoveries + restart_count
        quality_status = (
            CaptureQualityStatus.CAPTURE_INVALID
            if status is FeedHealthStatus.NOT_READY
            else (
                CaptureQualityStatus.CAPTURE_VALID_WITH_WARNINGS
                if warning_count
                else CaptureQualityStatus.CAPTURE_VALID
            )
        )
        session_quality = {
            CaptureQualityStatus.CAPTURE_VALID: SessionQualityStatus.CLEAN,
            CaptureQualityStatus.CAPTURE_VALID_WITH_WARNINGS: (
                SessionQualityStatus.VALID_WITH_WARNINGS
            ),
            CaptureQualityStatus.CAPTURE_INVALID: SessionQualityStatus.INVALID,
        }[quality_status]
        scorecard = CaptureQualityScorecard(
            status=quality_status,
            reasons=tuple(reasons),
            hashes_valid=manifest.get("hashes_valid") is True,
            complete=manifest.get("completeness") == "COMPLETE",
            all_required_streams_delivered=not missing,
            parser_clean=parser_errors == 0,
            sequence_clean=real_gaps == 0,
            book_synchronized=book.status is OrderBookStatus.SYNCHRONIZED,
            liveness_stable=(
                unresolved_incidents == 0 and not liveness_failures
            ),
        )
        details = {
            "gap_classification": classifications,
            "manifest": manifest,
            "event_count": len(events),
            "current_health": status.value,
            "session_quality": session_quality.value,
            "session_warning_count": warning_count,
            "runtime_health": runtime,
        }
        return health, scorecard, details

    @staticmethod
    def _liveness(
        manifest: dict[str, object],
        required_streams: tuple[str, ...],
    ) -> tuple[int, int, int, tuple[str, ...]]:
        raw = manifest.get("stream_liveness")
        subscriptions = manifest.get("subscription_manifest")
        if not isinstance(raw, dict) or not isinstance(subscriptions, list):
            return 0, 0, 0, ()
        if not subscriptions:
            return 0, 0, 0, ()
        stale = 0
        recoveries = 0
        unresolved = 0
        failures: list[str] = []
        for stream in required_streams:
            current = raw.get(stream)
            if not isinstance(current, dict):
                failures.append(f"{stream}=MISSING")
                continue
            state = current.get("state")
            if state != StreamLivenessState.LIVE.value:
                failures.append(f"{stream}={state}")
            stale += _non_negative_int(current.get("stale_count", 0), "stale_count")
            recoveries += _non_negative_int(
                current.get("recovery_count", 0),
                "recovery_count",
            )
            unresolved += _non_negative_int(
                current.get("unresolved_incident_count", 0),
                "unresolved_incident_count",
            )
        return stale, recoveries, unresolved, tuple(failures)

    @staticmethod
    def _reconstruct(
        events: tuple[MicrostructureEvent, ...],
        market: MarketType,
        symbol: str,
    ) -> tuple[LocalOrderBook, dict[str, int]]:
        book = LocalOrderBook(market, symbol)
        counts: Counter[str] = Counter()
        for event in events:
            if event.stream_type is MicrostructureStreamType.DEPTH_UPDATE:
                result = (
                    book.apply_update(event)
                    if book.synchronized
                    else book.buffer_update(event)
                )
            elif event.stream_type is MicrostructureStreamType.SNAPSHOT:
                result = book.apply_snapshot(event)
            else:
                continue
            if result.gap_classification is not None:
                counts[result.gap_classification.value] += 1
            if result.status is OrderBookStatus.INVALID:
                book.begin_resync()
        return book, {item.value: counts[item.value] for item in GapClassification}


def _required_string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"capture manifest {name} must be a string")
    return value


def _non_negative_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"capture manifest {name} must be a non-negative integer")
    return value


def _monotonic(value: int) -> None:
    if value < 0:
        raise ValueError("monotonic timestamp must be non-negative")


def _milliseconds(nanoseconds: int) -> float:
    return nanoseconds / 1_000_000


def _decimal_milliseconds(nanoseconds: int) -> Decimal:
    return Decimal(nanoseconds) / Decimal(1_000_000)


def _route_name(connection_id: str) -> str:
    if "public" in connection_id.lower():
        return "PUBLIC"
    if "market" in connection_id.lower():
        return "MARKET"
    return "UNKNOWN"


def _runtime_health(manifest: dict[str, object]) -> dict[str, object]:
    value = manifest.get("recorder_runtime_health")
    return value if isinstance(value, dict) else {}
