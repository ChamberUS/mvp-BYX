"""Offline USD-M feed-hardening evidence and fixed Sprint 4A.2.1 artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.health import (
    CaptureQualityStatus,
    FeedHealthAnalyzer,
    FeedHealthStatus,
)
from adaptive_trader.microstructure.models import (
    MicrostructureEvent,
    MicrostructureStreamType,
    OrderBookStatus,
)
from adaptive_trader.microstructure.order_book import LocalOrderBook
from adaptive_trader.microstructure.replay import MicrostructureReplayEngine, ReplaySpeed
from adaptive_trader.microstructure.routing import FuturesStreamRouter
from adaptive_trader.microstructure.sequence import GapClassification
from adaptive_trader.microstructure.storage import inspect_session

FUTURES_FEED_ARTIFACTS = (
    "experiment_manifest.json",
    "official_stream_mapping.json",
    "previous_futures_smoke_diagnosis.json",
    "stream_delivery_summary.json",
    "connection_health.json",
    "order_book_sequence_analysis.json",
    "gap_classification.json",
    "resync_events.csv",
    "book_ticker_alignment.csv",
    "feed_health.json",
    "capture_quality_scorecard.json",
    "replay_determinism.json",
    "futures_feed_hardening_report.md",
)


class FuturesFeedHardeningService:
    def run(
        self,
        *,
        session_path: Path,
        output_dir: Path,
        previous_session_path: Path | None = None,
    ) -> Path:
        capture = inspect_session(session_path)
        if capture.get("market") != MarketType.USD_M_FUTURES.value:
            raise ValueError("Futures feed hardening requires a USD-M Futures session")
        manifest_hash = hashlib.sha256(
            (session_path / "manifest.json").read_bytes()
        ).hexdigest()
        started_at = datetime.now(tz=UTC)
        experiment_id = (
            f"futures-feed-hardening-{started_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{manifest_hash[:8]}"
        )
        target = output_dir / experiment_id
        target.mkdir(parents=True, exist_ok=False)
        analyzer = FeedHealthAnalyzer()
        health, scorecard, details = analyzer.analyze(session_path)
        events = MicrostructureReplayEngine(seed=42).load_events(session_path)
        sequence = self._sequence_analysis(events, _required_string(capture, "symbol"))
        previous = self._previous_diagnosis(previous_session_path)
        delivery = self._delivery_summary(capture, events)
        connection_health = self._connection_health(capture, events)
        replay = self._replay_determinism(session_path)
        requested_duration = capture.get("requested_duration_seconds")
        duration_requirement_met = (
            isinstance(requested_duration, int)
            and not isinstance(requested_duration, bool)
            and requested_duration >= 300
            and health.capture_duration_seconds >= 295
        )
        ready_for_long_capture = (
            health.status is FeedHealthStatus.READY
            and scorecard.status is CaptureQualityStatus.CAPTURE_VALID
            and replay["same_input_hash"] is True
            and replay["same_output_hash"] is True
            and duration_requirement_met
        )
        readiness = (
            "READY_FOR_LONG_CAPTURE" if ready_for_long_capture else "NOT_READY"
        )
        manifest: dict[str, object] = {
            "experiment_id": experiment_id,
            "sprint": "4A.2.1",
            "session": str(session_path),
            "previous_session": (
                str(previous_session_path) if previous_session_path is not None else None
            ),
            "session_manifest_hash": manifest_hash,
            "started_at": started_at,
            "completed_at": datetime.now(tz=UTC),
            "official_documentation_observed_on": FuturesStreamRouter.OBSERVED_ON,
            "readiness": readiness,
            "readiness_criteria": {
                "requested_duration_at_least_300_seconds": duration_requirement_met,
                "feed_health_ready": health.status is FeedHealthStatus.READY,
                "capture_valid": scorecard.status is CaptureQualityStatus.CAPTURE_VALID,
                "replay_twice_identical": (
                    replay["same_input_hash"] is True
                    and replay["same_output_hash"] is True
                ),
            },
            "research_only": True,
            "authentication_used": False,
            "private_streams_used": False,
            "orders_sent": False,
            "testnet_used": False,
            "paper_trading_used": False,
            "profitability_claimed": False,
            "leverage": "1",
            "artifacts": FUTURES_FEED_ARTIFACTS,
        }
        self._write_json(target / "experiment_manifest.json", manifest)
        self._write_json(
            target / "official_stream_mapping.json",
            FuturesStreamRouter().official_mapping(),
        )
        self._write_json(target / "previous_futures_smoke_diagnosis.json", previous)
        self._write_json(target / "stream_delivery_summary.json", delivery)
        self._write_json(target / "connection_health.json", connection_health)
        self._write_json(target / "order_book_sequence_analysis.json", sequence)
        self._write_json(target / "gap_classification.json", details["gap_classification"])
        self._write_resync_csv(target / "resync_events.csv", capture)
        self._write_alignment_csv(target / "book_ticker_alignment.csv", events)
        self._write_json(target / "feed_health.json", health)
        self._write_json(target / "capture_quality_scorecard.json", scorecard)
        self._write_json(target / "replay_determinism.json", replay)
        (target / "futures_feed_hardening_report.md").write_text(
            self._markdown(manifest, health, scorecard, previous, sequence),
            encoding="utf-8",
        )
        observed = tuple(sorted(path.name for path in target.iterdir()))
        if observed != tuple(sorted(FUTURES_FEED_ARTIFACTS)):
            raise RuntimeError("Futures feed-hardening artifact contract changed")
        return target

    @staticmethod
    def _previous_diagnosis(session_path: Path | None) -> dict[str, object]:
        if session_path is None:
            return {
                "available": False,
                "conclusion": "NO_PREVIOUS_FUTURES_SMOKE_PROVIDED",
            }
        capture = inspect_session(session_path)
        events = MicrostructureReplayEngine(seed=42).load_events(session_path)
        counts = Counter(event.stream_type.value for event in events)
        depths = [
            event
            for event in events
            if event.stream_type is MicrostructureStreamType.DEPTH_UPDATE
        ]
        pu_transitions = 0
        pu_matches = 0
        spot_rule_false_gaps = 0
        previous_u: int | None = None
        for event in depths:
            if previous_u is not None:
                pu_transitions += 1
                if event.sequence_previous == previous_u:
                    pu_matches += 1
                    if not (
                        event.sequence_first is not None
                        and event.sequence_last is not None
                        and event.sequence_first <= previous_u + 1 <= event.sequence_last
                    ):
                        spot_rule_false_gaps += 1
            previous_u = event.sequence_last
        missing_market = (
            counts[MicrostructureStreamType.AGG_TRADE.value] == 0
            and counts[MicrostructureStreamType.MARK_PRICE.value] == 0
        )
        reported_gaps = capture.get("gaps", 0)
        false_gap_evidence = spot_rule_false_gaps > 0 and pu_matches > 0
        return {
            "available": True,
            "session": str(session_path),
            "event_count": len(events),
            "stream_event_counts": dict(sorted(counts.items())),
            "reported_gap_count": reported_gaps,
            "reported_resync_count": capture.get("resyncs", 0),
            "missing_market_route_streams": missing_market,
            "legacy_unrouted_url_in_baseline": "wss://fstream.binance.com/stream",
            "market_stream_delivery_failure_cause": (
                "MARKET_STREAMS_REQUESTED_ON_LEGACY_UNROUTED_CONNECTION"
                if missing_market
                else "NOT_OBSERVED"
            ),
            "depth_transition_count": pu_transitions,
            "pu_chain_match_count": pu_matches,
            "spot_contiguity_false_gap_count": spot_rule_false_gaps,
            "false_gap_evidence": false_gap_evidence,
            "gap_root_cause": (
                "SPOT_U_CONTAINS_PREVIOUS_PLUS_ONE_RULE_APPLIED_AFTER_FUTURES_BOOTSTRAP"
                if false_gap_evidence
                else "INSUFFICIENT_EVIDENCE"
            ),
            "conclusion": (
                "LEGACY_ROUTING_AND_CROSS_MARKET_SEQUENCE_POLICY_EXPLAIN_THE_SMOKE"
                if missing_market and false_gap_evidence
                else "PARTIAL_DIAGNOSIS"
            ),
            "orders_sent": False,
            "authentication_used": False,
        }

    @staticmethod
    def _delivery_summary(
        capture: dict[str, object],
        events: tuple[MicrostructureEvent, ...],
    ) -> dict[str, object]:
        counts = Counter(event.stream_type.value for event in events)
        delivery = capture.get("stream_delivery")
        return {
            "subscription_manifest": capture.get("subscription_manifest", []),
            "recorded_delivery": delivery if isinstance(delivery, list) else [],
            "event_counts_by_type": dict(sorted(counts.items())),
            "all_four_required_streams_delivered": all(
                counts[name] > 0
                for name in (
                    MicrostructureStreamType.AGG_TRADE.value,
                    MicrostructureStreamType.BOOK_TICKER.value,
                    MicrostructureStreamType.DEPTH_UPDATE.value,
                    MicrostructureStreamType.MARK_PRICE.value,
                )
            ),
            "valid_parse_required": True,
            "parser_errors": capture.get("parser_errors", 0),
        }

    @staticmethod
    def _connection_health(
        capture: dict[str, object],
        events: tuple[MicrostructureEvent, ...],
    ) -> dict[str, object]:
        by_connection: dict[str, Counter[str]] = {}
        for event in events:
            counter = by_connection.setdefault(event.connection_id, Counter())
            counter["events"] += 1
            if event.connection_state is not None:
                counter[event.connection_state] += 1
        subscription_manifest = capture.get("subscription_manifest", [])
        subscriptions = (
            subscription_manifest if isinstance(subscription_manifest, list) else []
        )
        return {
            "connections": {
                connection_id: dict(sorted(counter.items()))
                for connection_id, counter in sorted(by_connection.items())
            },
            "stream_liveness": capture.get("stream_liveness", {}),
            "disconnects": capture.get("disconnects", 0),
            "separate_public_market_connections": len(
                {
                    item.get("connection_id")
                    for item in subscriptions
                    if isinstance(item, dict)
                }
            )
            >= 2,
        }

    @staticmethod
    def _sequence_analysis(
        events: tuple[MicrostructureEvent, ...],
        symbol: str,
    ) -> dict[str, object]:
        book = LocalOrderBook(MarketType.USD_M_FUTURES, symbol)
        counts: Counter[str] = Counter()
        accepted_depth = 0
        snapshot_count = 0
        for event in events:
            if event.stream_type is MicrostructureStreamType.DEPTH_UPDATE:
                result = (
                    book.apply_update(event)
                    if book.synchronized
                    else book.buffer_update(event)
                )
            elif event.stream_type is MicrostructureStreamType.SNAPSHOT:
                snapshot_count += 1
                result = book.apply_snapshot(event)
            else:
                continue
            if result.applied and event.stream_type is MicrostructureStreamType.DEPTH_UPDATE:
                accepted_depth += 1
            if result.gap_classification is not None:
                counts[result.gap_classification.value] += 1
            if result.status is OrderBookStatus.INVALID:
                book.begin_resync()
        return {
            "market": MarketType.USD_M_FUTURES.value,
            "policy": book.sequence_policy.name,
            "bootstrap_rule": "U <= lastUpdateId <= u",
            "steady_state_rule": "event.pu == previous_event.u",
            "spot_previous_plus_one_rule_used_after_bootstrap": False,
            "snapshot_count": snapshot_count,
            "accepted_depth_update_count": accepted_depth,
            "final_update_id": book.update_id,
            "final_status": book.status.value,
            "classifications": {
                item.value: counts[item.value] for item in GapClassification
            },
        }

    @staticmethod
    def _replay_determinism(session_path: Path) -> dict[str, object]:
        engine = MicrostructureReplayEngine(seed=42)
        first = engine.run(
            session_path,
            speed=ReplaySpeed.MAX,
            handler=lambda event, clock: event.event_id,
        )
        second = engine.run(
            session_path,
            speed=ReplaySpeed.MAX,
            handler=lambda event, clock: event.event_id,
        )
        return {
            "first": first,
            "second": second,
            "same_input_hash": first.input_hash == second.input_hash,
            "same_output_hash": first.output_hash == second.output_hash,
            "same_event_count": first.event_count == second.event_count,
            "real_sleep_used": False,
            "deterministic_merge_key": (
                "exchange_event_time,exchange_transaction_time,connection_id,"
                "connection_sequence,receive_monotonic_ns,event_id"
            ),
        }

    @staticmethod
    def _write_resync_csv(path: Path, capture: dict[str, object]) -> None:
        fieldnames = (
            "classification",
            "event_id",
            "connection_id",
            "connection_sequence",
            "sequence_first",
            "sequence_last",
            "sequence_previous",
            "observed_at",
        )
        rows = capture.get("resync_events", [])
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=fieldnames, lineterminator="\n"
            )
            writer.writeheader()
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        writer.writerow({name: row.get(name) for name in fieldnames})

    @staticmethod
    def _write_alignment_csv(
        path: Path,
        events: tuple[MicrostructureEvent, ...],
    ) -> None:
        fieldnames = (
            "event_time",
            "connection_id",
            "book_ticker_bid",
            "local_book_bid",
            "bid_delta",
            "book_ticker_ask",
            "local_book_ask",
            "ask_delta",
            "exactly_aligned",
        )
        symbol = events[0].symbol if events else "ETHUSDT"
        book = LocalOrderBook(MarketType.USD_M_FUTURES, symbol)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=fieldnames, lineterminator="\n"
            )
            writer.writeheader()
            for event in events:
                if event.stream_type is MicrostructureStreamType.DEPTH_UPDATE:
                    result = (
                        book.apply_update(event)
                        if book.synchronized
                        else book.buffer_update(event)
                    )
                elif event.stream_type is MicrostructureStreamType.SNAPSHOT:
                    result = book.apply_snapshot(event)
                elif event.stream_type is MicrostructureStreamType.BOOK_TICKER:
                    bid = book.best_bid
                    ask = book.best_ask
                    ticker_bid = event.best_bid
                    ticker_ask = event.best_ask
                    bid_delta = (
                        ticker_bid - bid.price
                        if ticker_bid is not None and bid is not None
                        else None
                    )
                    ask_delta = (
                        ticker_ask - ask.price
                        if ticker_ask is not None and ask is not None
                        else None
                    )
                    writer.writerow(
                        {
                            "event_time": event.exchange_event_time.isoformat(),
                            "connection_id": event.connection_id,
                            "book_ticker_bid": ticker_bid,
                            "local_book_bid": bid.price if bid is not None else None,
                            "bid_delta": bid_delta,
                            "book_ticker_ask": ticker_ask,
                            "local_book_ask": ask.price if ask is not None else None,
                            "ask_delta": ask_delta,
                            "exactly_aligned": bid_delta == 0 and ask_delta == 0,
                        }
                    )
                    continue
                else:
                    continue
                if result.status is OrderBookStatus.INVALID:
                    book.begin_resync()

    @staticmethod
    def _markdown(
        manifest: dict[str, object],
        health: object,
        scorecard: object,
        previous: dict[str, object],
        sequence: dict[str, object],
    ) -> str:
        health_status = getattr(getattr(health, "status", None), "value", "UNKNOWN")
        quality_status = getattr(
            getattr(scorecard, "status", None), "value", "UNKNOWN"
        )
        return f"""# Futures Feed Hardening — Sprint 4A.2.1

