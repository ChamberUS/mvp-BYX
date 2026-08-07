from decimal import Decimal

from adaptive_trader.research.trend_following_analysis import (
    BootstrapStatus,
    bootstrap_trade_pnls,
)


def test_trade_bootstrap_is_deterministic_with_pre_registered_defaults() -> None:
    pnl = tuple(Decimal(value) for value in ("2", "1", "-1", "3", "-2", "1"))

    first = bootstrap_trade_pnls(pnl)
    second = bootstrap_trade_pnls(pnl)

    assert first == second
    assert first.seed == 42
    assert first.iterations == 2000
    assert first.confidence_percent == Decimal("95")
    assert first.total_pnl_interval is not None


def test_trade_bootstrap_does_not_invent_evidence_for_small_samples() -> None:
    result = bootstrap_trade_pnls((Decimal("1"),))

    assert result.status is BootstrapStatus.INSUFFICIENT_SAMPLE
    assert result.sample_size == 1
    assert result.total_pnl_interval is None
