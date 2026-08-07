from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.research.trend_following_risk import (
    DefensiveRiskStateMachine,
    RiskMode,
    TradeRiskOutcome,
)
from adaptive_trader.strategy.trend_following import TrendFollowingReasonCode

START = datetime(2024, 1, 1, tzinfo=UTC)


def _outcome(index: int, before: str, after: str) -> TradeRiskOutcome:
    return TradeRiskOutcome(
        closed_at=START + timedelta(days=index),
        exit_reason=TrendFollowingReasonCode.DONCHIAN_EXIT_10,
        net_pnl=Decimal(after) - Decimal(before),
        equity_before=Decimal(before),
        equity_after=Decimal(after),
    )


def _defensive_machine() -> DefensiveRiskStateMachine:
    machine = DefensiveRiskStateMachine()
    for outcome in (
        _outcome(1, "10000", "9900"),
        _outcome(2, "9900", "9800"),
        _outcome(3, "9800", "9700"),
    ):
        machine.record_trade(outcome)
    return machine


def test_winner_without_full_equity_recovery_stays_defensive() -> None:
    machine = _defensive_machine()

    transition = machine.record_trade(_outcome(4, "9700", "9800"))

    assert transition.recovered is False
    assert machine.state.mode is RiskMode.DEFENSIVE
    assert machine.state.recovery_target == Decimal("10000")
    assert machine.risk_percent == Decimal("0.5")


def test_equity_target_recovery_restores_one_percent_and_clears_sequence() -> None:
    machine = _defensive_machine()

    transition = machine.observe_equity(
        Decimal("10000"),
        START + timedelta(days=5),
    )

    assert transition.recovered is True
    assert machine.state.mode is RiskMode.NORMAL
    assert machine.state.recovery_target is None
    assert machine.state.consecutive_structural_losses == 0
    assert machine.risk_percent == Decimal("1")
