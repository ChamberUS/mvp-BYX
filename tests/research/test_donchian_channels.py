from dataclasses import replace
from decimal import Decimal

from adaptive_trader.indicators.trend_following import donchian_channel
from tests.research.test_sma_200 import _daily_candles


def test_donchian_10_and_20_use_only_days_before_current_candle() -> None:
    source = _daily_candles(21)
    current_extreme = replace(source[-1], high=Decimal("999"), low=Decimal("0.1"))
    prefix = (*source[:-1], current_extreme)

    channel_20 = donchian_channel(prefix, 20)
    channel_10 = donchian_channel(prefix, 10)

    assert channel_20 is not None
    assert channel_10 is not None
    assert channel_20.high == Decimal("21")
    assert channel_20.low == Decimal("1")
    assert channel_10.high == Decimal("21")
    assert channel_10.low == Decimal("11")
    assert channel_20.high != current_extreme.high
    assert channel_20.low != current_extreme.low
