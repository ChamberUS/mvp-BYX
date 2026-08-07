from datetime import UTC, datetime

from adaptive_trader.futures.temporal_robustness import aggregate_period, rolling_periods
from tests.futures.temporal_helpers import make_result, make_trade


def test_rolling_windows_never_include_future_trade() -> None:
    periods = rolling_periods(
        datetime(2022, 1, 1, tzinfo=UTC),
        datetime(2022, 12, 31, 23, tzinfo=UTC),
        window_days=90,
        step_days=30,
    )
    first = periods[0]
    result = make_result(
        (
            make_trade(datetime(2022, 2, 1, tzinfo=UTC), "10"),
            make_trade(datetime(2022, 6, 1, tzinfo=UTC), "20"),
        ),
        start=datetime(2022, 1, 1, tzinfo=UTC),
    )
    row = aggregate_period(
        "TEST",
        result,
        (),
        period=first[0],
        start=first[1],
        end=first[2],
    )
    assert row["trades"] == 1
    assert row["net_pnl"] == 10
