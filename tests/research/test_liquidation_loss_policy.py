from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.research.trend_following_risk import (
    DefensiveRiskStateMachine,
    RiskMode,
    RiskPolicy,
    RiskWarning,
    TradeRiskOutcome,
)
from adaptive_trader.strategy.trend_following import TrendFollowingReasonCode


def test_one_x_liquidation_immediately_activates_defensive_and_kills_day() -> None:
    closed_at = datetime(2024, 1, 2, 8, tzinfo=UTC)
    machine = DefensiveRiskStateMachine(policy=RiskPolicy.FIXED)

    transition = machine.record_trade(
        TradeRiskOutcome(
            closed_at=closed_at,
            exit_reason=TrendFollowingReasonCode.LIQUIDATION,
            net_pnl=Decimal("-5000"),
            equity_before=Decimal("10000"),
            equity_after=Decimal("5000"),
        )
    )

    assert transition.activated is True
    assert transition.warnings == (RiskWarning.UNEXPECTED_LIQUIDATION_AT_1X,)
    assert machine.state.mode is RiskMode.DEFENSIVE
    assert machine.state.recovery_target == Decimal("10000")
    assert machine.risk_percent == Decimal("0.5")
    assert machine.killed_for_day(closed_at.date()) is True


def test_liquidation_kill_state_resets_next_day_but_defensive_mode_remains() -> None:
    closed_at = datetime(2024, 1, 2, 8, tzinfo=UTC)
    machine = DefensiveRiskStateMachine()
    machine.record_trade(
        TradeRiskOutcome(
            closed_at=closed_at,
            exit_reason=TrendFollowingReasonCode.LIQUIDATION,
            net_pnl=Decimal("-5000"),
            equity_before=Decimal("10000"),
            equity_after=Decimal("5000"),
        )
    )

    machine.begin_day((closed_at + timedelta(days=1)).date())

    assert machine.state.kill_date is None
    assert machine.state.mode is RiskMode.DEFENSIVE
    assert machine.state.recovery_target == Decimal("10000")
