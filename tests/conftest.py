from datetime import UTC, datetime
from decimal import Decimal

import pytest

from adaptive_trader.domain.models import (
    Candle,
    MarketRegime,
    MarketSignal,
    PortfolioSnapshot,
    SignalDirection,
)


@pytest.fixture
def analysis_time() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def candle(analysis_time: datetime) -> Candle:
    return Candle(
        symbol="ETHUSDT",
        timestamp=analysis_time,
        open=Decimal("2000"),
        high=Decimal("2050"),
        low=Decimal("1980"),
        close=Decimal("2030"),
        volume=Decimal("10.5"),
    )


@pytest.fixture
def buy_signal(analysis_time: datetime) -> MarketSignal:
    return MarketSignal(
        signal_id="signal-1",
        symbol="ETHUSDT",
        generated_at=analysis_time,
        direction=SignalDirection.BUY,
        regime=MarketRegime.TRENDING_UP,
        confidence=Decimal("0.8"),
        entry_price=Decimal("2000"),
        stop_loss=Decimal("1900"),
        take_profit=Decimal("2200"),
        suggested_quantity=Decimal("0.2"),
        rationale="test signal",
        analyzer_name="test",
    )


@pytest.fixture
def empty_portfolio(analysis_time: datetime) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id="snapshot-1",
        captured_at=analysis_time,
        cash_balance=Decimal("10000"),
        equity=Decimal("10000"),
        daily_loss=Decimal("0"),
        trades_today=0,
        positions=(),
    )
