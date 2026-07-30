from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import Candle


@pytest.fixture
def daily_candles() -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(
            symbol="ETHUSDT",
            exchange="BINANCE",
            interval="1d",
            timestamp=start + timedelta(days=index),
            open=Decimal(str(100 + index)),
            high=Decimal(str(101 + index)),
            low=Decimal(str(99 + index)),
            close=Decimal(str(100 + index)),
            volume=Decimal("10"),
        )
        for index in range(12)
    )


@pytest.fixture
def research_config() -> TradingConfig:
    return TradingConfig(
        interval="1d",
        short_ema_period=2,
        long_ema_period=3,
        atr_period=1,
        volume_period=1,
        warmup_candles=1,
        force_close_at_end=True,
    )
