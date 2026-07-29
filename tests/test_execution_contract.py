import inspect

import pytest

from adaptive_trader.domain.models import MarketSignal, OrderIntent
from adaptive_trader.execution.simulator import SimulatedOrderExecutor


def test_executor_signature_accepts_order_intent_only() -> None:
    parameter = inspect.signature(SimulatedOrderExecutor.execute).parameters["intent"]

    assert parameter.annotation in {OrderIntent, "OrderIntent"}
    assert MarketSignal not in inspect.signature(SimulatedOrderExecutor.execute).parameters.values()


def test_executor_rejects_runtime_signal_object(buy_signal) -> None:
    with pytest.raises(AttributeError):
        SimulatedOrderExecutor().execute(buy_signal)  # type: ignore[arg-type]
