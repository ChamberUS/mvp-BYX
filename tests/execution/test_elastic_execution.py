from __future__ import annotations

from decimal import Decimal

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.execution import (
    BookState,
    ElasticExitExecutionAdapter,
    ExecutionSimulator,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionEffect,
    ReversalDiagnostics,
)
from adaptive_trader.microstructure.elastic_exit import ElasticProfitExitController
from adaptive_trader.microstructure.models import MakerPreference, ProfitExtensionState
from tests.microstructure.helpers import at, liquidity


def state(milliseconds: int) -> BookState:
    snapshot = liquidity(milliseconds=milliseconds)
    return BookState(
        timestamp=at(milliseconds),
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        bids=snapshot.bids,
        asks=snapshot.asks,
        sequence=milliseconds,
    )


def test_elastic_300ms_exit_uses_bids_and_submits_only_once() -> None:
    simulator = ExecutionSimulator()
    simulator.submit(
        client_intent_id="entry",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        decision_time=at(0),
        books=(state(30),),
        reference_price=Decimal("2000.10"),
        maximum_slippage_bps=Decimal("10"),
        maker_preference=MakerPreference.TAKER,
    )
    controller = ElasticProfitExitController(
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("1998"),
    )
    adapter = ElasticExitExecutionAdapter(
        simulator=simulator,
        controller=controller,
        market=MarketType.SPOT,
        symbol="ETHUSDT",
    )
    neutral = ReversalDiagnostics(price_reversal_detected=True)
    armed = adapter.observe(
        timestamp=at(30),
        liquidity=liquidity(milliseconds=30),
        books=(state(50),),
        diagnostics=neutral,
        mark_price=Decimal("9999"),
    )
    assert armed.observation.state is ProfitExtensionState.ARMED
    assert armed.execution is None
    assert neutral.microstructure_reversal is False

    exited = adapter.observe(
        timestamp=at(330),
        liquidity=liquidity(milliseconds=330),
        books=(state(350),),
        diagnostics=neutral,
        mark_price=Decimal("9999"),
    )
    assert exited.observation.exit_reason == "NO_NEW_PEAK_300MS"
    assert exited.execution is not None
    assert exited.execution.order.status is OrderStatus.FILLED
    assert exited.execution.order.side is OrderSide.SELL
    assert exited.execution.order.fills[0].price == Decimal("2000")
    assert simulator.position_ledger.snapshot(MarketType.SPOT, "ETHUSDT", at(360)).quantity == 0

    repeated = adapter.observe(
        timestamp=at(500),
        liquidity=liquidity(milliseconds=500),
        books=(state(520),),
        diagnostics=neutral,
    )
    assert repeated.execution is None


def test_reversal_diagnostics_separate_price_from_microstructure() -> None:
    assert not ReversalDiagnostics(price_reversal_detected=True).microstructure_reversal
    assert ReversalDiagnostics(ofi_reversal_detected=True).microstructure_reversal
    assert ReversalDiagnostics(trade_flow_reversal_detected=True).microstructure_reversal
    assert ReversalDiagnostics(depth_reversal_detected=True).microstructure_reversal
    assert ReversalDiagnostics(microprice_reversal_detected=True).microstructure_reversal
