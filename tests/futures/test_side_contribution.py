from datetime import UTC, datetime

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.futures.temporal_robustness import _side_contribution_rows
from tests.futures.temporal_helpers import make_context, make_result, make_trade


def test_long_and_short_contributions_are_never_combined() -> None:
    long_trade = make_trade(datetime(2025, 2, 1, tzinfo=UTC), "10")
    short_trade = make_trade(
        datetime(2025, 3, 1, tzinfo=UTC),
        "-5",
        side=PositionSide.SHORT,
    )
    contexts = (
        make_context(long_trade.entry_time),
        make_context(short_trade.entry_time),
    )
    rows = _side_contribution_rows(
        "TEST",
        make_result((long_trade, short_trade)),
        contexts,
    )
    year_rows = tuple(
        row for row in rows if row["dimension"] == "YEAR" and row["period"] == "2025"
    )
    assert {row["side"] for row in year_rows} == {"LONG", "SHORT"}
