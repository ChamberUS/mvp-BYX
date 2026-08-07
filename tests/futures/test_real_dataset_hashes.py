from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from adaptive_trader.futures.integrity import (
    FuturesGapPolicy,
    inspect_public_dataset,
)
from adaptive_trader.futures.models import FundingRate


def inspect(candles, marks, funding, start, end):
    return inspect_public_dataset(
        candles,
        marks,
        funding,
        requested_start=start,
        requested_end=end,
        gap_policy=FuturesGapPolicy.WARN,
    )


def test_real_hashes_change_with_each_content_family_and_period(
    futures_candles,
    mark_prices,
    start_time,
) -> None:
    funding = (
        FundingRate("ETHUSDT", start_time, Decimal("0.0001")),
    )
    baseline = inspect(
        futures_candles,
        mark_prices,
        funding,
        start_time,
        futures_candles[-1].open_time,
    )
    changed_candle = inspect(
        (replace(futures_candles[0], close=Decimal("100.5")), *futures_candles[1:]),
        mark_prices,
        funding,
        start_time,
        futures_candles[-1].open_time,
    )
    changed_mark = inspect(
        futures_candles,
        (replace(mark_prices[0], close=Decimal("100.5")), *mark_prices[1:]),
        funding,
        start_time,
        futures_candles[-1].open_time,
    )
    changed_funding = inspect(
        futures_candles,
        mark_prices,
        (replace(funding[0], funding_rate=Decimal("-0.0001")),),
        start_time,
        futures_candles[-1].open_time,
    )
    changed_period = inspect(
        futures_candles,
        mark_prices,
        funding,
        start_time,
        futures_candles[-1].open_time + timedelta(hours=1),
    )
    assert baseline.futures_candle_hash != changed_candle.futures_candle_hash
    assert baseline.mark_price_hash != changed_mark.mark_price_hash
    assert baseline.funding_hash != changed_funding.funding_hash
    assert len(
        {
            baseline.combined_dataset_hash,
            changed_candle.combined_dataset_hash,
            changed_mark.combined_dataset_hash,
            changed_funding.combined_dataset_hash,
            changed_period.combined_dataset_hash,
        }
    ) == 5
