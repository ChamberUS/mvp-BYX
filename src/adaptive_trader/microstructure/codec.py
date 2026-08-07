"""Canonical event codec shared by append-only storage and deterministic replay."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.models import (
    AggressiveSide,
    DepthLevel,
    MicrostructureEvent,
    MicrostructureStreamType,
)


def event_to_record(event: MicrostructureEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "exchange": event.exchange,
        "market_type": event.market_type.value,
        "symbol": event.symbol,
        "stream_type": event.stream_type.value,
        "exchange_event_time": event.exchange_event_time.isoformat(),
        "exchange_transaction_time": (
            event.exchange_transaction_time.isoformat()
            if event.exchange_transaction_time is not None
            else None
        ),
        "receive_wall_time": event.receive_wall_time.isoformat(),
        "receive_monotonic_ns": event.receive_monotonic_ns,
        "connection_id": event.connection_id,
        "connection_sequence": event.connection_sequence,
        "sequence_first": event.sequence_first,
        "sequence_last": event.sequence_last,
        "sequence_previous": event.sequence_previous,
        "raw_payload_hash": event.raw_payload_hash,
        "raw_payload_json": event.raw_payload_json,
        "trade_id": event.trade_id,
        "first_trade_id": event.first_trade_id,
        "last_trade_id": event.last_trade_id,
        "price": _decimal_text(event.price),
        "quantity": _decimal_text(event.quantity),
        "buyer_is_maker": event.buyer_is_maker,
        "aggressive_side": (
            event.aggressive_side.value if event.aggressive_side is not None else None
        ),
        "best_bid": _decimal_text(event.best_bid),
        "best_bid_quantity": _decimal_text(event.best_bid_quantity),
        "best_ask": _decimal_text(event.best_ask),
        "best_ask_quantity": _decimal_text(event.best_ask_quantity),
        "bids": [[str(level.price), str(level.quantity)] for level in event.bids],
        "asks": [[str(level.price), str(level.quantity)] for level in event.asks],
        "mark_price": _decimal_text(event.mark_price),
        "index_price": _decimal_text(event.index_price),
        "funding_rate": _decimal_text(event.funding_rate),
        "next_funding_time": (
            event.next_funding_time.isoformat()
            if event.next_funding_time is not None
            else None
        ),
        "connection_state": event.connection_state,
    }


def event_record_json(event: MicrostructureEvent) -> str:
    return json.dumps(event_to_record(event), sort_keys=True, separators=(",", ":"))


def event_from_record(record: object) -> MicrostructureEvent:
    if not isinstance(record, dict):
        raise ValueError("microstructure record must be an object")
    return MicrostructureEvent(
        event_id=_string(record, "event_id"),
        exchange=_string(record, "exchange"),
        market_type=MarketType(_string(record, "market_type")),
        symbol=_string(record, "symbol"),
        stream_type=MicrostructureStreamType(_string(record, "stream_type")),
        exchange_event_time=_datetime(record, "exchange_event_time"),
        exchange_transaction_time=_optional_datetime(record, "exchange_transaction_time"),
        receive_wall_time=_datetime(record, "receive_wall_time"),
        receive_monotonic_ns=_integer(record, "receive_monotonic_ns"),
        connection_id=_optional_string(record, "connection_id") or "legacy-public-1",
        connection_sequence=(
            _integer(record, "connection_sequence")
            if record.get("connection_sequence") is not None
            else 0
        ),
        sequence_first=_optional_integer(record, "sequence_first"),
        sequence_last=_optional_integer(record, "sequence_last"),
        sequence_previous=_optional_integer(record, "sequence_previous"),
        raw_payload_hash=_string(record, "raw_payload_hash"),
        raw_payload_json=_string(record, "raw_payload_json"),
        trade_id=_optional_integer(record, "trade_id"),
        first_trade_id=_optional_integer(record, "first_trade_id"),
        last_trade_id=_optional_integer(record, "last_trade_id"),
        price=_optional_decimal(record, "price"),
        quantity=_optional_decimal(record, "quantity"),
        buyer_is_maker=_optional_boolean(record, "buyer_is_maker"),
        aggressive_side=(
            AggressiveSide(value)
            if (value := record.get("aggressive_side")) is not None
            and isinstance(value, str)
            else None
        ),
        best_bid=_optional_decimal(record, "best_bid"),
        best_bid_quantity=_optional_decimal(record, "best_bid_quantity"),
        best_ask=_optional_decimal(record, "best_ask"),
        best_ask_quantity=_optional_decimal(record, "best_ask_quantity"),
        bids=_levels(record, "bids"),
        asks=_levels(record, "asks"),
        mark_price=_optional_decimal(record, "mark_price"),
        index_price=_optional_decimal(record, "index_price"),
        funding_rate=_optional_decimal(record, "funding_rate"),
        next_funding_time=_optional_datetime(record, "next_funding_time"),
        connection_state=_optional_string(record, "connection_state"),
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _string(record: dict[object, object], name: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"record {name} must be a non-empty string")
    return value


def _optional_string(record: dict[object, object], name: str) -> str | None:
    value = record.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"record {name} must be a string")
    return value


def _integer(record: dict[object, object], name: str) -> int:
    value = record.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"record {name} must be an integer")
    return value


def _optional_integer(record: dict[object, object], name: str) -> int | None:
    return None if record.get(name) is None else _integer(record, name)


def _optional_boolean(record: dict[object, object], name: str) -> bool | None:
    value = record.get(name)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"record {name} must be a boolean")
    return value


def _datetime(record: dict[object, object], name: str) -> datetime:
    try:
        value = datetime.fromisoformat(_string(record, name))
    except ValueError as exc:
        raise ValueError(f"record {name} must be an ISO datetime") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"record {name} must be timezone-aware")
    return value


def _optional_datetime(record: dict[object, object], name: str) -> datetime | None:
    return None if record.get(name) is None else _datetime(record, name)


def _optional_decimal(record: dict[object, object], name: str) -> Decimal | None:
    value = record.get(name)
    return Decimal(value) if isinstance(value, str) else None


def _levels(record: dict[object, object], name: str) -> tuple[DepthLevel, ...]:
    value = record.get(name)
    if not isinstance(value, list):
        raise ValueError(f"record {name} must be an array")
    levels: list[DepthLevel] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError(f"record {name} contains invalid level")
        levels.append(DepthLevel(Decimal(str(item[0])), Decimal(str(item[1]))))
    return tuple(levels)
