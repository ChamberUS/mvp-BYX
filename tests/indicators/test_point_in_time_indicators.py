from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trader.domain.models import Candle
from adaptive_trader.indicators.trend_following import (
    sma_200,
    sma_close,
    trend_following_indicators,
)


def _candles(count: int) -> tuple[Candle, ...]:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(
            symbol="ETHUSDT",
            interval="1d",
            timestamp=start + timedelta(days=index),
            close_time=start + timedelta(days=index + 1) - timedelta(milliseconds=1),
            open=Decimal(index + 1),
            high=Decimal(index + 2),
            low=Decimal(index + 1),
            close=Decimal(index + 1),
            volume=Decimal("1"),
        )
        for index in range(count)
    )


def test_sma_200_includes_current_candle_and_uses_decimal() -> None:
    source = _candles(200)
    baseline = sma_200(source)
    changed = sma_200(
        (*source[:-1], replace(source[-1], high=Decimal("401"), close=Decimal("400")))
    )

    assert baseline == Decimal("100.5")
    assert isinstance(baseline, Decimal)
    assert changed == (sum(range(1, 200)) + Decimal("400")) / Decimal("200")
    assert changed != baseline


def test_sma_is_unavailable_during_first_199_daily_candles() -> None:
    assert sma_200(_candles(199)) is None
    assert sma_close(_candles(2), 3) is None


def test_indicator_bundle_uses_independent_entry_and_exit_windows() -> None:
    source = _candles(200)

    indicators = trend_following_indicators(
        source,
        sma_period=200,
        entry_period=20,
        exit_period=10,
    )

    assert indicators.sma == Decimal("100.5")
    assert indicators.entry_channel is not None
    assert indicators.exit_channel is not None
    assert indicators.entry_channel.high == Decimal("200")
    assert indicators.entry_channel.low == Decimal("180")
    assert indicators.exit_channel.high == Decimal("200")
    assert indicators.exit_channel.low == Decimal("190")


@pytest.mark.parametrize("period", [0, -1])
def test_point_in_time_indicators_reject_non_positive_period(period: int) -> None:
    source = _candles(2)

    with pytest.raises(ValueError, match="positive"):
        sma_close(source, period)
    with pytest.raises(ValueError, match="positive"):
        trend_following_indicators(source, entry_period=period)
