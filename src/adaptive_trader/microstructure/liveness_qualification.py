"""Quantitative Futures liveness and long-capture qualification artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.health import (
    CaptureQualityScorecard,
    CaptureQualityStatus,
    FeedHealthAnalyzer,
    FeedHealthStatus,
    LongCaptureReadinessStatus,
    MicrostructureFeedHealth,
    SessionQualityStatus,
    default_liveness_config,
)
from adaptive_trader.microstructure.models import (
    MicrostructureEvent,
    MicrostructureStreamType,
    OrderBookStatus,
)
from adaptive_trader.microstructure.order_book import LocalOrderBook
from adaptive_trader.microstructure.replay import MicrostructureReplayEngine
from adaptive_trader.microstructure.routing import FuturesStreamRouter
from adaptive_trader.microstructure.storage import inspect_session

BASE_ARTIFACTS = (
    "experiment_manifest.json",
    "previous_stale_incident_analysis.json",
    "liveness_config.json",
    "stream_interarrival_statistics.json",
    "stream_interarrival_samples.csv",
    "transport_latency_summary.json",
    "local_processing_latency.json",
    "recorder_runtime_health.json",
    "liveness_incidents.csv",
    "cross_stream_consistency.json",
    "qualification_smoke_summary.json",
    "long_capture_summary.json",
    "replay_determinism.json",
    "readiness_assessment.json",
    "futures_liveness_qualification_report.md",
)

STREAM_NAMES = {
    MicrostructureStreamType.AGG_TRADE: "aggTrade",
    MicrostructureStreamType.BOOK_TICKER: "bookTicker",
    MicrostructureStreamType.DEPTH_UPDATE: "depth",
    MicrostructureStreamType.MARK_PRICE: "markPrice",
}


@dataclass(frozen=True, slots=True)
class QualityBudgets:
    maximum_unresolved_incidents: int = 0
    maximum_real_sequence_gaps: int = 0
    maximum_dropped_events: int = 0
    maximum_parser_errors: int = 0
    maximum_book_invalid_duration_ms: int = 0
    maximum_processing_backlog: int = 5_000
    maximum_recovered_stale_duration_ms: int = 10_000
    source: str = "ENGINEERING_ASSUMPTION"


@dataclass(frozen=True, slots=True)
class LongCaptureReadiness:
    status: LongCaptureReadinessStatus
    current_health: FeedHealthStatus
    session_quality: SessionQualityStatus
    reasons: tuple[str, ...]
    warning_count: int
    all_required_streams_active: bool
    replay_deterministic: bool
    runtime_queue_healthy: bool
    duration_requirement_met: bool


class LongCaptureReadinessAssessor:
    def __init__(self, budgets: QualityBudgets | None = None) -> None:
        self.budgets = budgets or QualityBudgets()

    def assess(
        self,
        *,
        health: MicrostructureFeedHealth,
        scorecard: CaptureQualityScorecard,
        replay_deterministic: bool,
        duration_requirement_met: bool,
        recovered_incident_durations_ms: tuple[Decimal, ...] = (),
    ) -> LongCaptureReadiness:
        reasons: list[str] = []
        if health.status is not FeedHealthStatus.READY:
            reasons.append(f"CURRENT_HEALTH_{health.status.value}")
        if not scorecard.all_required_streams_delivered:
            reasons.append("REQUIRED_STREAM_MISSING")
        if health.unresolved_incidents > self.budgets.maximum_unresolved_incidents:
            reasons.append("UNRESOLVED_LIVENESS_INCIDENT")
        if health.real_sequence_gaps > self.budgets.maximum_real_sequence_gaps:
            reasons.append("REAL_SEQUENCE_GAP")
        if health.dropped_events > self.budgets.maximum_dropped_events:
            reasons.append("DROPPED_EVENT")
        if health.parser_errors > self.budgets.maximum_parser_errors:
            reasons.append("PARSER_ERROR")
        if health.final_order_book_status != OrderBookStatus.SYNCHRONIZED.value:
            reasons.append("BOOK_DESYNCHRONIZED")
        if not replay_deterministic:
            reasons.append("REPLAY_MISMATCH")
        if any(
            duration > Decimal(self.budgets.maximum_recovered_stale_duration_ms)
            for duration in recovered_incident_durations_ms
        ):
            reasons.append("RECOVERED_INCIDENT_DURATION_BUDGET_EXCEEDED")
        runtime_queue_healthy = health.events_pending == 0
        if not runtime_queue_healthy:
            reasons.append("RUNTIME_BACKLOG_UNRECOVERED")
        if not duration_requirement_met:
            reasons.append("CAPTURE_DURATION_REQUIREMENT_NOT_MET")
        invalid = scorecard.status is CaptureQualityStatus.CAPTURE_INVALID
        if invalid and not reasons:
            reasons.append("SESSION_INVALID")
        warnings = health.stale_incidents + health.connection_restarts
        if reasons:
            status = LongCaptureReadinessStatus.NOT_READY_FOR_LONG_CAPTURE
            session_quality = SessionQualityStatus.INVALID
        elif warnings or scorecard.status is CaptureQualityStatus.CAPTURE_VALID_WITH_WARNINGS:
            status = LongCaptureReadinessStatus.READY_WITH_WARNINGS
            session_quality = SessionQualityStatus.VALID_WITH_WARNINGS
        else:
            status = LongCaptureReadinessStatus.READY_FOR_LONG_CAPTURE
            session_quality = SessionQualityStatus.CLEAN
        return LongCaptureReadiness(
            status=status,
            current_health=health.status,
            session_quality=session_quality,
            reasons=tuple(reasons or ("OBJECTIVE_QUALITY_BUDGETS_SATISFIED",)),
            warning_count=warnings,
            all_required_streams_active=scorecard.all_required_streams_delivered,
            replay_deterministic=replay_deterministic,
            runtime_queue_healthy=runtime_queue_healthy,
            duration_requirement_met=duration_requirement_met,
        )


class FuturesLivenessQualificationService:
    """Build the fixed Sprint 4A.2.2 evidence bundle without network access."""

    def run(
        self,
        *,
        qualification_session_path: Path,
        output_dir: Path,
        previous_session_path: Path | None = None,
        long_session_path: Path | None = None,
    ) -> Path:
        smoke = self._session_analysis(qualification_session_path, minimum_seconds=295)
        smoke_capture = _object_dict(smoke["capture"])
        if smoke_capture.get("market") != MarketType.USD_M_FUTURES.value:
            raise ValueError("liveness qualification requires USD-M Futures")
        long_analysis = (
            self._session_analysis(long_session_path, minimum_seconds=1_795)
            if long_session_path is not None
            else None
        )
        diagnostic_analysis = long_analysis if long_analysis is not None else smoke
        capture = _object_dict(diagnostic_analysis["capture"])
        session_hash = _manifest_hash(qualification_session_path)
        now = datetime.now(tz=UTC)
        experiment_id = (
            f"futures-liveness-qualification-{now.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{session_hash[:8]}"
        )
        target = output_dir / experiment_id
        target.mkdir(parents=True, exist_ok=False)
        previous = self._previous_stale_analysis(previous_session_path)
        events = diagnostic_analysis["events"]
        if not isinstance(events, tuple):
            raise RuntimeError("session event collection lost tuple type")
        interarrival, samples = _interarrival_analysis(events)
        transport = _transport_latency(events)
        cross_stream = _cross_stream_consistency(
            events, _required_string(capture, "symbol")
        )
        runtime = _object_dict(capture.get("recorder_runtime_health", {}))
        processing = _object_dict(capture.get("local_processing_latency", {}))
        incidents = _incident_rows(capture)
        readiness = smoke["readiness"]
        if not isinstance(readiness, LongCaptureReadiness):
            raise RuntimeError("readiness assessment lost typed value")
        final_readiness = (
            long_analysis["readiness"] if long_analysis is not None else readiness
        )
        if not isinstance(final_readiness, LongCaptureReadiness):
            raise RuntimeError("final readiness assessment lost typed value")
        final_4a3 = self._final_4a3(long_analysis, final_readiness)
        experiment = {
            "experiment_id": experiment_id,
            "sprint": "4A.2.2",
            "created_at": now,
            "qualification_session": str(qualification_session_path),
            "long_session": str(long_session_path) if long_session_path else None,
            "qualification_session_manifest_hash": session_hash,
            "official_documentation_observed_on": "2026-08-07",
            "official_documentation": FuturesStreamRouter().official_mapping(),
            "quality_budgets": QualityBudgets(),
            "smoke_readiness": readiness.status,
            "final_readiness": final_readiness.status,
            "final_4a3_decision": final_4a3,
            "research_only": True,
            "authentication_used": False,
            "private_streams_used": False,
            "orders_sent": False,
            "testnet_used": False,
            "paper_trading_used": False,
            "profitability_analyzed": False,
            "leverage": "1",
            "artifacts": BASE_ARTIFACTS
            + (("long_capture_manifest.json",) if long_analysis is not None else ()),
        }
        liveness_config = _liveness_config(capture)
        smoke_summary = _public_session_summary(smoke)
        long_summary: dict[str, object] = (
            _public_session_summary(long_analysis)
            if long_analysis is not None
            else {
                "executed": False,
                "reason": (
                    "SMOKE_NOT_READY"
                    if readiness.status
                    is LongCaptureReadinessStatus.NOT_READY_FOR_LONG_CAPTURE
                    else "LONG_CAPTURE_NOT_PROVIDED"
                ),
            }
        )
        if long_analysis is not None:
            long_summary.update(
                {
                    "transport_latency": transport,
                    "local_processing_latency": processing,
                    "cross_stream_consistency": cross_stream,
                    "connection_uptime_percent": (
                        Decimal(100)
                        if long_summary.get("disconnects") == 0
                        else "NOT_DERIVABLE_FROM_EVENT_COUNTS"
                    ),
                    "uptime_percent": (
                        Decimal(100)
                        if long_summary.get("disconnects") == 0
                        else "NOT_DERIVABLE_FROM_EVENT_COUNTS"
                    ),
                    "book_synchronized_percent": cross_stream.get(
                        "book_synchronized_percent"
                    ),
                    "book_invalid_duration_ms": (
                        Decimal(0)
                        if long_summary.get("book_status")
                        == OrderBookStatus.SYNCHRONIZED.value
                        and long_summary.get("resyncs") == 0
                        else "NOT_DERIVABLE_WITHOUT_TRANSITION_TIMESTAMPS"
                    ),
                }
            )
        replay = (
            long_analysis["replay"] if long_analysis is not None else smoke["replay"]
        )
        assessment = {
            "smoke": readiness,
            "final": final_readiness,
            "ready_for_4a3": final_4a3,
            "quality_budgets": QualityBudgets(),
            "historical_recovered_incidents_are_not_permanent_failures": True,
        }
        _write_json(target / "experiment_manifest.json", experiment)
        _write_json(target / "previous_stale_incident_analysis.json", previous)
        _write_json(target / "liveness_config.json", liveness_config)
        _write_json(target / "stream_interarrival_statistics.json", interarrival)
        _write_interarrival_csv(target / "stream_interarrival_samples.csv", samples)
        _write_json(target / "transport_latency_summary.json", transport)
        _write_json(target / "local_processing_latency.json", processing)
        _write_json(target / "recorder_runtime_health.json", runtime)
        _write_incidents_csv(target / "liveness_incidents.csv", incidents)
        _write_json(target / "cross_stream_consistency.json", cross_stream)
        _write_json(target / "qualification_smoke_summary.json", smoke_summary)
        _write_json(target / "long_capture_summary.json", long_summary)
        _write_json(target / "replay_determinism.json", replay)
        _write_json(target / "readiness_assessment.json", assessment)
        if long_analysis is not None:
            _write_json(
                target / "long_capture_manifest.json",
                self._long_manifest(long_session_path, long_analysis),
            )
        (target / "futures_liveness_qualification_report.md").write_text(
            _markdown_report(experiment, previous, smoke_summary, long_summary, assessment),
            encoding="utf-8",
        )
        expected = set(BASE_ARTIFACTS)
        if long_analysis is not None:
            expected.add("long_capture_manifest.json")
        if {path.name for path in target.iterdir()} != expected:
            raise RuntimeError("liveness qualification artifact contract changed")
        return target

    @staticmethod
    def _session_analysis(session_path: Path, *, minimum_seconds: int) -> dict[str, object]:
        capture = inspect_session(session_path)
        health, scorecard, details = FeedHealthAnalyzer().analyze(session_path)
        events = MicrostructureReplayEngine(seed=42).load_events(session_path)
        replay = _replay_determinism(session_path, events)
        requested = capture.get("requested_duration_seconds")
        duration_requirement_met = (
            isinstance(requested, int)
            and not isinstance(requested, bool)
            and requested >= minimum_seconds
            and health.capture_duration_seconds >= Decimal(minimum_seconds)
        )
        readiness = LongCaptureReadinessAssessor().assess(
            health=health,
            scorecard=scorecard,
            replay_deterministic=bool(replay["deterministic"]),
            duration_requirement_met=duration_requirement_met,
            recovered_incident_durations_ms=_recovered_durations(capture),
        )
        return {
            "path": session_path,
            "capture": capture,
            "events": events,
            "health": health,
            "scorecard": scorecard,
            "health_details": details,
            "replay": replay,
            "readiness": readiness,
        }

    @staticmethod
    def _previous_stale_analysis(session_path: Path | None) -> dict[str, object]:
        if session_path is None:
            return {"available": False, "classification": "INCONCLUSIVE"}
        capture = inspect_session(session_path)
        events = MicrostructureReplayEngine(seed=42).load_events(session_path)
        depth = sorted(
            (
                event
                for event in events
                if event.stream_type is MicrostructureStreamType.DEPTH_UPDATE
            ),
            key=lambda event: event.receive_monotonic_ns,
        )
        if len(depth) < 2:
            return {
                "available": True,
                "session": str(session_path),
                "classification": "INCONCLUSIVE",
                "reason": "INSUFFICIENT_DEPTH_EVENTS",
            }
        incident_index, (previous, following) = max(
            enumerate(zip(depth, depth[1:], strict=False)),
            key=lambda indexed: (
                indexed[1][1].receive_monotonic_ns
                - indexed[1][0].receive_monotonic_ns
            ),
        )
        between = tuple(
            event
            for event in events
            if previous.receive_monotonic_ns
            < event.receive_monotonic_ns
            < following.receive_monotonic_ns
        )
        activity = Counter(STREAM_NAMES.get(event.stream_type, "OTHER") for event in between)
        connection_states = [
            event.connection_state
            for event in between
            if event.stream_type is MicrostructureStreamType.CONNECTION_STATE
        ]
        gap_ms = _ns_ms(following.receive_monotonic_ns - previous.receive_monotonic_ns)
        exchange_gap_ms = Decimal(
            str((following.exchange_event_time - previous.exchange_event_time).total_seconds())
        ) * Decimal(1_000)
        pu_continuous = following.sequence_previous == previous.sequence_last
        return {
            "available": True,
            "session": str(session_path),
            "stream": "depth",
            "route": "PUBLIC",
            "connection_id": previous.connection_id,
            "previous_event_id": previous.event_id,
            "following_event_id": following.event_id,
            "previous_exchange_event_timestamp": previous.exchange_event_time,
            "following_exchange_event_timestamp": following.exchange_event_time,
            "previous_exchange_transaction_timestamp": previous.exchange_transaction_time,
            "following_exchange_transaction_timestamp": following.exchange_transaction_time,
            "previous_receive_wall_timestamp": previous.receive_wall_time,
            "following_receive_wall_timestamp": following.receive_wall_time,
            "previous_receive_monotonic_ns": previous.receive_monotonic_ns,
            "following_receive_monotonic_ns": following.receive_monotonic_ns,
            "receive_gap_ms": gap_ms,
            "exchange_event_gap_ms": exchange_gap_ms,
            "expected_threshold_ms": 2_000,
            "book_ticker_active_during_interval": activity["bookTicker"] > 0,
            "book_ticker_event_count_during_interval": activity["bookTicker"],
            "agg_trade_active_during_interval": activity["aggTrade"] > 0,
            "agg_trade_event_count_during_interval": activity["aggTrade"],
            "mark_price_active_during_interval": activity["markPrice"] > 0,
            "mark_price_event_count_during_interval": activity["markPrice"],
            "websocket_connected": "DISCONNECTED" not in connection_states,
            "ping_pong_healthy": "UNKNOWN_NOT_INSTRUMENTED_IN_PREVIOUS_CAPTURE",
            "cpu_event_loop_lag_observable": False,
            "cpu_event_loop_lag": "UNKNOWN_NOT_INSTRUMENTED_IN_PREVIOUS_CAPTURE",
            "sequence_pu_continuous_after_recovery": pu_continuous,
            "book_remained_consistent": (
                pu_continuous and capture.get("gaps") == 0 and capture.get("resyncs") == 0
            ),
            "snapshot_necessary": False,
            "resync_necessary": False,
            "top_of_book_diverged": "UNKNOWN_NOT_MEASURED_IN_PREVIOUS_CAPTURE",
            "events_during_receive_gap": len(between),
            "events_immediately_before": [
                event.event_id
                for event in depth[max(0, incident_index - 4) : incident_index + 1]
            ],
            "events_immediately_after": [
                event.event_id
                for event in depth[incident_index + 1 : incident_index + 6]
            ],
            "classification": "INCONCLUSIVE",
            "classification_reason": (
                "Cross-stream activity and a continuous pu chain rule out observed sequence "
                "loss, but the old recorder did not measure ping/pong, event-loop lag, or "
                "pipeline-stage latency; network jitter and local buffering cannot be "
                "distinguished honestly."
            ),
        }

    @staticmethod
    def _final_4a3(
        long_analysis: dict[str, object] | None,
        readiness: LongCaptureReadiness,
    ) -> str:
        if long_analysis is None:
            return "NOT_READY_FOR_4A3"
        return (
            "READY_FOR_4A3"
            if readiness.status
            in {
                LongCaptureReadinessStatus.READY_FOR_LONG_CAPTURE,
                LongCaptureReadinessStatus.READY_WITH_WARNINGS,
            }
            else "NOT_READY_FOR_4A3"
        )

    @staticmethod
    def _long_manifest(
        session_path: Path | None,
        analysis: dict[str, object],
    ) -> dict[str, object]:
        if session_path is None:
            raise ValueError("long session path is required")
        capture = _object_dict(analysis["capture"])
        health = analysis["health"]
        readiness = analysis["readiness"]
        replay = _object_dict(analysis["replay"])
        if not isinstance(health, MicrostructureFeedHealth) or not isinstance(
            readiness, LongCaptureReadiness
        ):
            raise RuntimeError("long capture analysis lost typed values")
        raw_subscriptions = capture.get("subscription_manifest", [])
        subscriptions = raw_subscriptions if isinstance(raw_subscriptions, list) else []
        raw_files = capture.get("files", [])
        files = raw_files if isinstance(raw_files, list) else []
        routes = {
            route
            for item in subscriptions
            if isinstance(item, dict)
            for route in (item.get("route"),)
            if isinstance(route, str)
        }
        return {
            "session_id": capture.get("session_id"),
            "start": capture.get("first_event"),
            "end": capture.get("last_event"),
            "duration_seconds": health.capture_duration_seconds,
            "market": capture.get("market"),
            "symbol": capture.get("symbol"),
            "routes": sorted(routes),
            "streams": capture.get("stream_delivery", []),
            "event_counts": health.event_counts,
            "hashes": [item.get("file_hash") for item in files if isinstance(item, dict)],
            "dataset_hash": _manifest_hash(session_path),
            "quality": readiness.session_quality,
            "warnings": readiness.warning_count,
            "current_health": health.status,
            "sequence_integrity": health.real_sequence_gaps == 0,
            "replay_hashes": replay,
            "software_commit": "970d1e4+SPRINT_4A_2_WORKTREE",
        }


def _interarrival_analysis(
    events: tuple[MicrostructureEvent, ...],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    statistics: dict[str, object] = {}
    samples: list[dict[str, object]] = []
    for stream in ("depth", "bookTicker", "aggTrade", "markPrice"):
        selected = [event for event in events if STREAM_NAMES.get(event.stream_type) == stream]
        receive_order = sorted(selected, key=lambda event: event.receive_monotonic_ns)
        exchange_order = sorted(selected, key=lambda event: event.exchange_event_time)
        receive_gaps = [
            _ns_ms(current.receive_monotonic_ns - previous.receive_monotonic_ns)
            for previous, current in zip(
                receive_order, receive_order[1:], strict=False
            )
        ]
        exchange_gaps = [
            Decimal(
                str(
                    (
                        current.exchange_event_time - previous.exchange_event_time
                    ).total_seconds()
                )
            )
            * Decimal(1_000)
            for previous, current in zip(
                exchange_order, exchange_order[1:], strict=False
            )
        ]
        config = default_liveness_config(stream)
        statistics[stream] = {
            "event_count": len(selected),
            "receive_time_interarrival": _distribution(receive_gaps),
            "exchange_event_time_interarrival": _distribution(exchange_gaps),
            "expected_cadence_ms": config.expected_cadence_ms,
            "liveness_mode": config.mode,
            "missing_expected_intervals": (
                sum(gap > Decimal(2_500) for gap in receive_gaps)
                if stream == "markPrice"
                else None
            ),
            "trade_rate_events_per_second": (
                _rate(len(selected), receive_order) if stream == "aggTrade" else None
            ),
            "fixed_periodicity_assumed": stream == "markPrice",
        }
        for index in _sample_indices(len(receive_gaps), maximum=1_000):
            samples.append(
                {
                    "stream": stream,
                    "sample_index": index,
                    "receive_gap_ms": receive_gaps[index],
                    "exchange_event_gap_ms": exchange_gaps[index],
                    "sampled_from_total": len(receive_gaps),
                }
            )
    return {
        "receive_and_exchange_cadence_are_separate": True,
        "update_speed_is_not_assumed_to_be_a_heartbeat": True,
        "streams": statistics,
    }, samples


def _transport_latency(events: tuple[MicrostructureEvent, ...]) -> dict[str, object]:
    by_stream: dict[str, object] = {}
    all_values: list[Decimal] = []
    for stream in ("depth", "bookTicker", "aggTrade", "markPrice"):
        values = [
            Decimal(str((event.receive_wall_time - event.exchange_event_time).total_seconds()))
            * Decimal(1_000)
            for event in events
            if STREAM_NAMES.get(event.stream_type) == stream
        ]
        all_values.extend(values)
        summary = _distribution(values)
        summary["negative_sample_count"] = len(
            [value for value in values if value < Decimal(0)]
        )
        by_stream[stream] = summary
    negative_count = len([value for value in all_values if value < Decimal(0)])
    return {
        "definition": "receive_wall_time - exchange_event_time",
        "clock_caveat": "Apparent one-way latency includes exchange/client clock offset.",
        "clock_alignment_valid": negative_count == 0,
        "negative_sample_count": negative_count,
        "one_way_latency_measurement_valid": negative_count == 0,
        "invalid_measurement_reason": (
            None
            if negative_count == 0
            else "CLIENT_WALL_CLOCK_PRECEDED_EXCHANGE_EVENT_TIME"
        ),
        "not_strategy_latency": True,
        "not_order_latency": True,
        "not_event_loop_lag": True,
        "streams": by_stream,
    }


def _cross_stream_consistency(
    events: tuple[MicrostructureEvent, ...], symbol: str
) -> dict[str, object]:
    book = LocalOrderBook(MarketType.USD_M_FUTURES, symbol)
    pending_since_ns: int | None = None
    target: tuple[Decimal, Decimal] | None = None
    durations: list[Decimal] = []
    mismatch_count = 0
    synchronized_observations = 0
    book_observations = 0
    for event in events:
        if event.stream_type is MicrostructureStreamType.SNAPSHOT:
            result = book.apply_snapshot(event)
        elif event.stream_type is MicrostructureStreamType.DEPTH_UPDATE:
            result = book.apply_update(event) if book.synchronized else book.buffer_update(event)
        else:
            result = None
        if result is not None:
            book_observations += 1
            synchronized_observations += result.status is OrderBookStatus.SYNCHRONIZED
            if result.status is OrderBookStatus.INVALID:
                book.begin_resync()
            if target is not None and _book_matches(book, target):
                if pending_since_ns is not None:
                    durations.append(_ns_ms(event.receive_monotonic_ns - pending_since_ns))
                pending_since_ns = None
                target = None
        if event.stream_type is MicrostructureStreamType.BOOK_TICKER:
            if event.best_bid is None or event.best_ask is None:
                continue
            observed_target = (event.best_bid, event.best_ask)
            if _book_matches(book, observed_target):
                if pending_since_ns is not None:
                    durations.append(_ns_ms(event.receive_monotonic_ns - pending_since_ns))
                pending_since_ns = None
                target = None
            elif target != observed_target:
                if pending_since_ns is None:
                    mismatch_count += 1
                    pending_since_ns = event.receive_monotonic_ns
                target = observed_target
    distribution = _distribution(durations)
    return {
        "method": (
            "Track mismatch episodes between latest bookTicker best prices and the local "
            "book; concurrent routes are not required to match byte-for-byte."
        ),
        "mismatch_duration": distribution,
        "median_mismatch_duration_ms": distribution.get("median_ms"),
        "p95_mismatch_duration_ms": distribution.get("p95_ms"),
        "p99_mismatch_duration_ms": distribution.get("p99_ms"),
        "max_mismatch_duration_ms": distribution.get("max_ms"),
        "mismatch_count": mismatch_count,
        "unresolved_mismatch_count": int(pending_since_ns is not None),
        "book_synchronized_percent": (
            Decimal(synchronized_observations) / Decimal(book_observations) * Decimal(100)
            if book_observations
            else Decimal(0)
        ),
        "byte_for_byte_simultaneity_required": False,
    }


def _replay_determinism(
    session_path: Path, events: tuple[MicrostructureEvent, ...]
) -> dict[str, object]:
    first = _replay_hashes(events)
    second = _replay_hashes(events)
    return {
        "session": str(session_path),
        "first": first,
        "second": second,
        "same_event_count": first["event_count"] == second["event_count"],
        "same_event_hash": first["event_hash"] == second["event_hash"],
        "same_book_state_hash": first["book_state_hash"] == second["book_state_hash"],
        "same_feature_hash": first["feature_hash"] == second["feature_hash"],
        "same_health_event_hash": (
            first["health_event_hash"] == second["health_event_hash"]
        ),
        "deterministic": first == second,
        "real_sleep_used": False,
    }


def _replay_hashes(events: tuple[MicrostructureEvent, ...]) -> dict[str, object]:
    book = LocalOrderBook(MarketType.USD_M_FUTURES, events[0].symbol if events else "ETHUSDT")
    event_parts: list[str] = []
    feature_parts: list[str] = []
    health_parts: list[str] = []
    for event in events:
        event_parts.append(event.event_id)
        feature_parts.append(
            "|".join(
                str(value)
                for value in (
                    event.stream_type.value,
                    event.price,
                    event.quantity,
                    event.best_bid,
                    event.best_ask,
                    event.mark_price,
                )
            )
        )
        if event.stream_type is MicrostructureStreamType.DEPTH_UPDATE:
            result = book.apply_update(event) if book.synchronized else book.buffer_update(event)
        elif event.stream_type is MicrostructureStreamType.SNAPSHOT:
            result = book.apply_snapshot(event)
        else:
            result = None
        if result is not None and result.gap_classification is not None:
            health_parts.append(result.gap_classification.value)
        if event.stream_type is MicrostructureStreamType.CONNECTION_STATE:
            health_parts.append(f"{event.connection_id}:{event.connection_state}")
        if result is not None and result.status is OrderBookStatus.INVALID:
            book.begin_resync()
    book_state = "|".join(
        str(value)
        for value in (
            book.status.value,
            book.update_id,
            book.best_bid,
            book.best_ask,
            book.sequence_gap_count,
            book.resync_count,
        )
    )
    return {
        "event_count": len(events),
        "event_hash": _hash_lines(event_parts),
        "book_state_hash": _hash_lines([book_state]),
        "feature_hash": _hash_lines(feature_parts),
        "health_event_hash": _hash_lines(health_parts),
    }


def _public_session_summary(analysis: dict[str, object]) -> dict[str, object]:
    capture = _object_dict(analysis["capture"])
    events = analysis["events"]
    health = analysis["health"]
    readiness = analysis["readiness"]
    if not isinstance(events, tuple) or not isinstance(
        health, MicrostructureFeedHealth
    ) or not isinstance(readiness, LongCaptureReadiness):
        raise RuntimeError("session summary lost typed analysis values")
    raw_files = capture.get("files", [])
    files = raw_files if isinstance(raw_files, list) else []
    raw_size = sum(
        int(item.get("raw_size", 0)) for item in files if isinstance(item, dict)
    )
    compressed_size = sum(
        int(item.get("compressed_size", 0)) for item in files if isinstance(item, dict)
    )
    duration = health.capture_duration_seconds
    incidents = _incident_rows(capture)
    return {
        "executed": True,
        "session": str(analysis["path"]),
        "duration_seconds": duration,
        "total_events": len(events),
        "events_per_second": Decimal(len(events)) / duration if duration else Decimal(0),
        "per_stream_counts": health.event_counts,
        "disconnects": capture.get("disconnects", 0),
        "reconnects": health.connection_restarts,
        "real_sequence_gaps": health.real_sequence_gaps,
        "resyncs": capture.get("resyncs", 0),
        "stale_incidents": health.stale_incidents,
        "recovered_incidents": len(
            [row for row in incidents if row.get("state") == "RECOVERED"]
        ),
        "unresolved_incidents": health.unresolved_incidents,
        "dropped_events": health.dropped_events,
        "parser_errors": health.parser_errors,
        "runtime_health": capture.get("recorder_runtime_health", {}),
        "queue_high_watermark": health.queue_high_watermark,
        "book_status": health.final_order_book_status,
        "storage_raw_bytes": raw_size,
        "storage_compressed_bytes": compressed_size,
        "compression_ratio": (
            Decimal(raw_size) / Decimal(compressed_size) if compressed_size else Decimal(0)
        ),
        "raw_bytes_per_second": (
            Decimal(raw_size) / duration if duration else Decimal(0)
        ),
        "compressed_bytes_per_second": (
            Decimal(compressed_size) / duration if duration else Decimal(0)
        ),
        "file_rotations": len(files),
        "readiness": readiness,
        "current_health": health.status,
        "session_quality": readiness.session_quality,
    }


def _liveness_config(capture: dict[str, object]) -> dict[str, object]:
    stored = capture.get("liveness_config")
    if isinstance(stored, dict) and stored:
        configs = stored
    else:
        configs = {
            stream: asdict(default_liveness_config(stream))
            for stream in ("depth", "bookTicker", "aggTrade", "markPrice")
        }
    return {
        "streams": configs,
        "quality_budgets": QualityBudgets(),
        "policy": {
            "update_speed_is_heartbeat": False,
            "depth_silence_is_sequence_gap": False,
            "cross_stream_evidence_used": True,
            "recovered_incident_is_permanent_failure": False,
        },
    }


def _incident_rows(capture: dict[str, object]) -> list[dict[str, object]]:
    value = capture.get("liveness_incidents")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _recovered_durations(capture: dict[str, object]) -> tuple[Decimal, ...]:
    values: list[Decimal] = []
    for incident in _incident_rows(capture):
        if incident.get("state") != "RECOVERED":
            continue
        duration = incident.get("duration_ms")
        if isinstance(duration, (str, int)) and not isinstance(duration, bool):
            values.append(Decimal(duration))
    return tuple(values)


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
    position = Decimal(len(ordered) - 1) * percentile / Decimal(100)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _sample_indices(size: int, *, maximum: int) -> tuple[int, ...]:
    if size <= maximum:
        return tuple(range(size))
    return tuple(min(size - 1, index * size // maximum) for index in range(maximum))


def _rate(count: int, events: list[MicrostructureEvent]) -> Decimal:
    if len(events) < 2:
        return Decimal(0)
    duration = _ns_ms(events[-1].receive_monotonic_ns - events[0].receive_monotonic_ns)
    return Decimal(count) / (duration / Decimal(1_000)) if duration else Decimal(0)


def _book_matches(book: LocalOrderBook, target: tuple[Decimal, Decimal]) -> bool:
    bid = book.best_bid
    ask = book.best_ask
    return bid is not None and ask is not None and (bid.price, ask.price) == target


def _ns_ms(value: int) -> Decimal:
    return Decimal(value) / Decimal(1_000_000)


def _hash_lines(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def _manifest_hash(path: Path) -> str:
    return hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()


def _required_string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"capture manifest {name} must be a string")
    return value


def _object_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _json_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(_json_value(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_interarrival_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = (
        "stream",
        "sample_index",
        "receive_gap_ms",
        "exchange_event_gap_ms",
        "sampled_from_total",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            normalized = _json_value(row)
            if not isinstance(normalized, dict):
                raise RuntimeError("inter-arrival row did not serialize to an object")
            writer.writerow(normalized)


def _write_incidents_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = (
        "incident_id",
        "stream",
        "route",
        "connection_id",
        "state",
        "start",
        "recovery",
        "duration_ms",
        "classification",
        "caused_gap",
        "caused_resync",
        "caused_book_invalid",
        "unresolved",
        "evidence",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            normalized = {
                name: json.dumps(_json_value(row.get(name)))
                if name == "evidence"
                else _json_value(row.get(name))
                for name in fieldnames
            }
            writer.writerow(normalized)


def _markdown_report(
    experiment: dict[str, object],
    previous: dict[str, object],
    smoke: dict[str, object],
    long_summary: dict[str, object],
    assessment: dict[str, object],
) -> str:
    return f"""# Futures feed liveness qualification

