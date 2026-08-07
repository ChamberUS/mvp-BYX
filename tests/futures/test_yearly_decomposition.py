from datetime import UTC, datetime

from adaptive_trader.futures.temporal_robustness import (
    aggregate_period,
    calendar_year_periods,
)
from tests.futures.temporal_helpers import make_result, make_trade


def test_yearly_metrics_exclude_warmup_and_do_not_mix_years() -> None:
    trades = (
        make_trade(datetime(2022, 1, 2, tzinfo=UTC), "50"),
        make_trade(datetime(2022, 2, 1, tzinfo=UTC), "100"),
        make_trade(datetime(2023, 2, 1, tzinfo=UTC), "-20"),
    )
    result = make_result(trades)
    periods = calendar_year_periods()
    row = aggregate_period(
        "TEST",
        result,
        (),
        period=periods[0][0],
        start=periods[0][1],
        end=periods[0][2],
    )
    assert row["trades"] == 1
    assert row["net_pnl"] == 100
