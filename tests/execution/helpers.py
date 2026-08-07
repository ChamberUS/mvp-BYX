from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.market import MarketType
from adaptive_trader.execution.models import BookState
from adaptive_trader.microstructure.models import DepthLevel

BASE = datetime(2026, 8, 6, 12, tzinfo=UTC)


def at(milliseconds: int) -> datetime:
    return BASE + timedelta(milliseconds=milliseconds)


def book(
    milliseconds: int = 30,
    *,
    market: MarketType = MarketType.SPOT,
    bids: tuple[tuple[str, str], ...] = (("100.00", "2"), ("99.90", "3")),
    asks: tuple[tuple[str, str], ...] = (("100.10", "1"), ("100.20", "2")),
    synchronized: bool = True,
    sequence: int = 1,
) -> BookState:
    return BookState(
        timestamp=at(milliseconds),
        market=market,
        symbol="ETHUSDT",
        bids=tuple(DepthLevel(Decimal(price), Decimal(quantity)) for price, quantity in bids),
        asks=tuple(DepthLevel(Decimal(price), Decimal(quantity)) for price, quantity in asks),
        synchronized=synchronized,
        sequence=sequence,
    )
