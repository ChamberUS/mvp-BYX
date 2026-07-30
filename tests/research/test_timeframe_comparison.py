from datetime import UTC, datetime
from decimal import Decimal

from adaptive_trader.domain.models import Candle
from adaptive_trader.research.datasets import validate_dataset


def test_missing_timeframe_is_representable_without_network() -> None:
    candle = Candle(
        symbol="ETHUSDT",
        interval="1h",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )
    dataset = validate_dataset((candle,))
    assert dataset.interval == "1h"
