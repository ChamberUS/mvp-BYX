from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.microstructure.models import (
    DepthLevel,
    LiquidityExecutionEstimate,
    OrderBookReason,
    OrderBookStatus,
)
from adaptive_trader.microstructure.order_book import LocalOrderBook
from tests.microstructure.helpers import at, depth_event, liquidity, snapshot_event


def test_bootstrap_buffers_discards_old_update_and_applies_spanning_sequence() -> None:
    book = LocalOrderBook(MarketType.SPOT, "ethusdt")
    old = depth_event(first=98, last=100, milliseconds=1, bids=[["2000", "9"]])
    current = depth_event(first=100, last=102, milliseconds=2, bids=[["2000", "4"]])

    assert book.buffer_update(old).status is OrderBookStatus.BUFFERING
    assert book.buffer_update(current).applied is False
    assert book.buffered_update_count == 2
    result = book.apply_snapshot(snapshot_event(update_id=100))

    assert result.synchronized is True
    assert book.update_id == 102
    assert book.best_bid == DepthLevel(Decimal("2000"), Decimal("4"))
    assert book.last_event_time == at(2)
    assert book.last_receive_time == at(2)
    assert book.buffered_update_count == 0


def test_sequential_insert_delete_best_levels_and_top_n() -> None:
    book = LocalOrderBook(MarketType.SPOT, "ETHUSDT")
    book.apply_snapshot(snapshot_event())
    result = book.apply_update(
        depth_event(
            bids=[["2000.05", "3"], ["2000.00", "0"]],
            asks=[["2000.10", "0"], ["2000.30", "5"]],
        )
    )

    assert result.applied and result.synchronized
    assert book.best_bid == DepthLevel(Decimal("2000.05"), Decimal("3"))
    assert book.best_ask == DepthLevel(Decimal("2000.20"), Decimal("2.1"))
    assert len(book.top_bids(20)) == 20
    assert len(book.top_asks(5)) == 5


def test_duplicate_stale_gap_cross_and_stale_book_fail_closed() -> None:
    duplicate_book = LocalOrderBook(MarketType.SPOT, "ETHUSDT")
    duplicate_book.apply_snapshot(snapshot_event())
    update = depth_event()
    assert duplicate_book.apply_update(update).applied
    assert duplicate_book.buffer_update(update).reason is OrderBookReason.DUPLICATE_UPDATE
    stale = depth_event(first=99, last=100, milliseconds=11)
    assert duplicate_book.apply_update(stale).reason is OrderBookReason.STALE_UPDATE

    gap_book = LocalOrderBook(MarketType.SPOT, "ETHUSDT")
    gap_book.apply_snapshot(snapshot_event())
    gap = gap_book.apply_update(depth_event(first=103, last=103))
    assert gap.reason is OrderBookReason.ORDER_BOOK_DESYNC
    assert gap_book.status is OrderBookStatus.INVALID
    assert gap.gap_classification.value == "SNAPSHOT_ALIGNMENT_RETRY"
    assert gap_book.sequence_gap_count == 0
    assert gap_book.apply_update(depth_event(first=101, last=101)).applied is False

    crossed = LocalOrderBook(MarketType.SPOT, "ETHUSDT")
    crossed.apply_snapshot(snapshot_event())
    crossed_result = crossed.apply_update(depth_event(bids=[["2000.20", "5"]]))
    assert crossed_result.reason is OrderBookReason.CROSSED_BOOK
    assert crossed.status is OrderBookStatus.INVALID

    stale_book = LocalOrderBook(MarketType.SPOT, "ETHUSDT")
    stale_book.apply_snapshot(snapshot_event())
    assert stale_book.mark_stale().reason is OrderBookReason.ORDER_BOOK_DESYNC


def test_futures_previous_sequence_link_and_resync_are_strict() -> None:
    book = LocalOrderBook(MarketType.USD_M_FUTURES, "ETHUSDT")
    book.apply_snapshot(snapshot_event(market=MarketType.USD_M_FUTURES))
    broken = book.apply_update(
        depth_event(
            market=MarketType.USD_M_FUTURES,
            first=101,
            last=101,
            previous=99,
        )
    )
    assert broken.reason is OrderBookReason.ORDER_BOOK_DESYNC

    book.begin_resync()
    assert book.status is OrderBookStatus.RESYNC_IN_PROGRESS
    assert book.resync_count == 1
    assert book.update_id is None and book.best_bid is None
    update = depth_event(
        market=MarketType.USD_M_FUTURES,
        first=200,
        last=201,
        previous=200,
    )
    assert book.apply_update(update).applied is False
    book.buffer_update(update)
    result = book.apply_snapshot(
        snapshot_event(market=MarketType.USD_M_FUTURES, update_id=200)
    )
    assert result.synchronized and book.update_id == 201


