from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from adaptive_trader.microstructure.features import MicrostructureFeatureEngine
from tests.microstructure.helpers import at, liquidity, trade_event


def test_microprice_spread_depth_flow_windows_and_boundary_timestamps() -> None:
    engine = MicrostructureFeatureEngine()
    first = liquidity(milliseconds=0)
    current = replace(
        liquidity(milliseconds=1_000),
        bid_quantity=Decimal("4"),
        ask_quantity=Decimal("1"),
    )
    engine.record_book(first)
    engine.record_book(current)
    engine.record_event(trade_event(milliseconds=0, quantity="10", trade_id=1))
    engine.record_event(trade_event(milliseconds=750, quantity="3", trade_id=2))
    engine.record_event(
        trade_event(milliseconds=800, quantity="1", buyer_is_maker=True, trade_id=3)
    )
    snapshot = engine.snapshot(now=at(1_000), liquidity=current)

    expected_microprice = (
        current.best_ask * Decimal("4") + current.best_bid * Decimal("1")
    ) / Decimal("5")
    assert snapshot.spread == current.spread
    assert snapshot.spread_bps == current.spread_bps
    assert snapshot.mid_price == current.mid_price
    assert snapshot.microprice == expected_microprice
    assert snapshot.microprice_edge_bps > 0
    assert snapshot.depth_imbalance_5 == current.depth_imbalance_5
    assert snapshot.trade_flow_250ms.aggressive_buy_qty == Decimal("3")
    assert snapshot.trade_flow_250ms.aggressive_sell_qty == Decimal("1")
    assert snapshot.trade_flow_250ms.trade_count == 2
    assert snapshot.trade_flow_1s.aggressive_buy_qty == Decimal("13")
    assert snapshot.trade_flow_3s.trade_count == 3
    assert snapshot.trade_flow_10s.quote_notional > 0
    assert snapshot.trade_age_ms == Decimal("200.0")
    assert snapshot.event_age_ms == 0
    assert snapshot.warmup_complete is True


def test_ofi_momentum_and_realized_volatility_are_point_in_time() -> None:
    engine = MicrostructureFeatureEngine()
    first = liquidity(milliseconds=0)
    second = replace(
        liquidity(milliseconds=500),
        best_bid=Decimal("2000.10"),
        best_ask=Decimal("2000.20"),
        bid_quantity=Decimal("5"),
        ask_quantity=Decimal("1"),
        mid_price=Decimal("2000.15"),
    )
    third = replace(
        liquidity(milliseconds=1_000),
        best_bid=Decimal("2000.20"),
        best_ask=Decimal("2000.30"),
        bid_quantity=Decimal("6"),
        ask_quantity=Decimal("1"),
        mid_price=Decimal("2000.25"),
    )
    for book in (first, second, third):
        engine.record_book(book)
    engine.record_event(trade_event(milliseconds=900))
    snapshot = engine.snapshot(now=at(1_000), liquidity=third)

    assert snapshot.ofi_250ms == Decimal("7")
    assert snapshot.ofi_1s == Decimal("14")
    assert snapshot.ofi_3s == Decimal("14")
    assert snapshot.momentum_250ms_bps == (
        third.mid_price / second.mid_price - 1
    ) * 10_000
    assert snapshot.momentum_1s_bps == (third.mid_price / first.mid_price - 1) * 10_000
    assert snapshot.momentum_3s_bps == 0
    assert snapshot.volatility_1s_bps > 0
    assert snapshot.volatility_5s_bps == snapshot.volatility_30s_bps


def test_future_trade_and_book_never_enter_earlier_snapshot() -> None:
    engine = MicrostructureFeatureEngine()
    current = liquidity(milliseconds=1_000)
    future = replace(
        liquidity(milliseconds=2_000),
        mid_price=Decimal("9999"),
        best_bid=Decimal("9998"),
        best_ask=Decimal("10000"),
    )
    engine.record_book(current)
    engine.record_book(future)
    engine.record_event(trade_event(milliseconds=1_000, quantity="2"))
    engine.record_event(trade_event(milliseconds=2_000, quantity="100", trade_id=2))

    snapshot = engine.snapshot(now=at(1_000), liquidity=current)

    assert snapshot.mid_price == current.mid_price
    assert snapshot.trade_flow_250ms.aggressive_buy_qty == Decimal("2")
    assert snapshot.trade_flow_10s.trade_count == 1
    assert snapshot.momentum_1s_bps == 0
    assert snapshot.volatility_1s_bps == 0


def test_zero_quantity_microprice_falls_back_to_mid_and_no_trade_age_is_none() -> None:
    engine = MicrostructureFeatureEngine()
    current = replace(
        liquidity(),
        bid_quantity=Decimal("0"),
        ask_quantity=Decimal("0"),
    )
    engine.record_book(current)
    snapshot = engine.snapshot(now=at(), liquidity=current)

    assert snapshot.microprice == current.mid_price
    assert snapshot.trade_age_ms is None
    assert snapshot.trade_flow_1s.aggressive_trade_imbalance == 0
    assert snapshot.warmup_complete is False


def test_feature_input_guards_reject_short_retention_naive_and_future_queries() -> None:
    with pytest.raises(ValueError, match="at least 30"):
        MicrostructureFeatureEngine(retention_seconds=29)
    engine = MicrostructureFeatureEngine()
    with pytest.raises(ValueError, match="timezone-aware"):
        engine.snapshot(now=datetime(2026, 1, 1), liquidity=liquidity())
    current = liquidity(milliseconds=1_000)
    engine.record_book(current)
    with pytest.raises(ValueError, match="future"):
        engine.snapshot(now=at(999), liquidity=current)
    empty = MicrostructureFeatureEngine()
    with pytest.raises(ValueError, match="observed book"):
        empty.snapshot(now=at(), liquidity=liquidity())
