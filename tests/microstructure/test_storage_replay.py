from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.codec import event_from_record, event_to_record
from adaptive_trader.microstructure.replay import (
    MicrostructureReplayEngine,
    ReplaySpeed,
    VirtualClock,
)
from adaptive_trader.microstructure.storage import (
    MicrostructureSessionWriter,
    inspect_session,
    recover_incomplete_session,
)
from tests.microstructure.helpers import at, depth_event, snapshot_event, trade_event, write_session


def test_rotated_compressed_complete_manifest_and_round_trip(tmp_path: Path) -> None:
    writer = MicrostructureSessionWriter(
        tmp_path,
        market_type=MarketType.SPOT,
        symbol="ETHUSDT",
        session_id="rotation",
        started_at=at(),
        rotate_event_count=2,
    )
    events = (
        snapshot_event(),
        depth_event(),
        trade_event(milliseconds=20),
        depth_event(first=102, last=102, milliseconds=30),
    )
    for event in events:
        writer.append(event)
    summary = writer.close(complete=True, gaps=1, disconnects=2, resyncs=1)
    manifest = inspect_session(summary.session_path)

    assert summary.event_count == 4
    assert summary.completeness == "COMPLETE"
    assert len(summary.files) == 2
    assert manifest["hashes_valid"] is True
    assert manifest["gaps"] == 1 and manifest["disconnects"] == 2
    assert manifest["credentials_persisted"] is False
    for metadata in summary.files:
        payload = (summary.session_path / metadata.path).read_bytes()
        assert payload[:2] == b"\x1f\x8b"
        assert hashlib.sha256(payload).hexdigest() == metadata.file_hash
        assert metadata.raw_size > 0 and metadata.compressed_size > 0
    replayed = MicrostructureReplayEngine().load_events(summary.session_path)
    assert {event.event_id for event in replayed} == {event.event_id for event in events}
    assert event_from_record(event_to_record(events[0])) == events[0]


def test_spot_futures_paths_and_incomplete_session_are_separate(tmp_path: Path) -> None:
    spot = write_session(tmp_path / "spot-root", complete=False)
    futures = write_session(
        tmp_path / "futures-root",
        market=MarketType.USD_M_FUTURES,
        complete=True,
    )

    assert "/spot/ETHUSDT/" in str(spot)
    assert "/futures/ETHUSDT/" in str(futures)
    assert inspect_session(spot)["completeness"] == "INCOMPLETE"
    assert inspect_session(futures)["completeness"] == "COMPLETE"


def test_secret_marker_and_session_lifecycle_guards(tmp_path: Path) -> None:
    writer = MicrostructureSessionWriter(
        tmp_path,
        market_type=MarketType.SPOT,
        symbol="ETHUSDT",
        session_id="secret-guard",
        started_at=at(),
    )
    secret_record = trade_event()
    record = event_to_record(secret_record)
    record["raw_payload_json"] = '{"api_key":"forbidden"}'
    record["raw_payload_hash"] = hashlib.sha256(
        str(record["raw_payload_json"]).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="credentials"):
        writer.append(event_from_record(record))
    writer.append(snapshot_event())
    writer.close(complete=True)
    with pytest.raises(RuntimeError, match="closed"):
        writer.append(snapshot_event())
    with pytest.raises(RuntimeError, match="already closed"):
        writer.close(complete=True)
    with pytest.raises(ValueError, match="safe path"):
        MicrostructureSessionWriter(
            tmp_path,
            market_type=MarketType.SPOT,
            symbol="ETHUSDT",
            session_id="../unsafe",
            started_at=at(),
        )


def test_crash_recovery_validates_gzip_jsonl_without_claiming_completion(tmp_path: Path) -> None:
    session = tmp_path / "crashed"
    session.mkdir()
    part = session / "events-00001.jsonl.gz.part"
    with gzip.open(part, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(event_to_record(snapshot_event())) + "\n")

    recovered = recover_incomplete_session(session)

    assert recovered == (session / "events-00001.jsonl.gz",)
    assert recovered[0].is_file() and not part.exists()

    bad = session / "events-00002.jsonl.gz.part"
    with gzip.open(bad, "wt", encoding="utf-8") as handle:
        handle.write("not json\n")
    with pytest.raises(ValueError, match="corrupt incomplete"):
        recover_incomplete_session(session)


def test_hash_mismatch_and_file_corruption_fail_replay(tmp_path: Path) -> None:
    session = write_session(tmp_path / "hash")
    manifest = inspect_session(session)
    files = manifest["files"]
    assert isinstance(files, list)
    first = files[0]
    assert isinstance(first, dict) and isinstance(first["path"], str)
    path = session / first["path"]
    path.write_bytes(path.read_bytes() + b"tampered")
    assert inspect_session(session)["hashes_valid"] is False
    with pytest.raises(ValueError, match="hash mismatch"):
        MicrostructureReplayEngine().load_events(session)

    corrupt = write_session(tmp_path / "corrupt")
    corrupt_manifest_path = corrupt / "manifest.json"
    corrupt_manifest = json.loads(corrupt_manifest_path.read_text(encoding="utf-8"))
    corrupt_file = corrupt / corrupt_manifest["files"][0]["path"]
    corrupt_file.write_bytes(b"not-gzip")
    corrupt_manifest["files"][0]["file_hash"] = hashlib.sha256(b"not-gzip").hexdigest()
    corrupt_manifest_path.write_text(
        json.dumps(corrupt_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="corrupt microstructure replay"):
        MicrostructureReplayEngine().load_events(corrupt)


def test_replay_order_seed_modes_and_virtual_clock_are_deterministic(tmp_path: Path) -> None:
    session = write_session(tmp_path)
    engine = MicrostructureReplayEngine(seed=77)
    events = engine.load_events(session)
    assert tuple(event.exchange_event_time for event in events) == tuple(
        sorted(event.exchange_event_time for event in events)
    )
    def handler(event, clock):
        return f"{clock.now.isoformat()}|{event.event_id}"

    maximum = engine.run(session, speed=ReplaySpeed.MAX, handler=handler)
    step = engine.run(session, speed=ReplaySpeed.STEP, handler=handler)
    one_x = engine.run(session, speed=ReplaySpeed.ONE_X, handler=handler)

    assert maximum.seed == step.seed == one_x.seed == 77
    assert maximum.input_hash == step.input_hash == one_x.input_hash
    assert maximum.output_hash == step.output_hash == one_x.output_hash
    assert maximum.real_sleep_used is False
    assert len(tuple(engine.steps(session))) == maximum.event_count

    clock = VirtualClock()
    with pytest.raises(RuntimeError, match="has not observed"):
        _ = clock.now
    clock.advance_to(at(10))
    with pytest.raises(ValueError, match="backwards"):
        clock.advance_to(at(9))
    with pytest.raises(ValueError, match="timezone-aware"):
        VirtualClock().advance_to(datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="non-negative"):
        MicrostructureReplayEngine(seed=-1)


def test_invalid_manifest_and_codec_records_fail_closed(tmp_path: Path) -> None:
    session = tmp_path / "invalid"
    session.mkdir()
    (session / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        inspect_session(session)
    with pytest.raises(ValueError, match="must be an object"):
        event_from_record([])
    record = event_to_record(snapshot_event())
    record["receive_monotonic_ns"] = "bad"
    with pytest.raises(ValueError, match="must be an integer"):
        event_from_record(record)
