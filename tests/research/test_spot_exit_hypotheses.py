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
from adaptive_trader.research.spot_hypotheses import load_spot_hypothesis_catalog
from adaptive_trader.risk.manager import DefaultRiskManager


class BuyOnceAnalyzer:
    def __init__(self, target: Decimal = Decimal("120")) -> None:
        self._target = target

    def analyze(self, context: MarketContext) -> MarketSignal:
        direction = SignalDirection.BUY if len(context.candles) == 1 else SignalDirection.HOLD
        return MarketSignal(
            signal_id=f"signal-{len(context.candles)}",
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=direction,
            regime=MarketRegime.TRENDING_UP,
            confidence=Decimal("1") if direction is SignalDirection.BUY else Decimal("0"),
            entry_price=Decimal("100"),
            stop_loss=Decimal("90") if direction is SignalDirection.BUY else Decimal("0"),
            take_profit=self._target if direction is SignalDirection.BUY else Decimal("0"),
            suggested_quantity=Decimal("1") if direction is SignalDirection.BUY else Decimal("0"),
            rationale="pre-registered exit test",
            analyzer_name="test",
        )


def _candles(
    count: int,
    *,
    trigger_index: int | None = None,
    trigger: str = "",
) -> tuple[Candle, ...]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    result = []
    for index in range(count):
        low = Decimal("80") if index == trigger_index and trigger == "stop" else Decimal("99")
        high = Decimal("125") if index == trigger_index and trigger == "target" else Decimal("101")
        result.append(
            Candle(
                symbol="ETHUSDT",
                interval="1h",
                timestamp=start + timedelta(hours=index),
                open=Decimal("100"),
                high=high,
                low=low,
                close=Decimal("100"),
                volume=Decimal("10"),
            )
        )
    return tuple(result)


def _run(
    candles: tuple[Candle, ...],
    *,
    time_exit: int | None = None,
    target: Decimal = Decimal("120"),
) -> str:
    config = TradingConfig(
        interval="1h",
        short_ema_period=2,
        long_ema_period=3,
        atr_period=1,
        volume_period=1,
        warmup_candles=1,
        maker_fee_bps=Decimal("0"),
        taker_fee_bps=Decimal("0"),
        spread_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    result = BacktestEngine(
        strategy=BuyOnceAnalyzer(target),
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(BacktestExecutionConfig()),
        config=config,
        time_exit_candles=time_exit,
    ).run(candles)
    assert len(result.trades) == 1
    return result.trades[0].exit_reason


def test_baseline_and_forced_end() -> None:
    assert _run(_candles(5)) == "FORCED_END"


def test_time_exit_12_and_24() -> None:
    assert _run(_candles(20), time_exit=12) == "TIME_EXIT"
    assert _run(_candles(30), time_exit=24) == "TIME_EXIT"


def test_target_2_5_and_registered_combination() -> None:
    catalog = load_spot_hypothesis_catalog()
    target = catalog.by_id("SPOT_TARGET_R_2_5_V1")
    combined = catalog.by_id("SPOT_TIME_EXIT_12_TARGET_R_2_5_V1")

    assert target.resolved_target(Decimal("2")) == Decimal("2.5")
    assert combined.time_exit_candles == 12
    assert combined.resolved_target(Decimal("2")) == Decimal("2.5")


def test_stop_has_priority_over_time_exit() -> None:
    assert _run(_candles(5, trigger_index=2, trigger="stop"), time_exit=1) == "STOP_LOSS"


def test_target_has_priority_over_time_exit() -> None:
    assert _run(_candles(5, trigger_index=2, trigger="target"), time_exit=1) == "TAKE_PROFIT"
