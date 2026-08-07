"""Deterministic microstructure replay with event-time virtual clocks."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from adaptive_trader.microstructure.codec import event_from_record
from adaptive_trader.microstructure.models import MicrostructureEvent
from adaptive_trader.microstructure.storage import inspect_session


class ReplaySpeed(StrEnum):
    ONE_X = "1x"
    MAX = "max"
    STEP = "step"


class VirtualClock:
    def __init__(self) -> None:
        self._now: datetime | None = None

    @property
    def now(self) -> datetime:
        if self._now is None:
            raise RuntimeError("virtual clock has not observed an event")
        return self._now

    def advance_to(self, timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("virtual timestamp must be timezone-aware")
        if self._now is not None and timestamp < self._now:
            raise ValueError("virtual clock cannot move backwards")
        self._now = timestamp
        return timestamp


@dataclass(frozen=True, slots=True)
class ReplayResult:
    session_path: str
    speed: str
    seed: int
    event_count: int
    first_event: str | None
    last_event: str | None
    input_hash: str
    output_hash: str
    deterministic: bool
    real_sleep_used: bool = False


ReplayHandler = Callable[[MicrostructureEvent, VirtualClock], str | None]


class MicrostructureReplayEngine:
    def __init__(self, *, seed: int = 42) -> None:
        if seed < 0:
            raise ValueError("replay seed must be non-negative")
        self.seed = seed

    def load_events(self, session_path: Path) -> tuple[MicrostructureEvent, ...]:
        manifest = inspect_session(session_path)
        if not manifest["hashes_valid"]:
            raise ValueError("microstructure session hash mismatch")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise ValueError("microstructure manifest files must be an array")
        events: list[MicrostructureEvent] = []
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("microstructure manifest file entry is invalid")
            path = session_path / item["path"]
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        events.append(event_from_record(json.loads(line)))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"corrupt microstructure replay file: {path}") from exc
        return tuple(sorted(events, key=self._event_order))

    def steps(self, session_path: Path) -> Iterator[tuple[MicrostructureEvent, VirtualClock]]:
        clock = VirtualClock()
        for event in self.load_events(session_path):
            clock.advance_to(event.exchange_event_time)
            yield event, clock

    def run(
        self,
        session_path: Path,
        *,
        speed: ReplaySpeed = ReplaySpeed.MAX,
        handler: ReplayHandler | None = None,
    ) -> ReplayResult:
        events = self.load_events(session_path)
        clock = VirtualClock()
        outputs: list[str] = []
        for event in events:
            clock.advance_to(event.exchange_event_time)
            if handler is not None:
                output = handler(event, clock)
                if output is not None:
                    outputs.append(output)
        input_payload = "\n".join(event.event_id for event in events).encode()
        output_payload = "\n".join(outputs).encode()
        return ReplayResult(
            session_path=str(session_path),
            speed=speed.value,
            seed=self.seed,
            event_count=len(events),
            first_event=events[0].exchange_event_time.isoformat() if events else None,
            last_event=events[-1].exchange_event_time.isoformat() if events else None,
            input_hash=hashlib.sha256(input_payload).hexdigest(),
            output_hash=hashlib.sha256(output_payload).hexdigest(),
            deterministic=True,
        )

    @staticmethod
    def _event_order(
        event: MicrostructureEvent,
    ) -> tuple[datetime, int, int, int, str]:
        maximum = 2**63 - 1
        return (
            event.exchange_event_time,
            event.sequence_first if event.sequence_first is not None else maximum,
            event.sequence_last if event.sequence_last is not None else maximum,
            event.receive_monotonic_ns,
            event.event_id,
        )
