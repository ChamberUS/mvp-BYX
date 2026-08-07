"""Rotated gzip JSONL persistence for high-frequency public events."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.codec import event_record_json
from adaptive_trader.microstructure.models import (
    MicrostructureEvent,
    MicrostructureStreamType,
)
from adaptive_trader.microstructure.provenance import (
    RecorderProvenance,
    capture_recorder_provenance,
)

FORBIDDEN_SECRET_MARKERS = ("apikey", "api_key", "secret", "listenkey", "listen_key")


@dataclass(frozen=True, slots=True)
class MicrostructureFileMetadata:
    path: str
    event_count: int
    first_event: str
    last_event: str
    file_hash: str
    raw_size: int
    compressed_size: int


@dataclass(frozen=True, slots=True)
class MicrostructureSessionSummary:
    session_id: str
    session_path: Path
    market: str
    symbol: str
    event_count: int
    first_event: str | None
    last_event: str | None
    gaps: int
    disconnects: int
    resyncs: int
    completeness: str
    files: tuple[MicrostructureFileMetadata, ...]


@dataclass(frozen=True, slots=True)
class StreamSubscriptionMetadata:
    requested_stream: str
    canonical_stream: str
    route: str
    connection_id: str
    url: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.requested_stream,
                self.canonical_stream,
                self.route,
                self.connection_id,
                self.url,
            )
        ):
            raise ValueError("subscription metadata fields are required")
        lowered = f"{self.route}|{self.url}".lower()
        if any(marker in lowered for marker in ("/private", "listenkey", "listen_key")):
            raise ValueError("private subscription metadata is forbidden")


class MicrostructureSessionWriter:
    """Batch events in one gzip stream instead of one SQLite transaction per event."""

    def __init__(
        self,
        output_dir: Path,
        *,
        market_type: MarketType,
        symbol: str,
        session_id: str,
        started_at: datetime,
        rotate_event_count: int = 100_000,
        subscriptions: tuple[StreamSubscriptionMetadata, ...] = (),
        requested_duration_seconds: int | None = None,
        provenance: RecorderProvenance | None = None,
    ) -> None:
        if rotate_event_count <= 0:
            raise ValueError("rotate_event_count must be positive")
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        if not session_id or any(part in session_id for part in ("/", "\\", "..")):
            raise ValueError("session_id must be a safe path component")
        if requested_duration_seconds is not None and requested_duration_seconds < 0:
            raise ValueError("requested duration must be non-negative")
        self.market_type = market_type
        self.symbol = symbol.upper()
        self.session_id = session_id
        self.started_at = started_at.astimezone(UTC)
        market_dir = "spot" if market_type is MarketType.SPOT else "futures"
        self.session_path = (
            output_dir
            / market_dir
            / self.symbol
            / self.started_at.date().isoformat()
            / session_id
        )
        self.session_path.mkdir(parents=True, exist_ok=False)
        self.rotate_event_count = rotate_event_count
        self.subscriptions = subscriptions
        self.requested_duration_seconds = requested_duration_seconds
        self.provenance = provenance or capture_recorder_provenance(
            {
                "market": market_type.value,
                "symbol": self.symbol,
                "subscriptions": [item.requested_stream for item in subscriptions],
                "requested_duration_seconds": requested_duration_seconds,
                "rotate_event_count": rotate_event_count,
            }
        )
        self._handle: TextIO | None = None
        self._part_path: Path | None = None
        self._part_index = 0
        self._part_events = 0
        self._part_raw_size = 0
        self._part_first: str | None = None
        self._part_last: str | None = None
        self._files: list[MicrostructureFileMetadata] = []
        self._event_count = 0
        self._first_event: str | None = None
        self._last_event: str | None = None
        self._closed = False
        self._parser_errors = 0
        self._liveness_summary: dict[str, object] = {}
        self._liveness_config: dict[str, object] = {}
        self._liveness_incidents: tuple[dict[str, object], ...] = ()
        self._runtime_health: dict[str, object] = {}
        self._processing_latency: dict[str, object] = {}
        self._order_book_status = "UNKNOWN"
        self._resync_events: tuple[dict[str, object], ...] = ()
        self._delivery: dict[str, dict[str, object]] = {
            item.requested_stream: {
                "requested_stream": item.requested_stream,
                "canonical_stream": item.canonical_stream,
                "route": item.route,
                "connection_id": item.connection_id,
                "url": item.url,
                "event_count": 0,
                "first_exchange_event_time": None,
                "last_exchange_event_time": None,
                "first_receive_wall_time": None,
                "last_receive_wall_time": None,
                "first_connection_sequence": None,
                "last_connection_sequence": None,
            }
            for item in subscriptions
        }
        self._open_part()

    def append(self, event: MicrostructureEvent) -> None:
        if self._closed or self._handle is None:
            raise RuntimeError("microstructure session is closed")
        if event.market_type is not self.market_type or event.symbol != self.symbol:
            raise ValueError("event belongs to another session")
        line = event_record_json(event)
        normalized = line.lower()
        if any(marker in normalized for marker in FORBIDDEN_SECRET_MARKERS):
            raise ValueError("credentials or private listen keys cannot be persisted")
        encoded_size = len(line.encode("utf-8")) + 1
        self._handle.write(line + "\n")
        self._part_events += 1
        self._part_raw_size += encoded_size
        self._event_count += 1
        timestamp = event.exchange_event_time.isoformat()
        self._part_first = self._part_first or timestamp
        self._part_last = timestamp
        self._first_event = self._first_event or timestamp
        self._last_event = timestamp
        self._record_delivery(event)
        if self._part_events >= self.rotate_event_count:
            self._finalize_part()
            self._open_part()

    def set_capture_metadata(
        self,
        *,
        parser_errors: int,
        liveness_summary: dict[str, object],
        resync_events: tuple[dict[str, object], ...] = (),
        liveness_config: dict[str, object] | None = None,
        liveness_incidents: tuple[dict[str, object], ...] = (),
        runtime_health: dict[str, object] | None = None,
        processing_latency: dict[str, object] | None = None,
        order_book_status: str = "UNKNOWN",
    ) -> None:
        if parser_errors < 0:
            raise ValueError("parser error count must be non-negative")
        self._parser_errors = parser_errors
        self._liveness_summary = liveness_summary
        self._resync_events = resync_events
        self._liveness_config = liveness_config or {}
        self._liveness_incidents = liveness_incidents
        self._runtime_health = runtime_health or {}
        self._processing_latency = processing_latency or {}
        self._order_book_status = order_book_status

    def close(
        self,
        *,
        complete: bool,
        gaps: int = 0,
        disconnects: int = 0,
        resyncs: int = 0,
    ) -> MicrostructureSessionSummary:
        if self._closed:
            raise RuntimeError("microstructure session is already closed")
        if min(gaps, disconnects, resyncs) < 0:
            raise ValueError("integrity counters must be non-negative")
        self._finalize_part()
        self._closed = True
        summary = MicrostructureSessionSummary(
            session_id=self.session_id,
            session_path=self.session_path,
            market=self.market_type.value,
            symbol=self.symbol,
            event_count=self._event_count,
            first_event=self._first_event,
            last_event=self._last_event,
            gaps=gaps,
            disconnects=disconnects,
            resyncs=resyncs,
            completeness="COMPLETE" if complete else "INCOMPLETE",
            files=tuple(self._files),
        )
        self._write_manifest(summary)
        return summary

    def _open_part(self) -> None:
        self._part_index += 1
        self._part_path = self.session_path / f"events-{self._part_index:05d}.jsonl.gz.part"
        self._handle = gzip.open(self._part_path, "wt", encoding="utf-8", newline="")
        self._part_events = 0
        self._part_raw_size = 0
        self._part_first = None
        self._part_last = None

    def _finalize_part(self) -> None:
        if self._handle is None or self._part_path is None:
            return
        self._handle.close()
        self._handle = None
        if self._part_events == 0:
            self._part_path.unlink()
            self._part_path = None
            return
        final_path = self._part_path.with_suffix("")
        self._part_path.replace(final_path)
        payload = final_path.read_bytes()
        if self._part_first is None or self._part_last is None:
            raise RuntimeError("non-empty event part lost timestamps")
        self._files.append(
            MicrostructureFileMetadata(
                path=final_path.name,
                event_count=self._part_events,
                first_event=self._part_first,
                last_event=self._part_last,
                file_hash=hashlib.sha256(payload).hexdigest(),
                raw_size=self._part_raw_size,
                compressed_size=len(payload),
            )
        )
        self._part_path = None

    def _write_manifest(self, summary: MicrostructureSessionSummary) -> None:
        payload = {
            "session_id": summary.session_id,
            "market": summary.market,
            "symbol": summary.symbol,
            "started_at": self.started_at.isoformat(),
            "ended_at": datetime.now(tz=UTC).isoformat(),
            "event_count": summary.event_count,
            "first_event": summary.first_event,
            "last_event": summary.last_event,
            "gaps": summary.gaps,
            "disconnects": summary.disconnects,
            "resyncs": summary.resyncs,
            "completeness": summary.completeness,
            "credentials_persisted": False,
            "requested_duration_seconds": self.requested_duration_seconds,
            "software_commit": self.provenance.software_commit,
            "dirty_worktree": self.provenance.dirty_worktree,
            "branch": self.provenance.branch,
            "recorder_version": self.provenance.recorder_version,
            "recorder_config_hash": self.provenance.recorder_config_hash,
            "provenance_status": self.provenance.status,
            "parser_errors": self._parser_errors,
            "subscription_manifest": [
                {
                    "requested_stream": item.requested_stream,
                    "canonical_stream": item.canonical_stream,
                    "route": item.route,
                    "connection_id": item.connection_id,
                    "url": item.url,
                }
                for item in self.subscriptions
            ],
            "stream_delivery": [
                self._delivery[name] for name in sorted(self._delivery)
            ],
            "stream_liveness": self._liveness_summary,
            "liveness_config": self._liveness_config,
            "liveness_incidents": self._liveness_incidents,
            "recorder_runtime_health": self._runtime_health,
            "local_processing_latency": self._processing_latency,
            "order_book_status": self._order_book_status,
            "resync_events": self._resync_events,
            "files": [
                {
                    "path": item.path,
                    "event_count": item.event_count,
                    "first_event": item.first_event,
                    "last_event": item.last_event,
                    "file_hash": item.file_hash,
                    "raw_size": item.raw_size,
                    "compressed_size": item.compressed_size,
                }
                for item in summary.files
            ],
        }
        (self.session_path / "manifest.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _record_delivery(self, event: MicrostructureEvent) -> None:
        event_names = {
            MicrostructureStreamType.AGG_TRADE: "aggTrade",
            MicrostructureStreamType.BOOK_TICKER: "bookTicker",
            MicrostructureStreamType.DEPTH_UPDATE: "depth",
            MicrostructureStreamType.MARK_PRICE: "markPrice",
        }
        event_name = event_names.get(event.stream_type)
        if event_name is None:
            return
        candidates = (
            item
            for item in self.subscriptions
            if item.requested_stream == event_name
            or item.requested_stream.startswith(f"{event_name}@")
        )
        subscription = next(candidates, None)
        if subscription is None:
            return
        delivery = self._delivery[subscription.requested_stream]
        current_count = delivery["event_count"]
        if not isinstance(current_count, int):
            raise RuntimeError("stream delivery count lost integer type")
        delivery["event_count"] = current_count + 1
        exchange_time = event.exchange_event_time.isoformat()
        receive_time = event.receive_wall_time.isoformat()
        delivery["first_exchange_event_time"] = (
            delivery["first_exchange_event_time"] or exchange_time
        )
        delivery["last_exchange_event_time"] = exchange_time
        delivery["first_receive_wall_time"] = (
            delivery["first_receive_wall_time"] or receive_time
        )
        delivery["last_receive_wall_time"] = receive_time
        delivery["first_connection_sequence"] = (
            delivery["first_connection_sequence"]
            if delivery["first_connection_sequence"] is not None
            else event.connection_sequence
        )
        delivery["last_connection_sequence"] = event.connection_sequence


def inspect_session(session_path: Path) -> dict[str, object]:
    manifest_path = session_path / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid microstructure session: {session_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("microstructure manifest must be an object")
    files = payload.get("files")
    if not isinstance(files, list):
        raise ValueError("microstructure manifest files must be an array")
    hashes_valid = True
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("microstructure file metadata must be an object")
        relative = item.get("path")
        expected = item.get("file_hash")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ValueError("microstructure file metadata is incomplete")
        path = session_path / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            hashes_valid = False
    return {**payload, "hashes_valid": hashes_valid}


def recover_incomplete_session(session_path: Path) -> tuple[Path, ...]:
    """Make readable gzip crash remnants replayable without claiming completeness."""

    recovered: list[Path] = []
    for part in sorted(session_path.glob("*.jsonl.gz.part")):
        try:
            with gzip.open(part, "rt", encoding="utf-8") as handle:
                for line in handle:
                    json.loads(line)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"corrupt incomplete microstructure file: {part}") from exc
        target = part.with_suffix("")
        part.replace(target)
        recovered.append(target)
    return tuple(recovered)
