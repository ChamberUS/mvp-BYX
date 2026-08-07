from datetime import UTC, datetime

from adaptive_trader.futures.temporal_robustness import _regime_rows
from tests.futures.temporal_helpers import make_context, make_result, make_trade


def test_temporal_regime_uses_entry_context_only() -> None:
    trade = make_trade(datetime(2023, 1, 2, tzinfo=UTC), "10")
    context = make_context(trade.entry_time, regime="TRENDING_UP")
    rows = _regime_rows("TEST", make_result((trade,)), (), (context,))
    trending = next(row for row in rows if row["regime"] == "TRENDING_UP")
    assert trending["entry_count"] == 1
    assert trending["net_pnl"] == 10
