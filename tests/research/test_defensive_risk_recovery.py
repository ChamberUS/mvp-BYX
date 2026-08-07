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
        exit_reason=TrendFollowingReasonCode.MACRO_FILTER_EXIT,
        net_pnl=Decimal(after) - Decimal(before),
        equity_before=Decimal(before),
        equity_after=Decimal(after),
    )


def test_defensive_mode_recovers_only_at_original_sequence_equity() -> None:
    machine = DefensiveRiskStateMachine()
    for outcome in (
        _outcome(1, "10000", "9900"),
        _outcome(2, "9900", "9800"),
        _outcome(3, "9800", "9700"),
    ):
        machine.record_trade(outcome)

    below_target = machine.observe_equity(Decimal("9999.99"), START + timedelta(days=4))
    recovered = machine.observe_equity(Decimal("10000"), START + timedelta(days=5))

    assert below_target.recovered is False
    assert below_target.current.mode is RiskMode.DEFENSIVE
    assert recovered.recovered is True
    assert machine.state.mode is RiskMode.NORMAL
    assert machine.state.recovery_target is None
    assert machine.risk_percent == Decimal("1")
