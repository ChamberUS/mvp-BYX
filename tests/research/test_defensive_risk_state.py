from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.research.trend_following_risk import (
    DefensiveRiskStateMachine,
    RiskMode,
    TradeRiskOutcome,
)
from adaptive_trader.strategy.trend_following import TrendFollowingReasonCode


def _loss(index: int, before: str, after: str) -> TradeRiskOutcome:
    return TradeRiskOutcome(
        closed_at=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index),
        exit_reason=TrendFollowingReasonCode.DONCHIAN_EXIT_20,
        net_pnl=Decimal(after) - Decimal(before),
        equity_before=Decimal(before),
        equity_after=Decimal(after),
    )


def test_three_structural_losses_switch_new_entries_to_half_percent() -> None:
    machine = DefensiveRiskStateMachine()

    machine.record_trade(_loss(1, "10000", "9900"))
    machine.record_trade(_loss(2, "9900", "9800"))
    transition = machine.record_trade(_loss(3, "9800", "9700"))

    assert transition.activated is True
    assert transition.counted_structural_loss is True
    assert machine.state.mode is RiskMode.DEFENSIVE
    assert machine.state.recovery_target == Decimal("10000")
    assert machine.risk_percent == Decimal("0.5")
