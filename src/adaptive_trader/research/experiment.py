"""Execution of independent research segments through BacktestEngine."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from adaptive_trader.backtest.engine import BacktestEngine
from adaptive_trader.backtest.models import BacktestResult
from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.protocols import MarketAnalyzer, RiskManager
from adaptive_trader.execution.backtest import BacktestExecutionConfig, BacktestOrderExecutor
from adaptive_trader.research.benchmarks import calculate_benchmarks
from adaptive_trader.research.models import DatasetSegment, SegmentRun
from adaptive_trader.risk.manager import DefaultRiskManager
from adaptive_trader.strategy.deterministic import DeterministicAnalyzer


class ResearchComponentFactory(Protocol):
    def __call__(
        self, config: TradingConfig
    ) -> tuple[MarketAnalyzer, RiskManager, BacktestOrderExecutor]: ...


def default_component_factory(
    config: TradingConfig,
) -> tuple[MarketAnalyzer, RiskManager, BacktestOrderExecutor]:
    strategy = DeterministicAnalyzer(
        short_period=config.short_ema_period,
        long_period=config.long_ema_period,
        minimum_volume_ratio=config.minimum_volume_ratio,
        maximum_atr_relative=config.maximum_atr_relative,
        stop_atr_multiple=config.stop_atr_multiple,
        target_r_multiple=config.target_r_multiple,
    )
    executor = BacktestOrderExecutor(
        BacktestExecutionConfig(
            maker_fee_bps=config.maker_fee_bps,
            taker_fee_bps=config.taker_fee_bps,
            slippage_bps=config.slippage_bps,
            spread_bps=config.spread_bps,
        )
    )
    return strategy, DefaultRiskManager(local_simulation=True), executor


class ResearchExperimentRunner:
    def __init__(
        self,
        *,
        component_factory: ResearchComponentFactory = default_component_factory,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._component_factory = component_factory
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    def run_segment(self, segment: DatasetSegment, config: TradingConfig) -> SegmentRun:
        run_config = replace(
            config,
            warmup_candles=max(config.warmup_candles, segment.warmup_candle_count + 1),
        )
        try:
            strategy, risk_manager, executor = self._component_factory(run_config)
            result = BacktestEngine(
                strategy=strategy,
                risk_manager=risk_manager,
                executor=executor,
                config=run_config,
                clock=self._clock,
            ).run(segment.candles)
            return SegmentRun(
                segment=segment,
                result=result,
                benchmarks=calculate_benchmarks(segment, run_config),
                parameters=run_config.as_dict(),
                warnings=tuple(result.warnings),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            return SegmentRun(
                segment=segment,
                result=None,
                benchmarks=(),
                parameters=run_config.as_dict(),
                failed=True,
                error=str(exc),
                warnings=(f"SEGMENT_FAILED: {segment.name}: {exc}",),
            )

    def run_segments(
        self, segments: Sequence[DatasetSegment], config: TradingConfig
    ) -> tuple[SegmentRun, ...]:
        return tuple(self.run_segment(segment, config) for segment in segments)


def result_for_run(run: SegmentRun) -> BacktestResult | None:
    return run.result
