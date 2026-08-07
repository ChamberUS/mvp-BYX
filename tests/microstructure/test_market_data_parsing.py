from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.models import AggressiveSide, MicrostructureStreamType
from adaptive_trader.microstructure.parsing import (
    InvalidMicrostructurePayload,
    canonical_payload,
    connection_state_event,
    parse_depth_snapshot,
    parse_public_event,
)
from tests.microstructure.helpers import at, depth_event, snapshot_event, trade_event


@pytest.mark.parametrize("market", [MarketType.SPOT, MarketType.USD_M_FUTURES])
@pytest.mark.parametrize(
    ("buyer_is_maker", "expected"),
    [(False, AggressiveSide.BUY), (True, AggressiveSide.SELL)],
)
def test_aggregate_trade_decimal_timestamp_and_aggressor_convention(
    market: MarketType,
    buyer_is_maker: bool,
    expected: AggressiveSide,
) -> None:
    event = trade_event(
        market=market,
        milliseconds=123,
        buyer_is_maker=buyer_is_maker,
        price="2000.12345678",
        quantity="0.00000009",
    )

    assert event.stream_type is MicrostructureStreamType.AGG_TRADE
    assert event.price == Decimal("2000.12345678")
    assert event.quantity == Decimal("0.00000009")
    assert event.aggressive_side is expected
    assert event.exchange_event_time == at(123)
    assert event.exchange_transaction_time == at(123)
    assert len(event.raw_payload_hash) == 64
    with pytest.raises(FrozenInstanceError):
        event.symbol = "BTCUSDT"


def test_combined_book_ticker_uses_stream_symbol_and_receive_time_fallback() -> None:
    event = parse_public_event(
        {
            "stream": "ethusdt@bookTicker",
            "data": {"u": 12, "b": "2000", "B": "2", "a": "2001", "A": "3"},
        },
        market_type=MarketType.SPOT,
        receive_wall_time=at(500),
        receive_monotonic_ns=42,
    )

    assert event.stream_type is MicrostructureStreamType.BOOK_TICKER
    assert event.symbol == "ETHUSDT"
    assert event.best_bid == Decimal("2000")
    assert event.best_ask_quantity == Decimal("3")
    assert event.sequence_first == event.sequence_last == 12
    assert event.exchange_event_time == at(500)


def test_spot_and_futures_depth_sequences_are_preserved() -> None:
    spot = depth_event(first=101, last=103)
    futures = depth_event(
        market=MarketType.USD_M_FUTURES,
        first=201,
        last=203,
        previous=200,
    )

    assert spot.bids == () and spot.sequence_previous is None
    assert futures.sequence_first == 201
    assert futures.sequence_last == 203
    assert futures.sequence_previous == 200


def test_mark_price_is_futures_only_and_combined_name_is_supported() -> None:
    payload = {
        "stream": "ethusdt@markPrice@1s",
        "data": {
            "e": "markPriceUpdate",
            "E": int(at().timestamp() * 1000),
            "s": "ETHUSDT",
            "p": "2000.50",
        },
    }
    event = parse_public_event(
        payload,
        market_type=MarketType.USD_M_FUTURES,
        receive_wall_time=at(),
        receive_monotonic_ns=3,
    )
    assert event.stream_type is MicrostructureStreamType.MARK_PRICE
    assert event.mark_price == Decimal("2000.50")

    with pytest.raises(InvalidMicrostructurePayload, match="Futures-only"):
        parse_public_event(
            payload,
            market_type=MarketType.SPOT,
            receive_wall_time=at(),
            receive_monotonic_ns=3,
        )


def test_snapshot_and_connection_state_have_canonical_public_payload() -> None:
    snapshot = snapshot_event(update_id=999)
    connection = connection_state_event(
        market_type=MarketType.SPOT,
        symbol="ethusdt",
        state="CONNECTED",
        timestamp=at(),
        monotonic_ns=7,
    )

    assert snapshot.stream_type is MicrostructureStreamType.SNAPSHOT
    assert snapshot.sequence_last == 999
    assert len(snapshot.bids) == 25
    assert connection.stream_type is MicrostructureStreamType.CONNECTION_STATE
    assert connection.connection_state == "CONNECTED"
    assert connection.symbol == "ETHUSDT"
    assert canonical_payload({"b": 1, "a": 2}) == '{"a":2,"b":1}'


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "must be an object"),
        ({"e": "unknown", "s": "ETHUSDT"}, "unknown public stream"),
        ({"stream": "ethusdt@aggTrade", "data": []}, "data must be an object"),
        ({"e": "aggTrade", "s": "ETHUSDT", "a": 1, "p": "1", "q": "1"}, "boolean"),
        (
            {"e": "aggTrade", "s": "BTCUSDT", "a": 1, "p": "1", "q": "1", "m": True},
            "differs from expected",
        ),
        (
            {"e": "aggTrade", "s": "ETHUSDT", "a": True, "p": "1", "q": "1", "m": True},
            "must be an integer",
        ),
        (
            {"e": "aggTrade", "s": "ETHUSDT", "a": 1, "p": "nan", "q": "1", "m": True},
            "positive finite",
        ),
    ],
)
def test_unknown_and_invalid_public_payloads_fail_closed(
    payload: object,
    message: str,
) -> None:
    with pytest.raises(InvalidMicrostructurePayload, match=message):
        parse_public_event(
            payload,
            market_type=MarketType.SPOT,
            receive_wall_time=at(),
            receive_monotonic_ns=1,
            expected_symbol="ETHUSDT",
        )


def test_invalid_wall_time_snapshot_levels_and_non_json_payload_are_rejected() -> None:
    with pytest.raises(InvalidMicrostructurePayload, match="timezone-aware"):
        parse_public_event(
            {"e": "aggTrade"},
            market_type=MarketType.SPOT,
            receive_wall_time=datetime(2026, 1, 1),
            receive_monotonic_ns=1,
        )
    with pytest.raises(InvalidMicrostructurePayload, match="depth snapshot"):
        parse_depth_snapshot(
            [],
            market_type=MarketType.SPOT,
            symbol="ETHUSDT",
            receive_wall_time=at(),
            receive_monotonic_ns=1,
        )
    with pytest.raises(InvalidMicrostructurePayload, match="level is invalid"):
        parse_depth_snapshot(
            {"lastUpdateId": 1, "bids": [["1"]], "asks": []},
            market_type=MarketType.SPOT,
            symbol="ETHUSDT",
            receive_wall_time=at(),
            receive_monotonic_ns=1,
        )
    with pytest.raises(InvalidMicrostructurePayload, match="not JSON serializable"):
        canonical_payload({"bad": object()})
