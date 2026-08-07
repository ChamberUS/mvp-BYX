from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.features import (
    MicrostructureFeatureEngine,
    MicrostructureFeatureSnapshot,
)
from adaptive_trader.microstructure.models import LiquiditySnapshot, MicrostructureEvent
from adaptive_trader.microstructure.order_book import LocalOrderBook
from adaptive_trader.microstructure.parsing import parse_depth_snapshot, parse_public_event
from adaptive_trader.microstructure.storage import MicrostructureSessionWriter

BASE_TIME = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def at(milliseconds: int = 0) -> datetime:
    return BASE_TIME + timedelta(milliseconds=milliseconds)


def levels(*, bid: bool, count: int = 25, quantity: str = "2") -> list[list[str]]:
    start = Decimal("2000") if bid else Decimal("2000.10")
    direction = Decimal("-0.10") if bid else Decimal("0.10")
    return [
        [str(start + direction * index), str(Decimal(quantity) + Decimal(index) / 10)]
        for index in range(count)
    ]


def snapshot_event(
    *,
    market: MarketType = MarketType.SPOT,
    update_id: int = 100,
    milliseconds: int = 0,
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
) -> MicrostructureEvent:
    return parse_depth_snapshot(
        {
            "lastUpdateId": update_id,
            "bids": bids if bids is not None else levels(bid=True),
            "asks": asks if asks is not None else levels(bid=False),
        },
        market_type=market,
        symbol="ETHUSDT",
        receive_wall_time=at(milliseconds),
        receive_monotonic_ns=1_000_000 + milliseconds,
    )


def depth_event(
    *,
    market: MarketType = MarketType.SPOT,
    first: int = 101,
    last: int = 101,
    previous: int | None = None,
    milliseconds: int = 10,
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
) -> MicrostructureEvent:
    payload: dict[str, object] = {
        "e": "depthUpdate",
        "E": int(at(milliseconds).timestamp() * 1000),
        "T": int(at(milliseconds).timestamp() * 1000),
        "s": "ETHUSDT",
        "U": first,
        "u": last,
        "b": bids if bids is not None else [],
        "a": asks if asks is not None else [],
    }
    if previous is not None:
        payload["pu"] = previous
    return parse_public_event(
        payload,
        market_type=market,
        receive_wall_time=at(milliseconds),
        receive_monotonic_ns=2_000_000 + milliseconds,
        expected_symbol="ETHUSDT",
    )


def trade_event(
    *,
    market: MarketType = MarketType.SPOT,
    milliseconds: int = 0,
    buyer_is_maker: bool = False,
    quantity: str = "1",
    price: str = "2000.05",
    trade_id: int = 1,
) -> MicrostructureEvent:
    return parse_public_event(
        {
            "e": "aggTrade",
            "E": int(at(milliseconds).timestamp() * 1000),
            "T": int(at(milliseconds).timestamp() * 1000),
            "s": "ETHUSDT",
            "a": trade_id,
            "p": price,
            "q": quantity,
            "m": buyer_is_maker,
        },
        market_type=market,
        receive_wall_time=at(milliseconds),
        receive_monotonic_ns=3_000_000 + milliseconds + trade_id,
    )


def synchronized_book(
    *,
    market: MarketType = MarketType.SPOT,
    milliseconds: int = 0,
) -> LocalOrderBook:
    book = LocalOrderBook(market, "ETHUSDT")
    book.apply_snapshot(snapshot_event(market=market, milliseconds=milliseconds))
    return book


def liquidity(
    *,
    market: MarketType = MarketType.SPOT,
    milliseconds: int = 0,
) -> LiquiditySnapshot:
    return synchronized_book(market=market, milliseconds=milliseconds).liquidity_snapshot(
        at(milliseconds)
    )


def feature_snapshot(
    *,
    market: MarketType = MarketType.SPOT,
    milliseconds: int = 1000,
) -> tuple[LiquiditySnapshot, MicrostructureFeatureSnapshot]:
    engine = MicrostructureFeatureEngine()
    first = liquidity(market=market, milliseconds=0)
    engine.record_book(first)
    engine.record_event(
        trade_event(
            market=market,
            milliseconds=700,
            buyer_is_maker=False,
            quantity="3",
        )
    )
    engine.record_event(
        trade_event(
            market=market,
            milliseconds=850,
            buyer_is_maker=True,
            quantity="1",
            trade_id=2,
        )
    )
    current = liquidity(market=market, milliseconds=milliseconds)
    engine.record_book(current)
    return current, engine.snapshot(now=at(milliseconds), liquidity=current)


def write_session(
    root: Path,
    *,
    market: MarketType = MarketType.SPOT,
    rotate_event_count: int = 100,
    complete: bool = True,
) -> Path:
    writer = MicrostructureSessionWriter(
        root,
        market_type=market,
        symbol="ETHUSDT",
        session_id=f"fixture-{market.value.lower()}",
        started_at=at(),
        rotate_event_count=rotate_event_count,
    )
    writer.append(depth_event(market=market, first=101, last=101, previous=100))
    writer.append(snapshot_event(market=market))
    writer.append(trade_event(market=market, milliseconds=20))
    writer.append(
        depth_event(
            market=market,
            first=102,
            last=102,
            previous=101 if market is MarketType.USD_M_FUTURES else None,
            milliseconds=30,
            bids=[["2000.00", "4"]],
        )
    )
    return writer.close(complete=complete).session_path
