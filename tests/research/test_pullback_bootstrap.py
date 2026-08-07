from adaptive_trader.research.pullback_analysis import (
    BootstrapStatus,
    bootstrap_trades,
)
from tests.research.pullback_helpers import closed_trade


def test_trade_bootstrap_is_deterministic_with_seed_42() -> None:
    trades = tuple(
        closed_trade(value)
        for value in ("2", "1", "-1", "3", "-2", "1")
    )

    first = bootstrap_trades(
        market="SPOT",
        mode="LONG",
        variant_id="PULLBACK_BASE",
        period="VALIDATION",
        trades=trades,
    )
    second = bootstrap_trades(
        market="SPOT",
        mode="LONG",
        variant_id="PULLBACK_BASE",
        period="VALIDATION",
        trades=trades,
    )

    assert first == second
    assert first.seed == 42
    assert first.iterations == 2000
    assert first.total_pnl_interval is not None


def test_bootstrap_does_not_invent_evidence_for_tiny_samples() -> None:
    result = bootstrap_trades(
        market="SPOT",
        mode="LONG",
        variant_id="PULLBACK_BASE",
        period="VALIDATION",
        trades=(closed_trade("1"),),
    )

    assert result.status is BootstrapStatus.INSUFFICIENT_SAMPLE
    assert result.total_pnl_interval is None
