from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.microstructure.alpha import liquidity_state
from adaptive_trader.microstructure.connection import ConnectionSupervisor, stream_capabilities
from adaptive_trader.microstructure.contracts import (
    MARKOUT_HORIZONS_MS,
    MarkoutPrice,
    calculate_markouts,
)
from adaptive_trader.microstructure.models import (
    ExecutionAnalysis,
    IntradayOrderIntent,
    IntradayRiskConfig,
    LiquidityState,
    MakerPreference,
    OrderUrgency,
)
from tests.microstructure.helpers import at, liquidity


def test_official_spot_and_futures_capabilities_are_public_only() -> None:
    spot = stream_capabilities(MarketType.SPOT, "ETHUSDT")
    futures = stream_capabilities(MarketType.USD_M_FUTURES, "ethusdt")

    assert spot.websocket_base_url == "wss://stream.binance.com:9443/stream"
    assert spot.aggregate_trade_stream == "ethusdt@aggTrade"
    assert spot.book_ticker_stream == "ethusdt@bookTicker"
    assert spot.diff_depth_stream == "ethusdt@depth@100ms"
    assert spot.depth_snapshot_path == "/api/v3/depth"
    assert spot.mark_price_stream is None
    assert futures.websocket_base_url == "wss://fstream.binance.com/stream"
    assert futures.depth_snapshot_path == "/fapi/v1/depth"
    assert futures.mark_price_stream == "ethusdt@markPrice@1s"
    assert spot.public_only and futures.public_only
    assert not spot.authenticated and not futures.order_capable
    with pytest.raises(ValueError, match="alphanumeric"):
        stream_capabilities(MarketType.SPOT, "ETH/USDT")


def test_connection_supervisor_counts_downtime_and_bounds_backoff() -> None:
    supervisor = ConnectionSupervisor(
        maximum_reconnects=3,
        base_backoff_ms=100,
        maximum_backoff_ms=250,
    )
    supervisor.connected(1_000_000)
    supervisor.disconnected(2_000_000)
    supervisor.disconnected(2_500_000)
    assert supervisor.reconnect_delay_ms(1) == 100
    assert supervisor.reconnect_delay_ms(2) == 200
    assert supervisor.reconnect_delay_ms(3) == 250
    supervisor.snapshot_observed()
    supervisor.sequence_gap_observed()
    supervisor.resync_observed()
    metrics = supervisor.connected(4_000_000)

    assert metrics.connection_count == 2
    assert metrics.reconnect_count == 3
    assert metrics.snapshot_count == 1
    assert metrics.sequence_gap_count == 1
    assert metrics.resync_count == 1
    assert metrics.downtime_ms == Decimal("2")
    with pytest.raises(RuntimeError, match="exhausted"):
        supervisor.reconnect_delay_ms(4)
    with pytest.raises(ValueError, match="non-negative"):
        supervisor.connected(-1)
    with pytest.raises(ValueError, match="invalid"):
        ConnectionSupervisor(base_backoff_ms=0)
    with pytest.raises(ValueError, match="cannot exceed"):
        ConnectionSupervisor(base_backoff_ms=10, maximum_backoff_ms=5)


def test_liquidity_state_distinguishes_ok_thin_and_unsafe() -> None:
    snapshot = liquidity()
    ok = liquidity_state(
        snapshot,
        required_quantity=Decimal("1"),
        maximum_visible_depth_fraction=Decimal("0.5"),
        maximum_slippage_bps=Decimal("1"),
        side=PositionSide.LONG,
    )
    thin = liquidity_state(
        snapshot,
        required_quantity=Decimal("3"),
        maximum_visible_depth_fraction=Decimal("0.5"),
        maximum_slippage_bps=Decimal("0"),
        side=PositionSide.LONG,
    )
    unsafe = liquidity_state(
        replace(snapshot, synchronized=False),
        required_quantity=Decimal("1"),
        maximum_visible_depth_fraction=Decimal("0.5"),
        maximum_slippage_bps=Decimal("1"),
        side=PositionSide.SHORT,
    )
    assert ok is LiquidityState.LIQUIDITY_OK
    assert thin is LiquidityState.LIQUIDITY_THIN
    assert unsafe is LiquidityState.LIQUIDITY_UNSAFE


