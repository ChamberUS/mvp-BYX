from datetime import timedelta

from adaptive_trader.futures.integrity import align_mark_prices, inspect_mark_prices


def test_alignment_uses_exact_or_previous_and_never_future(
    futures_candles,
    mark_prices,
) -> None:
    marks = mark_prices[:2] + mark_prices[3:]
    rows = align_mark_prices(futures_candles, marks)
    missing_hour = rows[2]
    assert missing_hour.match_type == "PREVIOUS"
    assert missing_hour.mark_open_time == mark_prices[1].open_time
    assert missing_hour.future_match is False
    integrity, _ = inspect_mark_prices(futures_candles, marks)
    assert integrity.previous_match_count == 1
    assert integrity.future_match_count == 0
    assert integrity.coverage_percent == 100


def test_alignment_reports_missing_without_using_future(
    futures_candles,
    mark_prices,
) -> None:
    rows = align_mark_prices(futures_candles, mark_prices[2:])
    assert rows[0].match_type == "MISSING"
    assert rows[0].mark_open_time is None
    assert rows[1].match_type == "MISSING"
    assert all(not item.future_match for item in rows)
    assert rows[2].alignment_delay_seconds == 0
    assert (
        futures_candles[2].open_time - futures_candles[1].open_time
        == timedelta(hours=1)
    )
