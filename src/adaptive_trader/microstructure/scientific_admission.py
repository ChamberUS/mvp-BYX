"""Conservative scientific admission for independently captured public sessions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from adaptive_trader.microstructure.replay import MicrostructureReplayEngine
from adaptive_trader.microstructure.storage import inspect_session

REQUIRED_STREAMS = frozenset({"aggTrade", "bookTicker", "depth", "markPrice"})


@dataclass(frozen=True, slots=True)
class SessionAdmission:
    session_id: str
    path: str
    admitted: bool
    status: str
    reasons: tuple[str, ...]
    duration_seconds: float
    event_count: int
    replay_status: str
    replay_hash: str | None
    software_commit: str
    dirty_worktree: bool | None
    branch: str
    recorder_version: str
    recorder_config_hash: str

    def as_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "path": self.path,
            "admitted": self.admitted,
            "status": self.status,
            "reasons": "|".join(self.reasons),
            "duration_seconds": self.duration_seconds,
            "event_count": self.event_count,
            "replay_status": self.replay_status,
            "replay_hash": self.replay_hash,
            "software_commit": self.software_commit,
            "dirty_worktree": self.dirty_worktree,
            "branch": self.branch,
            "recorder_version": self.recorder_version,
            "recorder_config_hash": self.recorder_config_hash,
        }


def qualify_session(
    session_path: Path, *, expected_market: str, expected_symbol: str
) -> SessionAdmission:
    manifest = inspect_session(session_path)
    reasons: list[str] = []
    _require(manifest.get("completeness") == "COMPLETE", "INCOMPLETE", reasons)
    _require(manifest.get("hashes_valid") is True, "EVENT_HASH_INVALID", reasons)
    _require(manifest.get("market") == expected_market, "WRONG_MARKET", reasons)
    _require(manifest.get("symbol") == expected_symbol, "WRONG_SYMBOL", reasons)
    _require(manifest.get("gaps") == 0, "REAL_SEQUENCE_GAP", reasons)
    _require(manifest.get("parser_errors") == 0, "PARSER_CORRUPTION", reasons)
    runtime = manifest.get("recorder_runtime_health", {})
    dropped = runtime.get("dropped_events", 0) if isinstance(runtime, dict) else None
    _require(dropped == 0, "DROPPED_EVENTS", reasons)
    incidents = manifest.get("liveness_incidents", [])
    unresolved = any(
        isinstance(item, dict)
        and (item.get("unresolved") is True or item.get("state") != "RECOVERED")
        for item in incidents
    ) if isinstance(incidents, list) else True
    _require(not unresolved, "UNRESOLVED_INCIDENT", reasons)
    delivery = manifest.get("stream_delivery", [])
    delivered = {
        str(item.get("requested_stream", "")).split("@", 1)[0]
        for item in delivery
        if isinstance(item, dict) and int(item.get("event_count", 0)) > 0
    } if isinstance(delivery, list) else set()
    _require(delivered == REQUIRED_STREAMS, "FOUR_STREAMS_NOT_LIVE", reasons)
    liveness = manifest.get("stream_liveness", {})
    live = isinstance(liveness, dict) and len(liveness) == 4 and all(
        isinstance(item, dict) and item.get("state") == "LIVE"
        for item in liveness.values()
    )
    _require(live, "STREAM_LIVENESS_INVALID", reasons)
    _require(manifest.get("order_book_status") == "SYNCHRONIZED", "BOOK_NOT_SYNCHRONIZED", reasons)

    commit = str(manifest.get("software_commit", "UNKNOWN"))
    dirty = manifest.get("dirty_worktree")
    branch = str(manifest.get("branch", "UNKNOWN"))
    version = str(manifest.get("recorder_version", "UNKNOWN"))
    config_hash = str(manifest.get("recorder_config_hash", "UNKNOWN"))
    provenance_complete = (
        manifest.get("provenance_status") == "COMPLETE"
        and len(commit) == 40
        and dirty is False
        and branch != "UNKNOWN"
        and version != "UNKNOWN"
        and len(config_hash) == 64
    )
    _require(provenance_complete, "PROVENANCE_INCOMPLETE", reasons)
    if dirty is True:
        reasons.append("DIRTY_WORKTREE")

    replay_status = "NOT_RUN"
    replay_hash: str | None = None
    try:
        first = MicrostructureReplayEngine().load_events(session_path)
        second = MicrostructureReplayEngine().load_events(session_path)
        first_ids = "\n".join(item.event_id for item in first).encode()
        second_ids = "\n".join(item.event_id for item in second).encode()
        replay_hash = hashlib.sha256(first_ids).hexdigest()
        replay_status = "DETERMINISTIC" if first_ids == second_ids else "NON_DETERMINISTIC"
    except (OSError, ValueError):
        replay_status = "REPLAY_FAILED"
    _require(replay_status == "DETERMINISTIC", "REPLAY_NOT_DETERMINISTIC", reasons)

    start = _boundary(manifest, "first_exchange_event_time", latest=False)
    end = _boundary(manifest, "last_exchange_event_time", latest=True)
    duration = (
        max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
        if start is not None and end is not None
        else 0.0
    )
    unique_reasons = tuple(dict.fromkeys(reasons))
    return SessionAdmission(
        session_id=str(manifest.get("session_id", session_path.name)),
        path=str(session_path),
        admitted=not unique_reasons,
        status="ADMITTED" if not unique_reasons else "REJECT_FROM_SCIENTIFIC_DATASET",
        reasons=unique_reasons,
        duration_seconds=duration if not unique_reasons else 0.0,
        event_count=_integer(manifest.get("event_count", 0)),
        replay_status=replay_status,
        replay_hash=replay_hash,
        software_commit=commit,
        dirty_worktree=dirty if isinstance(dirty, bool) else None,
        branch=branch,
        recorder_version=version,
        recorder_config_hash=config_hash,
    )


def reject_duplicate_sessions(
    admissions: tuple[SessionAdmission, ...],
) -> tuple[SessionAdmission, ...]:
    seen: set[str] = set()
    result: list[SessionAdmission] = []
    for item in admissions:
        duplicate_key = item.replay_hash or item.session_id
        if duplicate_key in seen:
            reasons = (*item.reasons, "OVERLAPPING_DUPLICATE_SESSION")
            item = replace(
                item,
                admitted=False,
                status="REJECT_FROM_SCIENTIFIC_DATASET",
                reasons=reasons,
                duration_seconds=0.0,
            )
        seen.add(duplicate_key)
        result.append(item)
    return tuple(result)


def _boundary(manifest: dict[str, object], key: str, *, latest: bool) -> str | None:
    delivery = manifest.get("stream_delivery", [])
    values = [
        str(item[key])
        for item in delivery
        if isinstance(item, dict) and item.get(key) is not None
    ] if isinstance(delivery, list) else []
    return (max(values) if latest else min(values)) if values else None


def _require(condition: bool, reason: str, reasons: list[str]) -> None:
    if not condition:
        reasons.append(reason)


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