def test_book_rejects_wrong_events_invalid_counts_and_empty_or_crossed_snapshot() -> None:
    with pytest.raises(ValueError, match="at least 20"):
        LocalOrderBook(MarketType.SPOT, "ETHUSDT", visible_levels=19)
    with pytest.raises(ValueError, match="alphanumeric"):
        LocalOrderBook(MarketType.SPOT, "ETH/USDT")

    book = LocalOrderBook(MarketType.SPOT, "ETHUSDT")
    with pytest.raises(ValueError, match="different book"):
        book.apply_snapshot(snapshot_event(market=MarketType.USD_M_FUTURES))
    with pytest.raises(ValueError, match="positive"):
        book.top_bids(0)
    with pytest.raises(ValueError, match="both sides"):
        book.liquidity_snapshot(at())
    crossed = snapshot_event(bids=[["2001", "1"]], asks=[["2000", "1"]])
    assert book.apply_snapshot(crossed).reason is OrderBookReason.CROSSED_BOOK


def test_liquidity_snapshot_spread_depth_imbalance_and_freshness() -> None:
    snapshot = liquidity(milliseconds=10)

    assert snapshot.best_bid == Decimal("2000")
    assert snapshot.best_ask == Decimal("2000.10")
    assert snapshot.mid_price == Decimal("2000.05")
    assert snapshot.spread == Decimal("0.10")
    assert snapshot.spread_bps == Decimal("0.10") / Decimal("2000.05") * 10_000
    assert snapshot.top_5_bid_notional > 0
    assert snapshot.top_10_bid_notional > snapshot.top_5_bid_notional
    assert snapshot.top_20_ask_notional > snapshot.top_10_ask_notional
    assert Decimal("-1") <= snapshot.depth_imbalance_20 <= Decimal("1")
    assert snapshot.book_age_ms == 0


def test_executable_vwap_slippage_depth_bands_and_execution_estimate() -> None:
    snapshot = liquidity()

    assert snapshot.executable_buy_price(Decimal("2")) == Decimal("2000.10")
    assert snapshot.executable_sell_price(Decimal("2")) == Decimal("2000")
    expected_buy = (Decimal("2") * Decimal("2000.10") + Decimal("1") * Decimal("2000.20")) / 3
    assert snapshot.executable_buy_price(Decimal("3")) == expected_buy
    assert snapshot.slippage_bps(PositionSide.LONG, Decimal("3")) > 0
    assert snapshot.slippage_bps(PositionSide.SHORT, Decimal("3")) > 0
    assert snapshot.executable_buy_price(Decimal("1000")) is None
    assert snapshot.slippage_bps(PositionSide.LONG, Decimal("1000")) is None

    one = snapshot.available_notional_within_1bp(PositionSide.LONG)
    two = snapshot.available_notional_within_2bp(PositionSide.LONG)
    five = snapshot.available_notional_within_5bp(PositionSide.LONG)
    assert 0 < one <= two <= five
    assert snapshot.visible_quantity(PositionSide.LONG) > 0
    estimate = snapshot.execution_estimate(PositionSide.LONG, Decimal("3"))
    assert isinstance(estimate, LiquidityExecutionEstimate)
    assert estimate.expected_vwap == expected_buy
    assert estimate.executable_notional == expected_buy * 3
    assert estimate.percent_of_visible_depth is not None
    assert estimate.top_20_notional == snapshot.top_20_ask_notional


def test_liquidity_validation_and_future_snapshot_are_rejected() -> None:
    snapshot = liquidity()
    with pytest.raises(ValueError, match="non-negative"):
        snapshot.available_notional_within_bps(PositionSide.LONG, Decimal("-1"))
    with pytest.raises(ValueError, match="positive"):
        snapshot.executable_buy_price(Decimal("0"))
    with pytest.raises(ValueError, match="timezone-aware"):
        LocalOrderBook(MarketType.SPOT, "ETHUSDT").liquidity_snapshot(
            datetime(2026, 1, 1)
        )
    with pytest.raises(ValueError, match=r"\[-1, 1\]"):
        replace(snapshot, depth_imbalance_5=Decimal("2"))
