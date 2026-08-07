from datetime import UTC, datetime

from adaptive_trader.futures.temporal_robustness import _transition_rows
from tests.futures.temporal_helpers import make_context, make_result, make_trade


def test_regime_transition_keeps_entry_and_exit_separate() -> None:
    trade = make_trade(datetime(2023, 1, 2, tzinfo=UTC), "10", holding_hours=1)
    contexts = (
        make_context(trade.entry_time, regime="TRENDING_UP"),
        make_context(
            trade.exit_time.replace(minute=0, second=0, microsecond=0),
            regime="RANGING",
        ),
    )
    rows = _transition_rows("TEST", make_result((trade,)), (), contexts)
    assert rows[0]["transition"] == "TRENDING_UP->RANGING"