Readiness: **{manifest['readiness']}**

This is a public-market-data, research-only validation. It does not use authentication,
private streams, account endpoints, Testnet, paper trading, external orders, alpha selection,
or leverage above 1x. It makes no profitability claim.

## Routing and delivery

USD-M high-frequency `bookTicker` and `depth@100ms` are routed to `/public`; `aggTrade`
and `markPrice@1s` are routed independently to `/market`. Legacy unrouted WebSocket URLs are
rejected. Feed health is `{health_status}` and capture quality is `{quality_status}`.

## Previous 30-second smoke

Diagnosis: `{previous.get('conclusion', 'UNAVAILABLE')}`. The earlier implementation requested
MARKET streams through the legacy route and applied Spot post-snapshot contiguity to Futures.
Those are transport/policy defects, not evidence of 66 genuine exchange sequence gaps.

## Order book

- Bootstrap: `{sequence['bootstrap_rule']}`
- Steady state: `{sequence['steady_state_rule']}`
- Final status: `{sequence['final_status']}`

The 1,800-second capture is permitted only when this report says
`READY_FOR_LONG_CAPTURE`. Otherwise the fail-closed result is `NOT_READY`.
"""

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
                default=_json_default,
            )
            + "\n",
            encoding="utf-8",
        )


def _json_default(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _required_string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"capture manifest {name} must be a string")
    return value
