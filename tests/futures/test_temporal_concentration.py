from datetime import UTC, datetime

from adaptive_trader.futures.temporal_robustness import concentration_metrics
from tests.futures.temporal_helpers import make_trade


def test_concentration_warns_when_top_three_change_sign() -> None:
    trades = (
        make_trade(datetime(2025, 1, 1, tzinfo=UTC), "100"),
        make_trade(datetime(2025, 1, 2, tzinfo=UTC), "-80"),
        make_trade(datetime(2025, 1, 3, tzinfo=UTC), "-10"),
    )
    metrics = concentration_metrics(trades)
    assert metrics["result_without_top_3"] == -90
    assert metrics["warning"] == "RESULT_DEPENDS_ON_FEW_TRADES"
