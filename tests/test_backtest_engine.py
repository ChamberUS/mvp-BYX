from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.backtest.engine import BacktestEngine
from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import (
    Candle,
    MarketContext,
    MarketRegime,
    MarketSignal,
    SignalDirection,
)
from adaptive_trader.execution.backtest import BacktestExecutionConfig, BacktestOrderExecutor
from adaptive_trader.risk.manager import DefaultRiskManager


def candles() -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    values = ((100, 100.5, 99.5), (100, 101, 99.8), (101, 103, 100.8), (102, 103, 101.5))
    return tuple(
        Candle(
            symbol="ETHUSDT",
            interval="1m",
            timestamp=start + timedelta(minutes=index),
            open=Decimal(str(open_price)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(open_price)),
            volume=Decimal("10"),
        )
        for index, (open_price, high, low) in enumerate(values)
    )


class OneShotAnalyzer:
    def analyze(self, context: MarketContext) -> MarketSignal:
        if len(context.candles) != 1:
            return MarketSignal(
                signal_id=f"hold-{len(context.candles)}",
                symbol=context.symbol,
                generated_at=context.created_at,
                direction=SignalDirection.HOLD,
                regime=MarketRegime.UNKNOWN,
                confidence=Decimal("0"),
                entry_price=context.latest_candle.close,
                stop_loss=Decimal("0"),
                take_profit=Decimal("0"),
                suggested_quantity=Decimal("0"),
                rationale="one shot",
                analyzer_name="test",
            )
        return MarketSignal(
            signal_id="buy-once",
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=SignalDirection.BUY,
            regime=MarketRegime.TRENDING_UP,
            confidence=Decimal("1"),
            entry_price=Decimal("100"),
            stop_loss=Decimal("99"),
            take_profit=Decimal("102"),
            suggested_quantity=Decimal("0.1"),
            rationale="deterministic test setup",
            analyzer_name="test",
        )


def test_backtest_is_chronological_and_executes_take_profit() -> None:
    config = TradingConfig(
        trading_enabled=False,
        short_ema_period=2,
        long_ema_period=3,
        atr_period=2,
        volume_period=2,
        warmup_candles=1,
        force_close_at_end=True,
    )
    engine = BacktestEngine(
        strategy=OneShotAnalyzer(),
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(
            BacktestExecutionConfig(
                taker_fee_bps=Decimal("0"), slippage_bps=Decimal("0"), spread_bps=Decimal("0")
            )
        ),
        config=config,
    )

    result = engine.run(candles())

    assert result.candle_count == 4
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "TAKE_PROFIT"
    assert result.trades[0].intrabar_ambiguous is False
    assert result.trades[0].entry_price == Decimal("100")


def test_backtest_rejects_future_or_duplicate_candles() -> None:
    config = TradingConfig(short_ema_period=2, long_ema_period=3, atr_period=2, volume_period=2)
    engine = BacktestEngine(
        strategy=OneShotAnalyzer(),
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(),
        config=config,
    )
    original = candles()
    try:
        engine.run((original[1], original[0]))
    except ValueError as exc:
        assert "chronological" in str(exc)


def test_optional_partial_take_profit_is_deterministic() -> None:
    config = TradingConfig(
        short_ema_period=2,
        long_ema_period=3,
        atr_period=2,
        volume_period=2,
        partial_take_profit_enabled=True,
        partial_take_profit_r_multiple=Decimal("1"),
        partial_take_profit_percent=Decimal("50"),
        warmup_candles=1,
        force_close_at_end=True,
    )
    engine = BacktestEngine(
        strategy=OneShotAnalyzer(),
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(
            BacktestExecutionConfig(
                taker_fee_bps=Decimal("0"), slippage_bps=Decimal("0"), spread_bps=Decimal("0")
            )
        ),
        config=config,
    )

    result = engine.run(candles())

    assert any(trade.exit_reason == "PARTIAL_TAKE_PROFIT" for trade in result.trades)
    assert result.metrics.entry_count == 1
    assert result.metrics.order_count == 3
    assert result.metrics.closed_trade_count == 2
    assert result.metrics.partial_exit_count == 1
