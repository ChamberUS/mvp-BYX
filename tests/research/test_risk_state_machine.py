from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.research.trend_following_risk import (
    DefensiveRiskStateMachine,
    RiskMode,
    RiskPolicy,
    TradeRiskOutcome,
)
from adaptive_trader.strategy.trend_following import TrendFollowingReasonCode

START = datetime(2024, 1, 1, tzinfo=UTC)


def _loss(index: int, before: str, after: str) -> TradeRiskOutcome:
    return TradeRiskOutcome(
        closed_at=START + timedelta(days=index),
        exit_reason=TrendFollowingReasonCode.DONCHIAN_EXIT_20,
        net_pnl=Decimal(after) - Decimal(before),
        equity_before=Decimal(before),
        equity_after=Decimal(after),
    )


def test_third_consecutive_structural_loss_activates_half_percent() -> None:
    machine = DefensiveRiskStateMachine()

    machine.record_trade(_loss(1, "10000", "9900"))
    machine.record_trade(_loss(2, "9900", "9800"))
    transition = machine.record_trade(_loss(3, "9800", "9700"))

    assert transition.activated is True
    assert machine.state.mode is RiskMode.DEFENSIVE
    assert machine.state.consecutive_structural_losses == 3
    assert machine.state.recovery_target == Decimal("10000")
    assert machine.risk_percent == Decimal("0.5")


def test_positive_trade_breaks_sequence_before_third_loss() -> None:
    machine = DefensiveRiskStateMachine()
    machine.record_trade(_loss(1, "10000", "9900"))
    winner = TradeRiskOutcome(
        closed_at=START + timedelta(days=2),
        exit_reason=TrendFollowingReasonCode.MACRO_FILTER_EXIT,
        net_pnl=Decimal("50"),
        equity_before=Decimal("9900"),
        equity_after=Decimal("9950"),
    )

    machine.record_trade(winner)

    assert machine.state.mode is RiskMode.NORMAL
    assert machine.state.consecutive_structural_losses == 0
    assert machine.state.loss_sequence_start_equity is None


def test_new_defensive_loss_keeps_original_recovery_target() -> None:
    machine = DefensiveRiskStateMachine()
    for outcome in (
        _loss(1, "10000", "9900"),
        _loss(2, "9900", "9800"),
        _loss(3, "9800", "9700"),
    ):
        machine.record_trade(outcome)

    transition = machine.record_trade(_loss(4, "9700", "9600"))

    assert transition.counted_structural_loss is True
    assert machine.state.consecutive_structural_losses == 4
    assert machine.state.recovery_target == Decimal("10000")
    assert machine.risk_percent == Decimal("0.5")


def test_fixed_policy_never_activates_from_structural_losses() -> None:
    machine = DefensiveRiskStateMachine(policy=RiskPolicy.FIXED)
    for outcome in (
        _loss(1, "10000", "9900"),
        _loss(2, "9900", "9800"),
        _loss(3, "9800", "9700"),
    ):
        machine.record_trade(outcome)

    assert machine.state.mode is RiskMode.NORMAL
    assert machine.state.consecutive_structural_losses == 0
    assert machine.risk_percent == Decimal("1")
