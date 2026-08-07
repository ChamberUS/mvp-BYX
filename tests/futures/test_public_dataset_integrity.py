from datetime import timedelta

from adaptive_trader.domain.models import Candle
from adaptive_trader.futures.integrity import (
    FuturesGapPolicy,
    ReadinessStatus,
    inspect_public_dataset,
)
from adaptive_trader.futures.models import FundingRate, FuturesCandle


def test_public_candles_are_distinct_from_spot_and_document_gaps(
    futures_candles,
    mark_prices,
    start_time,
) -> None:
    candles = futures_candles[:2] + futures_candles[3:]
    marks = mark_prices[:2] + mark_prices[3:]
    funding = (
        FundingRate(
            symbol="ETHUSDT",
            funding_time=start_time,
            funding_rate=__import__("decimal").Decimal("0.0001"),
        ),
    )
    integrity = inspect_public_dataset(
        candles,
        marks,
        funding,
        requested_start=candles[0].open_time,
        requested_end=candles[-1].open_time,
        gap_policy=FuturesGapPolicy.WARN,
    )
    assert all(isinstance(item, FuturesCandle) for item in candles)
    assert not any(isinstance(item, Candle) for item in candles)
    assert integrity.candles.spot_storage_collision_count == 0
    assert integrity.candles.gap_count == 1
    assert integrity.candles.missing_candle_count == 1
    assert integrity.readiness is ReadinessStatus.READY_WITH_WARNINGS
    assert integrity.candle_gaps[0].next_open_time == start_time + timedelta(hours=3)


def test_allow_documented_gap_rejects_only_unexplained_gap(
    futures_candles,
    mark_prices,
    start_time,
) -> None:
    candles = futures_candles[:2] + futures_candles[3:]
    marks = mark_prices[:2] + mark_prices[3:]
    funding = (
        FundingRate(
            symbol="ETHUSDT",
            funding_time=start_time,
            funding_rate=__import__("decimal").Decimal("0"),
        ),
    )
    integrity = inspect_public_dataset(
        candles,
        marks,
        funding,
        requested_start=candles[0].open_time,
        requested_end=candles[-1].open_time,
        gap_policy=FuturesGapPolicy.ALLOW_DOCUMENTED,
        documented_gap_starts=frozenset({start_time + timedelta(hours=3)}),
    )
    assert integrity.candles.unexplained_gap_count == 0
    assert integrity.readiness is ReadinessStatus.READY_WITH_WARNINGS
