from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaptive_trader.cli.main import main
from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.futures_feed import (
    FUTURES_FEED_ARTIFACTS,
    FuturesFeedHardeningService,
)
from adaptive_trader.microstructure.health import (
    CaptureQualityStatus,
    FeedHealthAnalyzer,
    FeedHealthStatus,
    StreamLivenessMonitor,
    StreamLivenessState,
)
from adaptive_trader.microstructure.order_book import LocalOrderBook
from adaptive_trader.microstructure.parsing import (
    connection_state_event,
    parse_public_event,
)
from adaptive_trader.microstructure.replay import MicrostructureReplayEngine
from adaptive_trader.microstructure.routing import (
    FuturesConnectionPlan,
    FuturesStreamRoute,
    FuturesStreamRouter,
)
from adaptive_trader.microstructure.sequence import GapClassification
from adaptive_trader.microstructure.storage import (
    MicrostructureSessionWriter,
    StreamSubscriptionMetadata,
)
from tests.microstructure.helpers import at, depth_event, snapshot_event


def _subscriptions() -> tuple[StreamSubscriptionMetadata, ...]:
    plans = FuturesStreamRouter().plans(
        "ETHUSDT",
        ("aggTrade", "bookTicker", "depth", "markPrice"),
    )
    return tuple(
        StreamSubscriptionMetadata(
            requested_stream=stream.requested_stream,
            canonical_stream=stream.stream_name,
            route=plan.route.value,
            connection_id=plan.connection_id,
            url=plan.url,
        )
        for plan in plans
        for stream in plan.streams
    )


def _full_futures_session(root: Path) -> Path:
    writer = MicrostructureSessionWriter(
        root,
        market_type=MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        session_id="full-futures",
        started_at=at(),
        subscriptions=_subscriptions(),
        requested_duration_seconds=300,
    )
    writer.append(
        connection_state_event(
            market_type=MarketType.USD_M_FUTURES,
            symbol="ETHUSDT",
            state="CONNECTED",
            timestamp=at(),
            monotonic_ns=1_000_000,
            connection_id="futures-public-1",
            connection_sequence=1,
        )
    )
    writer.append(
        connection_state_event(
            market_type=MarketType.USD_M_FUTURES,
            symbol="ETHUSDT",
            state="CONNECTED",
            timestamp=at(),
            monotonic_ns=1_000_001,
            connection_id="futures-market-1",
            connection_sequence=1,
        )
    )
    writer.append(
        parse_public_event(
            {
                "stream": "ethusdt@depth@100ms",
                "data": {
                    "e": "depthUpdate",
                    "E": int(at(10).timestamp() * 1000),
                    "T": int(at(10).timestamp() * 1000),
                    "s": "ETHUSDT",
                    "U": 100,
                    "u": 101,
                    "pu": 99,
                    "b": [],
                    "a": [],
                },
            },
            market_type=MarketType.USD_M_FUTURES,
            receive_wall_time=at(10),
            receive_monotonic_ns=2_000_000,
            connection_id="futures-public-1",
            connection_sequence=2,
        )
    )
    writer.append(snapshot_event(market=MarketType.USD_M_FUTURES, update_id=100))
    writer.append(
        parse_public_event(
            {
                "stream": "ethusdt@bookTicker",
                "data": {
                    "e": "bookTicker",
                    "E": int(at(20).timestamp() * 1000),
                    "T": int(at(20).timestamp() * 1000),
                    "s": "ETHUSDT",
                    "u": 101,
                    "b": "2000",
                    "B": "2",
                    "a": "2000.10",
                    "A": "2",
                },
            },
            market_type=MarketType.USD_M_FUTURES,
            receive_wall_time=at(20),
            receive_monotonic_ns=3_000_000,
            connection_id="futures-public-1",
            connection_sequence=3,
        )
    )
    trade = parse_public_event(
        {
            "stream": "ethusdt@aggTrade",
            "data": {
                "e": "aggTrade",
                "E": int(at(30).timestamp() * 1000),
                "T": int(at(30).timestamp() * 1000),
                "s": "ETHUSDT",
                "a": 7,
                "p": "2000.05",
                "q": "0.5",
                "f": 70,
                "l": 72,
                "m": False,
            },
        },
        market_type=MarketType.USD_M_FUTURES,
        receive_wall_time=at(30),
        receive_monotonic_ns=4_000_000,
        connection_id="futures-market-1",
        connection_sequence=2,
    )
    writer.append(trade)
    mark = parse_public_event(
        {
            "stream": "ethusdt@markPrice@1s",
            "data": {
                "e": "markPriceUpdate",
                "E": int(at(40).timestamp() * 1000),
                "s": "ETHUSDT",
                "p": "2000.06",
                "i": "2000.04",
                "r": "-0.00001",
                "T": int(at(1000).timestamp() * 1000),
            },
        },
        market_type=MarketType.USD_M_FUTURES,
        receive_wall_time=at(40),
        receive_monotonic_ns=5_000_000,
        connection_id="futures-market-1",
        connection_sequence=3,
    )
    writer.append(mark)
    writer.append(
        depth_event(
            market=MarketType.USD_M_FUTURES,
            first=150,
            last=200,
            previous=101,
            milliseconds=50,
        )
    )
    writer.set_capture_metadata(
        parser_errors=0,
        liveness_summary={
            name: {"state": StreamLivenessState.LIVE.value}
            for name in ("aggTrade", "bookTicker", "depth", "markPrice")
        },
    )
    return writer.close(complete=True).session_path


