"""Lossless parsers for documented Binance public microstructure payloads."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.models import (
    AggressiveSide,
    DepthLevel,
    MicrostructureEvent,
    MicrostructureStreamType,
)


class InvalidMicrostructurePayload(ValueError):
    """Raised when a public payload cannot satisfy the immutable event contract."""


def canonical_payload(payload: object) -> str:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise InvalidMicrostructurePayload("payload is not JSON serializable") from exc


def parse_public_event(
    payload: object,
    *,
    market_type: MarketType,
    receive_wall_time: datetime,
    receive_monotonic_ns: int,
    expected_symbol: str | None = None,
) -> MicrostructureEvent:
    """Parse one raw or combined public-stream message using Decimal values."""

    if receive_wall_time.tzinfo is None or receive_wall_time.utcoffset() is None:
        raise InvalidMicrostructurePayload("receive_wall_time must be timezone-aware")
    if not isinstance(payload, dict):
        raise InvalidMicrostructurePayload("public stream payload must be an object")
    raw_outer = payload
    stream_name: str | None = None
    if "stream" in payload and "data" in payload:
        stream_name = _string(payload, "stream")
        nested = payload["data"]
        if not isinstance(nested, dict):
            raise InvalidMicrostructurePayload("combined stream data must be an object")
        payload = nested

    raw_json = canonical_payload(raw_outer)
    raw_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
    event_name = payload.get("e")
    stream_type = _stream_type(event_name, stream_name, payload)
    symbol = _symbol(payload, stream_name, expected_symbol)
    event_ms = _integer(payload.get("E"), "E", default=_milliseconds(receive_wall_time))
    transaction_ms = _optional_integer(payload.get("T"), "T")
    common: dict[str, Any] = {
        "event_id": hashlib.sha256(
            f"{market_type.value}|{raw_hash}|{receive_monotonic_ns}".encode()
        ).hexdigest(),
        "exchange": "BINANCE",
        "market_type": market_type,
        "symbol": symbol,
        "stream_type": stream_type,
        "exchange_event_time": _datetime_ms(event_ms, "E"),
        "exchange_transaction_time": (
            _datetime_ms(transaction_ms, "T") if transaction_ms is not None else None
        ),
        "receive_wall_time": receive_wall_time.astimezone(UTC),
        "receive_monotonic_ns": receive_monotonic_ns,
        "raw_payload_hash": raw_hash,
        "raw_payload_json": raw_json,
    }
    if stream_type is MicrostructureStreamType.AGG_TRADE:
        buyer_is_maker = _boolean(payload, "m")
        return MicrostructureEvent(
            **common,
            sequence_first=None,
            sequence_last=None,
            sequence_previous=None,
            trade_id=_integer(payload.get("a"), "a"),
            price=_decimal(payload, "p"),
            quantity=_decimal(payload, "q"),
            buyer_is_maker=buyer_is_maker,
            aggressive_side=(
                AggressiveSide.SELL if buyer_is_maker else AggressiveSide.BUY
            ),
        )
    if stream_type is MicrostructureStreamType.BOOK_TICKER:
        update_id = _integer(payload.get("u"), "u")
        return MicrostructureEvent(
            **common,
            sequence_first=update_id,
            sequence_last=update_id,
            sequence_previous=None,
            best_bid=_decimal(payload, "b"),
            best_bid_quantity=_decimal(payload, "B"),
            best_ask=_decimal(payload, "a"),
            best_ask_quantity=_decimal(payload, "A"),
        )
    if stream_type is MicrostructureStreamType.DEPTH_UPDATE:
        return MicrostructureEvent(
            **common,
            sequence_first=_integer(payload.get("U"), "U"),
            sequence_last=_integer(payload.get("u"), "u"),
            sequence_previous=_optional_integer(payload.get("pu"), "pu"),
            bids=_levels(payload.get("b"), "b"),
            asks=_levels(payload.get("a"), "a"),
        )
    if stream_type is MicrostructureStreamType.MARK_PRICE:
        if market_type is not MarketType.USD_M_FUTURES:
            raise InvalidMicrostructurePayload("mark price is Futures-only")
        return MicrostructureEvent(
            **common,
            sequence_first=None,
            sequence_last=None,
            sequence_previous=None,
            mark_price=_decimal(payload, "p"),
        )
    raise InvalidMicrostructurePayload(f"unsupported stream payload: {stream_type.value}")


def parse_depth_snapshot(
    payload: object,
    *,
    market_type: MarketType,
    symbol: str,
    receive_wall_time: datetime,
    receive_monotonic_ns: int,
) -> MicrostructureEvent:
    if not isinstance(payload, dict):
        raise InvalidMicrostructurePayload("depth snapshot must be an object")
    raw_json = canonical_payload(payload)
    raw_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
    update_id = _integer(payload.get("lastUpdateId"), "lastUpdateId")
    normalized = symbol.strip().upper()
    return MicrostructureEvent(
        event_id=hashlib.sha256(
            f"{market_type.value}|SNAPSHOT|{raw_hash}|{receive_monotonic_ns}".encode()
        ).hexdigest(),
        exchange="BINANCE",
        market_type=market_type,
        symbol=normalized,
        stream_type=MicrostructureStreamType.SNAPSHOT,
        exchange_event_time=receive_wall_time.astimezone(UTC),
        exchange_transaction_time=None,
        receive_wall_time=receive_wall_time.astimezone(UTC),
        receive_monotonic_ns=receive_monotonic_ns,
        sequence_first=update_id,
        sequence_last=update_id,
        sequence_previous=None,
        raw_payload_hash=raw_hash,
        raw_payload_json=raw_json,
        bids=_levels(payload.get("bids"), "bids"),
        asks=_levels(payload.get("asks"), "asks"),
    )


def connection_state_event(
    *,
    market_type: MarketType,
    symbol: str,
    state: str,
    timestamp: datetime,
    monotonic_ns: int,
) -> MicrostructureEvent:
    payload = {"state": state, "symbol": symbol, "market": market_type.value}
    raw_json = canonical_payload(payload)
    raw_hash = hashlib.sha256(raw_json.encode()).hexdigest()
    return MicrostructureEvent(
        event_id=hashlib.sha256(
            f"CONNECTION|{raw_hash}|{monotonic_ns}".encode()
        ).hexdigest(),
        exchange="BINANCE",
        market_type=market_type,
        symbol=symbol.upper(),
        stream_type=MicrostructureStreamType.CONNECTION_STATE,
        exchange_event_time=timestamp.astimezone(UTC),
        exchange_transaction_time=None,
        receive_wall_time=timestamp.astimezone(UTC),
        receive_monotonic_ns=monotonic_ns,
        sequence_first=None,
        sequence_last=None,
        sequence_previous=None,
        raw_payload_hash=raw_hash,
        raw_payload_json=raw_json,
        connection_state=state,
    )


def _stream_type(
    event_name: object,
    stream_name: str | None,
    payload: dict[str, object],
) -> MicrostructureStreamType:
    if event_name == "aggTrade":
        return MicrostructureStreamType.AGG_TRADE
    if event_name == "depthUpdate":
        return MicrostructureStreamType.DEPTH_UPDATE
    if event_name == "markPriceUpdate":
        return MicrostructureStreamType.MARK_PRICE
    if event_name == "bookTicker" or {"u", "b", "B", "a", "A"} <= payload.keys():
        return MicrostructureStreamType.BOOK_TICKER
    if stream_name:
        if "@aggTrade" in stream_name:
            return MicrostructureStreamType.AGG_TRADE
        if "@bookTicker" in stream_name:
            return MicrostructureStreamType.BOOK_TICKER
        if "@depth" in stream_name:
            return MicrostructureStreamType.DEPTH_UPDATE
        if "@markPrice" in stream_name:
            return MicrostructureStreamType.MARK_PRICE
    raise InvalidMicrostructurePayload("unknown public stream payload")


def _symbol(
    payload: dict[str, object],
    stream_name: str | None,
    expected_symbol: str | None,
) -> str:
    value = payload.get("s")
    if isinstance(value, str) and value:
        symbol = value.upper()
    elif stream_name and "@" in stream_name:
        symbol = stream_name.split("@", 1)[0].upper()
    elif expected_symbol:
        symbol = expected_symbol.upper()
    else:
        raise InvalidMicrostructurePayload("payload does not identify a symbol")
    if expected_symbol is not None and symbol != expected_symbol.upper():
        raise InvalidMicrostructurePayload("payload symbol differs from expected symbol")
    return symbol


def _levels(value: object, name: str) -> tuple[DepthLevel, ...]:
    if not isinstance(value, list):
        raise InvalidMicrostructurePayload(f"{name} must be an array")
    result: list[DepthLevel] = []
    try:
        for item in value:
            if not isinstance(item, list) or len(item) < 2:
                raise InvalidMicrostructurePayload(f"{name} level is invalid")
            result.append(DepthLevel(Decimal(str(item[0])), Decimal(str(item[1]))))
    except (InvalidOperation, TypeError, ValueError) as exc:
        if isinstance(exc, InvalidMicrostructurePayload):
            raise
        raise InvalidMicrostructurePayload(f"{name} contains invalid Decimal data") from exc
    return tuple(result)


def _decimal(payload: dict[str, object], name: str) -> Decimal:
    try:
        value = Decimal(str(payload[name]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidMicrostructurePayload(f"{name} must be a Decimal") from exc
    if not value.is_finite() or value <= 0:
        raise InvalidMicrostructurePayload(f"{name} must be a positive finite Decimal")
    return value


def _integer(value: object, name: str, *, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str, bytes, bytearray)):
        raise InvalidMicrostructurePayload(f"{name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidMicrostructurePayload(f"{name} must be an integer") from exc
    if parsed < 0:
        raise InvalidMicrostructurePayload(f"{name} must be non-negative")
    return parsed


def _optional_integer(value: object, name: str) -> int | None:
    return None if value is None else _integer(value, name)


def _boolean(payload: dict[str, object], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise InvalidMicrostructurePayload(f"{name} must be a boolean")
    return value


def _string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise InvalidMicrostructurePayload(f"{name} must be a non-empty string")
    return value


def _datetime_ms(value: int, name: str) -> datetime:
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError) as exc:
        raise InvalidMicrostructurePayload(f"{name} timestamp is out of range") from exc


def _milliseconds(value: datetime) -> int:
    milliseconds = value.astimezone(UTC).timestamp() * 1000
    return int(milliseconds)
