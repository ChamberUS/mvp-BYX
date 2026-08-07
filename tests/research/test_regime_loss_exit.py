from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trader.backtest.engine import BacktestEngine
from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import (
    Candle,
    MarketContext,
    MarketRegime,
    MarketSignal,
    SignalDirection,
)
from adaptive_trader.execution.backtest import (
    BacktestExecutionConfig,
    BacktestOrderExecutor,
)
from adaptive_trader.risk.manager import DefaultRiskManager


class BuyThenRegimeExit:
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, context: MarketContext) -> MarketSignal:
        self.calls += 1
        if self.calls == 1:
            return MarketSignal(
                signal_id="entry",
                symbol=context.symbol,
                generated_at=context.created_at,
                direction=SignalDirection.BUY,
                regime=MarketRegime.TRENDING_UP,
                confidence=Decimal("1"),
                entry_price=Decimal("100"),
                stop_loss=Decimal("90"),
                take_profit=Decimal("120"),
                suggested_quantity=Decimal("1"),
                rationale="fixture entry",
                analyzer_name="pullback",
                reason_code="ENTER_LONG_APPROVED",
            )
        if self.calls == 2:
            return MarketSignal(
                signal_id="regime-exit",
                symbol=context.symbol,
                generated_at=context.created_at,
                direction=SignalDirection.SELL,
                regime=MarketRegime.RANGING,
                confidence=Decimal("1"),
                entry_price=context.latest_candle.close,
                stop_loss=context.latest_candle.close,
                take_profit=context.latest_candle.close,
                suggested_quantity=Decimal("1"),
                rationale="regime loss",
                analyzer_name="pullback",
                reason_code="REGIME_LOSS_EXIT",
            )
        return MarketSignal(
            signal_id=f"hold-{self.calls}",
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=SignalDirection.HOLD,
            regime=MarketRegime.RANGING,
            confidence=Decimal("0"),
            entry_price=context.latest_candle.close,
            stop_loss=Decimal("0"),
            take_profit=Decimal("0"),
            suggested_quantity=Decimal("0"),
            rationale="hold",
            analyzer_name="pullback",
        )


def make_candle(
    index: int,
    *,
    open_price: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100",
) -> Candle:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    return Candle(
        symbol="ETHUSDT",
        interval="1h",
        timestamp=start + timedelta(hours=index),
        close_time=start + timedelta(hours=index + 1) - timedelta(milliseconds=1),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("10"),
    )


def engine() -> BacktestEngine:
    config = TradingConfig(
        interval="1h",
        short_ema_period=1,
        long_ema_period=2,
        atr_period=1,
        volume_period=1,
        warmup_candles=1,
        force_close_at_end=True,
    )
    return BacktestEngine(
        strategy=BuyThenRegimeExit(),
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(
            BacktestExecutionConfig(
                taker_fee_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
                spread_bps=Decimal("0"),
            )
        ),
        config=config,
        allow_strategy_exit=True,
    )


def test_regime_loss_executes_only_on_the_following_candle_open() -> None:
    candles = tuple(make_candle(index) for index in range(4))

    result = engine().run(candles)

    assert result.trades[0].exit_reason == "REGIME_LOSS_EXIT"
    assert result.trades[0].exit_time == candles[2].open_time


@pytest.mark.parametrize(
    ("high", "low", "expected"),
    [
        ("121", "89", "STOP_LOSS"),
        ("121", "99", "TAKE_PROFIT"),
    ],
)
def test_stop_and_target_keep_priority_over_pending_regime_exit(
    high: str,
    low: str,
    expected: str,
) -> None:
    candles = (
        make_candle(0),
        make_candle(1),
        make_candle(2, high=high, low=low),
        make_candle(3),
    )

    result = engine().run(candles)

    assert result.trades[0].exit_reason == expected
    assert result.trades[0].exit_time == candles[2].close_time
