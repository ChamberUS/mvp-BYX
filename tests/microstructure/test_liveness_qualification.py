from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from adaptive_trader.microstructure.health import (
    CaptureQualityScorecard,
    CaptureQualityStatus,
    FeedHealthStatus,
    LivenessIncidentClassification,
    LivenessIncidentState,
    LongCaptureReadinessStatus,
    MicrostructureFeedHealth,
    StreamLivenessMonitor,
)
from adaptive_trader.microstructure.liveness_qualification import (
    LongCaptureReadinessAssessor,
)
from adaptive_trader.microstructure.runtime_health import RecorderRuntimeMonitor


def _connected_monitor() -> StreamLivenessMonitor:
    monitor = StreamLivenessMonitor(
        (("depth", "futures-public-1"), ("bookTicker", "futures-public-1"))
    )
    monitor.connected("futures-public-1", 0)
    monitor.heartbeat("futures-public-1", 1)
    monitor.observed(
        "depth",
        connection_id="futures-public-1",
        connection_sequence=1,
        now_ns=1_000_000,
    )
    monitor.observed(
        "bookTicker",
        connection_id="futures-public-1",
        connection_sequence=2,
        now_ns=1_000_000,
    )
    return monitor


def _health() -> MicrostructureFeedHealth:
    return MicrostructureFeedHealth(
        status=FeedHealthStatus.READY,
        reasons=("ALL_REQUIRED_PUBLIC_STREAMS_HEALTHY",),
        market="USD_M_FUTURES",
        symbol="ETHUSDT",
        required_streams=("aggTrade", "bookTicker", "depth", "markPrice"),
        delivered_streams=("aggTrade", "bookTicker", "depth", "markPrice"),
        missing_streams=(),
        event_counts={"aggTrade": 2, "bookTicker": 2, "depth": 2, "markPrice": 2},
        real_sequence_gaps=0,
        parser_errors=0,
        connection_restarts=0,
        stale_incidents=0,
        liveness_recoveries=0,
        unresolved_incidents=0,
        dropped_events=0,
        queue_high_watermark=2,
        events_pending=0,
        final_order_book_status="SYNCHRONIZED",
        capture_duration_seconds=Decimal(300),
    )


def _scorecard() -> CaptureQualityScorecard:
    return CaptureQualityScorecard(
        status=CaptureQualityStatus.CAPTURE_VALID,
        reasons=(),
        hashes_valid=True,
        complete=True,
        all_required_streams_delivered=True,
        parser_clean=True,
        sequence_clean=True,
        book_synchronized=True,
        liveness_stable=True,
    )


def test_a_depth_silence_500ms_without_cross_activity_is_not_stale() -> None:
    monitor = _connected_monitor()
    monitor.evaluate(501_000_000)
    assert monitor.summary()["depth"]["state"] == "LIVE"
    assert monitor.incidents == ()


def test_b_depth_silence_500ms_with_stable_book_ticker_is_not_stale() -> None:
    monitor = _connected_monitor()
    monitor.evaluate(501_000_000)
    monitor.finalize(501_000_000)
    assert monitor.summary()["depth"]["stale_count"] == 0


def test_c_depth_silence_with_book_ticker_changes_enters_observation() -> None:
    monitor = _connected_monitor()
    monitor.evaluate(2_101_000_000)
    monitor.observed(
        "bookTicker",
        connection_id="futures-public-1",
        connection_sequence=3,
        now_ns=2_201_000_000,
    )
    monitor.evaluate(2_301_000_000)
    assert monitor.summary()["depth"]["state"] == "STALE"
    assert monitor.incidents[0].state is LivenessIncidentState.OBSERVING


def test_d_local_processing_pause_is_classified_on_recovery() -> None:
    monitor = _connected_monitor()
    monitor.evaluate(2_101_000_000)
    monitor.observed(
        "depth",
        connection_id="futures-public-1",
        connection_sequence=3,
        now_ns=2_501_000_000,
        sequence_continuous=True,
        local_processing_delay=True,
        queue_depth=10,
    )
    assert (
        monitor.incidents[0].classification
        is LivenessIncidentClassification.LOCAL_PROCESSING_DELAY
    )


def test_e_persistence_backlog_degrades_until_recovered() -> None:
    runtime = RecorderRuntimeMonitor(queue_capacity=100, maximum_processing_backlog=10)
    for _ in range(12):
        runtime.received(
            receive_monotonic_ns=1,
            parsing_completed_ns=2,
            persistence_queued_ns=3,
            queue_depth=12,
        )
    assert runtime.summary().status == "DEGRADED"
    for _ in range(12):
        runtime.processed(
            receive_monotonic_ns=1,
            processing_started_ns=2,
            book_update_completed_ns=3,
            persistence_started_ns=4,
            persistence_completed_ns=5,
            queue_depth=0,
        )
    assert runtime.summary().status == "READY"


