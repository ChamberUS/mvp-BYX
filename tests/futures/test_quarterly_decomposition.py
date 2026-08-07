from adaptive_trader.futures.temporal_robustness import calendar_quarter_periods


def test_quarter_periods_are_civil_and_non_overlapping() -> None:
    periods = calendar_quarter_periods()
    assert len(periods) == 16
    assert periods[0][0] == "2022-Q1"
    assert periods[-1][0] == "2025-Q4"
    assert all(
        current[1] > previous[2]
        for previous, current in zip(periods, periods[1:], strict=False)
    )
