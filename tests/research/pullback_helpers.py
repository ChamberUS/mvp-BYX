from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.models import Candle, MarketRegime
from adaptive_trader.research.pullback_analysis import (
    PullbackClosedTrade,
    PullbackRun,
    WalkForwardSummary,
)
from adaptive_trader.strategy.pullback import (
    PullbackContinuationCore,
    PullbackEvaluation,
    PullbackParameters,
)

START = datetime(2023, 1, 1, tzinfo=UTC)


def parameters(**updates: object) -> PullbackParameters:
    values: dict[str, object] = {
        "trend_persistence_candles": 3,
        "pullback_min_candles": 1,
        "pullback_max_candles": 6,
        "minimum_pullback_depth_atr": Decimal("0.10"),
        "maximum_pullback_depth_atr": Decimal("1"),
        "maximum_entry_extension_atr": Decimal("1"),
        "minimum_volume_ratio": Decimal("1"),
        "maximum_atr_relative": Decimal("0.05"),
        "stop_atr_multiple": Decimal("2"),
        "target_r_multiple": Decimal("2"),
    }
    values.update(updates)
    return PullbackParameters(**values)  # type: ignore[arg-type]


def candle(index: int, close: str) -> Candle:
    price = Decimal(close)
    return Candle(
        symbol="ETHUSDT",
        interval="1h",
        timestamp=START + timedelta(hours=index),
        close_time=START + timedelta(hours=index + 1) - timedelta(milliseconds=1),
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("100"),
    )


def evaluate_long(
    core: PullbackContinuationCore,
    index: int,
    close: str,
    previous_close: str,
    *,
    short_ema: str = "105",
    long_ema: str = "100",
    atr_value: str = "10",
    volume_ratio: str = "1",
    regime: MarketRegime = MarketRegime.TRENDING_UP,
) -> PullbackEvaluation:
    return core.evaluate(
        latest=candle(index, close),
        previous=candle(index - 1, previous_close),
        regime=regime,
        short_ema=Decimal(short_ema),
        long_ema=Decimal(long_ema),
        atr_value=Decimal(atr_value),
        volume_ratio=Decimal(volume_ratio),
        allow_long=True,
        allow_short=False,
    )


def seed_long_trend(
    core: PullbackContinuationCore,
    count: int = 3,
    *,
    short_ema: str = "105",
    long_ema: str = "100",
    close: str = "106",
    atr_value: str = "10",
) -> int:
    previous = close
    for index in range(count):
        evaluate_long(
            core,
            index,
            close,
            previous,
            short_ema=short_ema,
            long_ema=long_ema,
            atr_value=atr_value,
        )
        previous = close
    return count


def closed_trade(
    net_pnl: str,
    *,
    side: str = "LONG",
    variant_id: str = "PULLBACK_BASE",
    period: str = "VALIDATION",
) -> PullbackClosedTrade:
    pnl = Decimal(net_pnl)
    return PullbackClosedTrade(
        market="SPOT",
        mode="LONG",
        variant_id=variant_id,
        period=period,
        scenario="BASE",
        side=side,
        entry_time=START,
        exit_time=START + timedelta(hours=2),
        entry_price=Decimal("100"),
        exit_price=Decimal("101"),
        quantity=Decimal("1"),
        gross_pnl=pnl,
        fees=Decimal("0"),
        execution_costs=Decimal("0"),
        funding_paid=Decimal("0"),
        funding_received=Decimal("0"),
        net_funding=Decimal("0"),
        liquidation_fee=Decimal("0"),
        net_pnl=pnl,
        holding_candles=2,
        exit_reason="TAKE_PROFIT" if pnl > 0 else "STOP_LOSS",
        liquidated=False,
    )


def run_with_return(
    scenario: str,
    net_return: str,
    *,
    gross_pnl: str = "10",
    total_costs: str = "1",
    net_funding: str = "0",
) -> PullbackRun:
    net = Decimal(net_return)
    return PullbackRun(
        market="SPOT",
        mode="LONG",
        variant_id="PULLBACK_BASE",
        period="VALIDATION",
        scenario=scenario,
        evaluation_start=START,
        evaluation_end=START + timedelta(days=90),
        initial_capital=Decimal("10000"),
        final_capital=Decimal("10000") + net,
        gross_pnl=Decimal(gross_pnl),
        net_pnl=net,
        net_return_percent=net,
        maximum_drawdown_percent=Decimal("1"),
        total_costs=Decimal(total_costs),
        fees=Decimal(total_costs),
        funding_paid=Decimal("0"),
        funding_received=Decimal(net_funding),
        net_funding=Decimal(net_funding),
        liquidation_count=0,
        evaluated_candles=100,
        entry_count=1,
        approvals=1,
        executions=1,
        trend_detected=10,
        persistence_accepted=5,
        pullbacks_detected=3,
        pullbacks_valid=2,
        resumptions=1,
        long_signals=1,
        short_signals=0,
        buy_and_hold_return_percent=Decimal("2"),
        long_pnl=net,
        short_pnl=Decimal("0"),
        trades=(closed_trade(net_return),),
        pullback_traces=(),
        reason_counts=(),
        warnings=(),
    )


def fold_summary(
    variant_id: str,
    median: str,
    positive_percent: str,
    *,
    period: str = "DEVELOPMENT",
    scenario: str = "BASE",
    drawdown: str = "2",
    zero_percent: str = "0",
    concentration: str = "20",
    trades: int = 20,
) -> WalkForwardSummary:
    return WalkForwardSummary(
        market="SPOT",
        mode="LONG",
        variant_id=variant_id,
        period=period,
        scenario=scenario,
        fold_count=4,
        positive_fold_count=2,
        positive_fold_percent=Decimal(positive_percent),
        zero_trade_fold_count=0,
        zero_trade_fold_percent=Decimal(zero_percent),
        median_return_percent=Decimal(median),
        mean_return_percent=Decimal(median),
        worst_fold_return_percent=Decimal("-1"),
        best_fold_return_percent=Decimal("2"),
        trades=trades,
        maximum_drawdown_percent=Decimal(drawdown),
        total_costs=Decimal("10"),
        net_funding=Decimal("0"),
        best_trade_concentration_percent=Decimal(concentration),
        net_pnl_without_top_three=Decimal("1"),
        long_pnl=Decimal("10"),
        short_pnl=Decimal("0"),
    )