def test_markouts_are_post_event_and_separate_side_style_and_reference() -> None:
    prices = tuple(
        MarkoutPrice(
            timestamp=at(horizon),
            executable_price=Decimal("2001"),
            mid_price=Decimal("2002"),
        )
        for horizon in MARKOUT_HORIZONS_MS
    )
    long = calculate_markouts(
        signal_time=at(),
        side=PositionSide.LONG,
        reference_price=Decimal("2000"),
        prices=prices,
        execution_style="TAKER",
        use_mid=False,
    )
    short = calculate_markouts(
        signal_time=at(),
        side=PositionSide.SHORT,
        reference_price=Decimal("2000"),
        prices=prices,
        execution_style="MAKER",
        use_mid=True,
    )

    assert tuple(horizon for horizon, _ in long.horizons_bps) == MARKOUT_HORIZONS_MS
    assert long.horizons_bps[0][1] == Decimal("5.000")
    assert short.horizons_bps[0][1] == (Decimal("2000") / Decimal("2002") - 1) * 10_000
    assert long.execution_style == "TAKER" and short.execution_style == "MAKER"
    assert long.post_event_only and short.post_event_only

    missing = calculate_markouts(
        signal_time=at(),
        side=PositionSide.LONG,
        reference_price=Decimal("2000"),
        prices=(),
        execution_style="MAKER",
        use_mid=False,
    )
    assert all(value is None for _, value in missing.horizons_bps)
    with pytest.raises(ValueError, match="post-event"):
        calculate_markouts(
            signal_time=at(100),
            side=PositionSide.LONG,
            reference_price=Decimal("2000"),
            prices=(MarkoutPrice(at(), Decimal("1"), Decimal("1")),),
            execution_style="MAKER",
            use_mid=False,
        )
    with pytest.raises(ValueError, match="style"):
        calculate_markouts(
            signal_time=at(),
            side=PositionSide.LONG,
            reference_price=Decimal("2000"),
            prices=(),
            execution_style="UNKNOWN",
            use_mid=False,
        )


def test_risk_order_and_execution_analysis_contracts_validate_without_executing() -> None:
    risk = IntradayRiskConfig(
        risk_per_trade_percent=Decimal("0.1"),
        maximum_daily_loss_percent=Decimal("1"),
        maximum_weekly_loss_percent=Decimal("3"),
        maximum_consecutive_losses=3,
        cooldown_ms=1_000,
        maximum_open_positions=1,
        maximum_orders_per_minute=5,
        maximum_notional=Decimal("1000"),
        maximum_visible_depth_fraction=Decimal("0.1"),
        maximum_slippage_bps=Decimal("2"),
        kill_switch_enabled=True,
    )
    intent = IntradayOrderIntent(
        side=PositionSide.LONG,
        quantity=Decimal("0.1"),
        reference_price=Decimal("2000"),
        limit_price=Decimal("2000.10"),
        urgency=OrderUrgency.NORMAL,
        maker_preference=MakerPreference.MAKER,
        maximum_slippage_bps=Decimal("2"),
        expiry_ms=500,
        reason="research plan only",
    )
    analysis = ExecutionAnalysis(
        expected_edge_bps=Decimal("3"),
        realized_edge_bps=None,
        spread_cost_bps=Decimal("0.5"),
        fee_cost_bps=Decimal("2"),
        slippage_bps=Decimal("0.5"),
        total_cost_bps=Decimal("3"),
        markout_bps=None,
        adverse_selection_bps=None,
        fill_latency_ms=None,
        decision_latency_ms=Decimal("1"),
        book_age_at_decision_ms=Decimal("5"),
    )

    assert risk.leverage == Decimal("1") and risk.maximum_open_positions == 1
    assert intent.reason == "research plan only"
    assert analysis.post_event_only
    with pytest.raises(ValueError, match="locked to 1x"):
        replace(risk, leverage=Decimal("2"))
    with pytest.raises(ValueError, match="must not exceed 1"):
        replace(risk, maximum_visible_depth_fraction=Decimal("1.1"))
    with pytest.raises(ValueError, match="invalid"):
        replace(intent, expiry_ms=0)
    with pytest.raises(ValueError, match="post-event"):
        replace(analysis, post_event_only=False)