def test_f_real_pu_gap_escalates_recovered_incident_to_integrity_failure() -> None:
    monitor = _connected_monitor()
    monitor.evaluate(2_101_000_000)
    monitor.observed(
        "depth",
        connection_id="futures-public-1",
        connection_sequence=3,
        now_ns=2_501_000_000,
        sequence_continuous=False,
        caused_gap=True,
        caused_resync=True,
        caused_book_invalid=True,
    )
    incident = monitor.incidents[0]
    assert incident.classification is LivenessIncidentClassification.TRUE_FEED_STALL
    assert incident.caused_gap and incident.caused_resync and incident.caused_book_invalid


def test_g_recovered_stale_returns_current_stream_to_live() -> None:
    monitor = _connected_monitor()
    monitor.evaluate(2_101_000_000)
    monitor.observed(
        "depth",
        connection_id="futures-public-1",
        connection_sequence=3,
        now_ns=2_501_000_000,
        sequence_continuous=True,
    )
    assert monitor.summary()["depth"]["state"] == "LIVE"
    assert monitor.incidents[0].state is LivenessIncidentState.RECOVERED


def test_h_unresolved_stale_fails_closed_at_session_end() -> None:
    monitor = _connected_monitor()
    monitor.evaluate(2_101_000_000)
    monitor.observed(
        "bookTicker",
        connection_id="futures-public-1",
        connection_sequence=3,
        now_ns=2_201_000_000,
    )
    monitor.finalize(12_501_000_000)
    assert monitor.summary()["depth"]["state"] == "FAILED"
    assert monitor.incidents[0].unresolved is True


def test_i_dropped_event_is_not_ready() -> None:
    runtime = RecorderRuntimeMonitor(queue_capacity=100, maximum_processing_backlog=10)
    runtime.dropped()
    assert runtime.summary().status == "NOT_READY"
    readiness = LongCaptureReadinessAssessor().assess(
        health=replace(_health(), dropped_events=1),
        scorecard=_scorecard(),
        replay_deterministic=True,
        duration_requirement_met=True,
    )
    assert readiness.status is LongCaptureReadinessStatus.NOT_READY_FOR_LONG_CAPTURE


def test_j_parser_error_is_not_ready() -> None:
    readiness = LongCaptureReadinessAssessor().assess(
        health=replace(_health(), parser_errors=1),
        scorecard=replace(_scorecard(), parser_clean=False),
        replay_deterministic=True,
        duration_requirement_met=True,
    )
    assert "PARSER_ERROR" in readiness.reasons


def test_k_disconnect_marks_active_incident_unresolved() -> None:
    monitor = _connected_monitor()
    monitor.evaluate(2_101_000_000)
    monitor.connection_restarted("futures-public-1", 2_201_000_000)
    assert monitor.incidents[0].state is LivenessIncidentState.ESCALATED
    assert monitor.incidents[0].unresolved is True


def test_l_reconnect_waits_for_fresh_stream_events() -> None:
    monitor = _connected_monitor()
    monitor.connection_restarted("futures-public-1", 2_000_000)
    monitor.connected("futures-public-1", 3_000_000)
    assert monitor.summary()["depth"]["state"] == "WAITING_FIRST_EVENT"
    assert monitor.summary()["depth"]["restart_count"] == 1


def test_clean_ready_is_ready_for_long_capture() -> None:
    readiness = LongCaptureReadinessAssessor().assess(
        health=_health(),
        scorecard=_scorecard(),
        replay_deterministic=True,
        duration_requirement_met=True,
    )
    assert readiness.status is LongCaptureReadinessStatus.READY_FOR_LONG_CAPTURE


def test_recovered_warning_is_ready_with_warnings() -> None:
    readiness = LongCaptureReadinessAssessor().assess(
        health=replace(_health(), stale_incidents=1, liveness_recoveries=1),
        scorecard=replace(
            _scorecard(), status=CaptureQualityStatus.CAPTURE_VALID_WITH_WARNINGS
        ),
        replay_deterministic=True,
        duration_requirement_met=True,
    )
    assert readiness.status is LongCaptureReadinessStatus.READY_WITH_WARNINGS


@pytest.mark.parametrize(
    ("health", "scorecard", "replay"),
    (
        (replace(_health(), unresolved_incidents=1), _scorecard(), True),
        (replace(_health(), real_sequence_gaps=1), _scorecard(), True),
        (
            replace(_health(), final_order_book_status="INVALID"),
            replace(_scorecard(), book_synchronized=False),
            True,
        ),
        (_health(), _scorecard(), False),
    ),
)
def test_readiness_failures_are_not_ready(
    health: MicrostructureFeedHealth,
    scorecard: CaptureQualityScorecard,
    replay: bool,
) -> None:
    readiness = LongCaptureReadinessAssessor().assess(
        health=health,
        scorecard=scorecard,
        replay_deterministic=replay,
        duration_requirement_met=True,
    )
    assert readiness.status is LongCaptureReadinessStatus.NOT_READY_FOR_LONG_CAPTURE
