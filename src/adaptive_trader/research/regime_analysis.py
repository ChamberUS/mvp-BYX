"""Approximate, point-in-time regime aggregation."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from adaptive_trader.backtest.models import BacktestResult
from adaptive_trader.domain.models import MarketRegime
from adaptive_trader.research.models import DatasetSegment, RegimeMetric
from adaptive_trader.strategy.regime import DeterministicRegimeClassifier


def _drawdown_percent(values: tuple[Decimal, ...]) -> Decimal:
    peak = Decimal("0")
    maximum = Decimal("0")
    for value in values:
        peak = max(peak, value)
        if peak:
            maximum = max(maximum, (peak - value) / peak * Decimal("100"))
    return maximum


def analyze_regimes(
    segment: DatasetSegment,
    result: BacktestResult,
    *,
    short_period: int,
    long_period: int,
    maximum_atr_relative: Decimal,
) -> tuple[RegimeMetric, ...]:
    classifier = DeterministicRegimeClassifier(
        short_period=short_period,
        long_period=long_period,
        maximum_atr_relative=maximum_atr_relative,
    )
    by_regime: dict[MarketRegime, list[int]] = defaultdict(list)
    for index, candle in enumerate(segment.candles):
        if candle.open_time < segment.evaluation_start_time:
            continue
        regime = classifier.classify(segment.candles[: index + 1]).regime
        by_regime[regime].append(index)
    metrics: list[RegimeMetric] = []
    evaluated_exposure = result.exposure_curve[1:]
    evaluated_equity = result.equity_curve[1:]
    for regime, indexes in sorted(by_regime.items(), key=lambda item: item[0].value):
        times = {
            segment.candles[index].open_time
            for index in indexes
        }
        trades = tuple(trade for trade in result.trades if trade.entry_time in times)
        pnl = sum((trade.net_pnl for trade in trades), Decimal("0"))
        gains = sum(trade.net_pnl > 0 for trade in trades)
        losses = tuple(trade.net_pnl for trade in trades if trade.net_pnl < 0)
        wins = tuple(trade.net_pnl for trade in trades if trade.net_pnl > 0)
        total = len(trades)
        exposure = tuple(
            evaluated_exposure[index - segment.warmup_candle_count]
            for index in indexes
            if index - segment.warmup_candle_count < len(evaluated_exposure)
        )
        equity = tuple(
            evaluated_equity[index - segment.warmup_candle_count]
            for index in indexes
            if index - segment.warmup_candle_count < len(evaluated_equity)
        )
        metrics.append(
            RegimeMetric(
                regime=regime,
                candle_count=len(indexes),
                entry_count=len({trade.entry_time for trade in trades}),
                closed_trade_count=total,
                net_return=pnl,
                win_rate=Decimal(gains) / Decimal(total) * Decimal("100") if total else None,
                profit_factor=sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0")))
                if losses
                else None,
                expectancy=pnl / Decimal(total) if total else None,
                maximum_drawdown_percent=_drawdown_percent(equity),
                exposure_percent=(
                    sum(exposure, Decimal("0")) / Decimal(len(exposure))
                    if exposure
                    else Decimal("0")
                ),
                total_costs=sum((trade.fees for trade in trades), Decimal("0")),
            )
        )
    return tuple(metrics)
