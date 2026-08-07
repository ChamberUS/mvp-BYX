from datetime import UTC, datetime, timedelta

from adaptive_trader.futures.temporal_robustness import (
    BootstrapStatus,
    bootstrap_trade_pnls,
)
from tests.futures.temporal_helpers import make_trade


def test_bootstrap_is_deterministic_and_seed_changes_sample() -> None:
    trades = tuple(
        make_trade(datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=index), str(index - 5))
        for index in range(12)
    )
    first = bootstrap_trade_pnls("TEST", trades, iterations=100, seed=42)
    repeated = bootstrap_trade_pnls("TEST", trades, iterations=100, seed=42)
    changed = bootstrap_trade_pnls("TEST", trades, iterations=100, seed=43)
    assert first == repeated
    assert first.sample_fingerprint != changed.sample_fingerprint


def test_bootstrap_small_sample_is_inconclusive() -> None:
    trade = make_trade(datetime(2025, 1, 1, tzinfo=UTC), "1")
    result = bootstrap_trade_pnls("TEST", (trade,), iterations=10, seed=42)
    assert result.status is BootstrapStatus.INSUFFICIENT_SAMPLE
