from decimal import Decimal

import pytest

from adaptive_trader.domain.models import OrderIntent, SignalDirection
from adaptive_trader.execution.backtest import (
    BacktestExecutionConfig,
    BacktestOrderExecutor,
    ExecutionError,
)


def intent(analysis_time) -> OrderIntent:
    return OrderIntent(
        intent_id="intent-1",
        symbol="ETHUSDT",
        direction=SignalDirection.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        stop_loss=Decimal("90"),
        take_profit=Decimal("120"),
        created_at=analysis_time,
    )


def test_backtest_executor_applies_spread_slippage_and_fee(analysis_time) -> None:
    executor = BacktestOrderExecutor(
        BacktestExecutionConfig(
            taker_fee_bps=Decimal("10"),
            slippage_bps=Decimal("10"),
            spread_bps=Decimal("10"),
        )
    )
    executor.set_reference_price(Decimal("100"))

    order = executor.execute(intent(analysis_time))

    assert order.price > Decimal("100")
    assert order.fee > 0
    assert order.slippage_cost > 0
    assert order.spread_cost > 0


def test_backtest_executor_requires_reference_and_minimum_quantity(analysis_time) -> None:
    executor = BacktestOrderExecutor(BacktestExecutionConfig(minimum_order_quantity=Decimal("2")))
    with pytest.raises(ExecutionError):
        executor.execute(intent(analysis_time))
    executor.set_reference_price(Decimal("100"))
    with pytest.raises(ExecutionError):
        executor.execute(intent(analysis_time))
