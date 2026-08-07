"""Exchange-specific depth sequencing policies and explicit gap classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from adaptive_trader.microstructure.models import MicrostructureEvent


class GapClassification(StrEnum):
    REAL_SEQUENCE_GAP = "REAL_SEQUENCE_GAP"
    SNAPSHOT_ALIGNMENT_RETRY = "SNAPSHOT_ALIGNMENT_RETRY"
    OLD_EVENT = "OLD_EVENT"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    OUT_OF_ORDER_EVENT = "OUT_OF_ORDER_EVENT"
    STALE_EVENT = "STALE_EVENT"
    CONNECTION_RESTART = "CONNECTION_RESTART"
    PARSER_ERROR = "PARSER_ERROR"


@dataclass(frozen=True, slots=True)
class SequenceDecision:
    accepted: bool
    classification: GapClassification | None
    previous_update_id: int
    sequence_first: int | None
    sequence_last: int | None
    sequence_previous: int | None
    detail: str


class DepthSequencePolicy(Protocol):
    name: str

    def bootstrap(
        self,
        snapshot_update_id: int,
        event: MicrostructureEvent,
    ) -> SequenceDecision: ...

    def next_event(
        self,
        previous_update_id: int,
        event: MicrostructureEvent,
    ) -> SequenceDecision: ...


class SpotSequencePolicy:
    name = "SPOT_U_CONTAINS_PREVIOUS_PLUS_ONE"

    def bootstrap(
        self,
        snapshot_update_id: int,
        event: MicrostructureEvent,
    ) -> SequenceDecision:
        first, last = _ids(event)
        expected = snapshot_update_id + 1
        if last < expected:
            return _decision(False, GapClassification.OLD_EVENT, snapshot_update_id, event)
        if first <= expected <= last:
            return _decision(True, None, snapshot_update_id, event)
        return _decision(
            False,
            GapClassification.SNAPSHOT_ALIGNMENT_RETRY,
            snapshot_update_id,
            event,
        )

    def next_event(
        self,
        previous_update_id: int,
        event: MicrostructureEvent,
    ) -> SequenceDecision:
        first, last = _ids(event)
        if last == previous_update_id:
            return _decision(
                False,
                GapClassification.DUPLICATE_EVENT,
                previous_update_id,
                event,
            )
        if last < previous_update_id:
            return _decision(
                False,
                GapClassification.OUT_OF_ORDER_EVENT,
                previous_update_id,
                event,
            )
        expected = previous_update_id + 1
        if first <= expected <= last:
            return _decision(True, None, previous_update_id, event)
        return _decision(
            False,
            GapClassification.REAL_SEQUENCE_GAP,
            previous_update_id,
            event,
        )


class FuturesSequencePolicy:
    name = "USD_M_FUTURES_PU_CHAIN"

    def bootstrap(
        self,
        snapshot_update_id: int,
        event: MicrostructureEvent,
    ) -> SequenceDecision:
        first, last = _ids(event)
        if last < snapshot_update_id:
            return _decision(False, GapClassification.OLD_EVENT, snapshot_update_id, event)
        if first <= snapshot_update_id <= last:
            return _decision(True, None, snapshot_update_id, event)
        return _decision(
            False,
            GapClassification.SNAPSHOT_ALIGNMENT_RETRY,
            snapshot_update_id,
            event,
        )

    def next_event(
        self,
        previous_update_id: int,
        event: MicrostructureEvent,
    ) -> SequenceDecision:
        _, last = _ids(event)
        if last == previous_update_id:
            return _decision(
                False,
                GapClassification.DUPLICATE_EVENT,
                previous_update_id,
                event,
            )
        if last < previous_update_id:
            return _decision(
                False,
                GapClassification.OUT_OF_ORDER_EVENT,
                previous_update_id,
                event,
            )
        if event.sequence_previous is None:
            return _decision(
                False,
                GapClassification.PARSER_ERROR,
                previous_update_id,
                event,
            )
        if event.sequence_previous == previous_update_id:
            return _decision(True, None, previous_update_id, event)
        classification = (
            GapClassification.OUT_OF_ORDER_EVENT
            if event.sequence_previous < previous_update_id
            else GapClassification.REAL_SEQUENCE_GAP
        )
        return _decision(False, classification, previous_update_id, event)


def _ids(event: MicrostructureEvent) -> tuple[int, int]:
    if event.sequence_first is None or event.sequence_last is None:
        raise ValueError("depth event requires first and last update IDs")
    return event.sequence_first, event.sequence_last


def _decision(
    accepted: bool,
    classification: GapClassification | None,
    previous_update_id: int,
    event: MicrostructureEvent,
) -> SequenceDecision:
    return SequenceDecision(
        accepted=accepted,
        classification=classification,
        previous_update_id=previous_update_id,
        sequence_first=event.sequence_first,
        sequence_last=event.sequence_last,
        sequence_previous=event.sequence_previous,
        detail=(
            "accepted"
            if classification is None
            else f"{classification.value}: previous={previous_update_id}, "
            f"U={event.sequence_first}, u={event.sequence_last}, pu={event.sequence_previous}"
        ),
    )