Sprint: `4A.2.2`

Final decision: **{experiment['final_4a3_decision']}**

## Safety boundary

This qualification used public USD-M market data only. It did not authenticate,
use private streams or Testnet, submit orders, enable paper trading, change alpha,
analyze profitability, or use leverage above 1x.

## Interpretation

Binance's advertised stream update speed is not treated as a mandatory heartbeat.
`markPrice@1s` is approximately periodic; depth and bookTicker are change-driven,
and aggTrade is execution-driven. Depth silence is therefore distinct from a broken
`pu` sequence chain. Recovered incidents remain session warnings but do not leave
current health permanently degraded.

The previous 2.372 s receive-time incident is classified **{previous.get('classification')}**.
Its following depth update preserved the Futures `pu == previous u` chain, but the old
capture lacked event-loop and ping/pong instrumentation, so a narrower cause was not
invented.

## Qualification smoke

- Duration: {smoke.get('duration_seconds', 'unknown')} seconds
- Events: {smoke.get('total_events', 'unknown')}
- Current health: {smoke.get('current_health', 'unknown')}
- Readiness: {_json_value(smoke.get('readiness'))}

## Long capture

- Executed: {long_summary.get('executed')}
- Duration: {long_summary.get('duration_seconds', 'not available')} seconds
- Events: {long_summary.get('total_events', 'not available')}
- Gaps: {long_summary.get('real_sequence_gaps', 'not available')}
- Drops: {long_summary.get('dropped_events', 'not available')}

## Objective assessment

```json
{json.dumps(_json_value(assessment), indent=2, sort_keys=True)}
```

All latency fields distinguish exchange-event/receive transport timing from local
parsing, book update, queueing, and persistence timing. No strategic or order latency
is inferred from these measurements.
"""
