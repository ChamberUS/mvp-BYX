from dataclasses import replace
from decimal import Decimal

from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.domain.models import MarketRegime
from adaptive_trader.futures.engine import FuturesBacktestEngine
from adaptive_trader.futures.models import (
    FuturesExitReason,
    FuturesSignal,
    FuturesSignalDirection,
)
from adaptive_trader.futures.pullback import PullbackContinuationFuturesAnalyzer
from adaptive_trader.strategy.pullback import (
    PullbackContinuationCore,
    PullbackReasonCode,
)
from tests.futures.conftest import make_candles, make_marks
from tests.futures.pullback_helpers import ApprovedCore
from tests.research.pullback_helpers import candle, parameters


def test_long_short_mode_enables_both_semantics_without_hedging(
    futures_config,
    start_time,
) -> None:
    candles = make_candles(
        start_time,
        tuple(str(100 + index) for index in range(10)),
    )
    config = replace(
        futures_config,
        trading_mode=TradingMode.FUTURES_LONG_SHORT,
        minimum_volume_ratio=Decimal("0"),
    )
    long_analyzer = PullbackContinuationFuturesAnalyzer(parameters())
    long_core = ApprovedCore(PositionSide.LONG)
    long_analyzer._core = long_core  # type: ignore[assignment]
    short_analyzer = PullbackContinuationFuturesAnalyzer(parameters())
    short_core = ApprovedCore(PositionSide.SHORT)
    short_analyzer._core = short_core  # type: ignore[assignment]

    long_signal = long_analyzer.analyze(candles, config, None)
    short_signal = short_analyzer.analyze(candles, config, None)
    managed = long_analyzer.analyze(candles, config, PositionSide.LONG)

    assert long_core.allow_long and long_core.allow_short
    assert short_core.allow_long and short_core.allow_short
    assert long_signal.direction is FuturesSignalDirection.ENTER_LONG
    assert short_signal.direction is FuturesSignalDirection.ENTER_SHORT
    assert managed.direction is FuturesSignalDirection.HOLD


def test_long_short_trace_reports_the_branch_matching_current_regime() -> None:
    evaluation = PullbackContinuationCore(parameters()).evaluate(
        latest=candle(1, "94"),
        previous=candle(0, "95"),
        regime=MarketRegime.TRENDING_DOWN,
        short_ema=Decimal("95"),
        long_ema=Decimal("100"),
        atr_value=Decimal("5"),
        volume_ratio=Decimal("1"),
        allow_long=True,
        allow_short=True,
    )

    assert (
        evaluation.trace.reason_code
        is PullbackReasonCode.TREND_PERSISTENCE_TOO_SHORT
    )
    assert evaluation.trace.trend_persistence_count == 1


class EntryThenRegimeLoss:
    def __init__(self) -> None:
        self.entered = False

    def analyze(self, candles, config, position_side):
        latest = candles[-1]
        if not self.entered:
            self.entered = True
            return FuturesSignal(
                signal_id="entry",
                symbol=latest.symbol,
                generated_at=latest.close_time,
                direction=FuturesSignalDirection.ENTER_LONG,
                regime=MarketRegime.TRENDING_UP,
                entry_price=latest.close,
                stop_loss=Decimal("90"),
                take_profit=Decimal("120"),
                rationale="fixture",
                reason_code="ENTER_LONG_APPROVED",
            )
        if position_side is PositionSide.LONG:
            return FuturesSignal(
                signal_id="regime-loss",
                symbol=latest.symbol,
                generated_at=latest.close_time,
                direction=FuturesSignalDirection.EXIT_LONG,
                regime=MarketRegime.RANGING,
                entry_price=latest.close,
                stop_loss=None,
                take_profit=None,
                rationale="fixture",
                reason_code="REGIME_LOSS_EXIT",
            )
        return FuturesSignal(
            signal_id="hold",
            symbol=latest.symbol,
            generated_at=latest.close_time,
            direction=FuturesSignalDirection.HOLD,
            regime=MarketRegime.RANGING,
            entry_price=latest.close,
            stop_loss=None,
            take_profit=None,
            rationale="fixture",
            reason_code="HOLD",
        )


def test_liquidation_keeps_priority_over_pending_regime_loss_exit(
    futures_config,
    start_time,
) -> None:
    candles = make_candles(
        start_time,
        ("100", "100", "100", "100", "100"),
        lows=("99", "99", "99", "89", "99"),
    )
    marks = make_marks(
        candles,
        lows=("99", "99", "99", "0.1", "99"),
    )

    result = FuturesBacktestEngine(
        futures_config,
        analyzer=EntryThenRegimeLoss(),
    ).run(candles, marks, ())

    assert result.leverage == Decimal("1")
    assert result.trades[0].exit_reason is FuturesExitReason.LIQUIDATION
