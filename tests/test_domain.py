import json
from decimal import Decimal

from adaptive_trader.domain.models import (
    MarketRegime,
    MarketSignal,
    SignalDirection,
    serialize_model,
)


def test_model_serialization_preserves_decimal_as_string(buy_signal: MarketSignal) -> None:
    serialized = serialize_model(buy_signal)

    assert serialized["entry_price"] == "2000"
    assert serialized["confidence"] == "0.8"
    assert isinstance(serialized["entry_price"], str)
    assert json.loads(json.dumps(serialized))["direction"] == "BUY"


def test_decimal_arithmetic_is_exact() -> None:
    quantity = Decimal("0.1")
    price = Decimal("2000.10")

    assert quantity * price == Decimal("200.010")


def test_hold_signal_can_have_zero_trade_values(analysis_time) -> None:
    signal = MarketSignal(
        signal_id="hold-1",
        symbol="ETHUSDT",
        generated_at=analysis_time,
        direction=SignalDirection.HOLD,
        regime=MarketRegime.RANGING,
        confidence=Decimal("0"),
        entry_price=Decimal("2000"),
        stop_loss=Decimal("0"),
        take_profit=Decimal("0"),
        suggested_quantity=Decimal("0"),
        rationale="no setup",
        analyzer_name="test",
    )

    assert serialize_model(signal)["direction"] == "HOLD"