def test_typed_futures_router_splits_routes_and_rejects_private_legacy_misuse() -> None:
    router = FuturesStreamRouter()
    plans = router.plans(
        "ETHUSDT",
        ("aggTrade", "bookTicker", "depth", "markPrice"),
    )

    assert tuple(plan.route for plan in plans) == (
        FuturesStreamRoute.PUBLIC,
        FuturesStreamRoute.MARKET,
    )
    assert "/public/stream?streams=ethusdt@bookTicker/ethusdt@depth@100ms" in plans[0].url
    assert "/market/stream?streams=ethusdt@aggTrade/ethusdt@markPrice@1s" in plans[1].url
    for plan in plans:
        router.validate_url(plan)
    with pytest.raises(ValueError, match="private"):
        router.route("private", "ETHUSDT")
    with pytest.raises(ValueError, match="legacy"):
        router.validate_url(
            FuturesConnectionPlan(
                connection_id="bad",
                route=FuturesStreamRoute.PUBLIC,
                url=router.LEGACY_BASE_URL,
                streams=(router.route("depth", "ETHUSDT"),),
            )
        )


def test_futures_sequence_uses_pu_after_inclusive_snapshot_alignment() -> None:
    book = LocalOrderBook(MarketType.USD_M_FUTURES, "ETHUSDT")
    book.buffer_update(
        depth_event(
            market=MarketType.USD_M_FUTURES,
            first=100,
            last=101,
            previous=99,
        )
    )
    assert book.apply_snapshot(
        snapshot_event(market=MarketType.USD_M_FUTURES, update_id=100)
    ).synchronized

    accepted = book.apply_update(
        depth_event(
            market=MarketType.USD_M_FUTURES,
            first=150,
            last=200,
            previous=101,
        )
    )
    gap = book.apply_update(
        depth_event(
            market=MarketType.USD_M_FUTURES,
            first=201,
            last=205,
            previous=202,
        )
    )

    assert accepted.applied and accepted.gap_classification is None
    assert gap.gap_classification is GapClassification.REAL_SEQUENCE_GAP
    assert book.sequence_gap_count == 1


def test_mark_price_trade_ids_and_connection_metadata_are_lossless(
    tmp_path: Path,
) -> None:
    session = _full_futures_session(tmp_path)
    events = MicrostructureReplayEngine(seed=42).load_events(session)
    trade = next(
        event for event in events if event.stream_type.value == "AGG_TRADE"
    )
    mark = next(
        event for event in events if event.stream_type.value == "MARK_PRICE"
    )
    assert (trade.first_trade_id, trade.last_trade_id) == (70, 72)
    assert trade.connection_id == "futures-market-1"
    assert mark.index_price is not None and mark.funding_rate is not None
    assert mark.next_funding_time == at(1000)
    assert mark.exchange_transaction_time is None


def test_stream_liveness_detects_first_event_timeout_stale_and_recovery() -> None:
    monitor = StreamLivenessMonitor((("markPrice", "market-1"),))
    monitor.connected("market-1", 1_000_000)
    assert monitor.summary()["markPrice"]["state"] == "WAITING_FIRST_EVENT"
    monitor.evaluate(6_100_000_000)
    assert monitor.summary()["markPrice"]["state"] == "FAILED"

    recovered = StreamLivenessMonitor((("markPrice", "market-1"),))
    recovered.connected("market-1", 1_000_000)
    recovered.observed(
        "markPrice",
        connection_id="market-1",
        connection_sequence=1,
        now_ns=2_000_000,
    )
    recovered.evaluate(4_000_000_000)
    assert recovered.summary()["markPrice"]["state"] == "STALE"
    recovered.observed(
        "markPrice",
        connection_id="market-1",
        connection_sequence=2,
        now_ns=4_100_000_000,
    )
    summary = recovered.summary()["markPrice"]
    assert summary["state"] == "LIVE" and summary["recovery_count"] == 1


def test_health_cli_and_exact_hardening_artifacts_fail_closed_on_short_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ADAPTIVE_TRADER_DB_PATH", str(tmp_path / "cli.sqlite3"))
    session = _full_futures_session(tmp_path / "capture")
    health, scorecard, _ = FeedHealthAnalyzer().analyze(session)

    assert health.status is FeedHealthStatus.READY
    assert scorecard.status is CaptureQualityStatus.CAPTURE_VALID
    assert main(["market", "microstructure", "health", "--session", str(session)]) == 0
    assert json.loads(capsys.readouterr().out)["feed_health"]["status"] == "READY"

    report = FuturesFeedHardeningService().run(
        session_path=session,
        output_dir=tmp_path / "reports",
    )
    assert tuple(sorted(path.name for path in report.iterdir())) == tuple(
        sorted(FUTURES_FEED_ARTIFACTS)
    )
    manifest = json.loads((report / "experiment_manifest.json").read_text())
    replay = json.loads((report / "replay_determinism.json").read_text())
    mapping = json.loads((report / "official_stream_mapping.json").read_text())
    assert manifest["readiness"] == "NOT_READY"
    assert manifest["authentication_used"] is False and manifest["orders_sent"] is False
    assert replay["same_input_hash"] and replay["same_output_hash"]
    assert mapping["private_route_used"] is False
