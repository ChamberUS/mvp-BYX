from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.research.trend_following_risk import (
    DefensiveRiskStateMachine,
    TradeRiskOutcome,
)
from adaptive_trader.strategy.trend_following import TrendFollowingReasonCode


def test_forced_end_loss_neither_counts_nor_breaks_existing_loss_sequence() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    machine = DefensiveRiskStateMachine()
    machine.record_trade(
        TradeRiskOutcome(
            closed_at=start,
            exit_reason=TrendFollowingReasonCode.MACRO_FILTER_EXIT,
            net_pnl=Decimal("-100"),
            equity_before=Decimal("10000"),
            equity_after=Decimal("9900"),
        )
    )

    transition = machine.record_trade(
        TradeRiskOutcome(
            closed_at=start + timedelta(days=1),
            exit_reason=TrendFollowingReasonCode.FORCED_END,
            net_pnl=Decimal("-50"),
            equity_before=Decimal("9900"),
            equity_after=Decimal("9850"),
        )
    )

    assert transition.counted_structural_loss is False
    assert machine.state.consecutive_structural_losses == 1
    assert machine.state.loss_sequence_start_equity == Decimal("10000")
