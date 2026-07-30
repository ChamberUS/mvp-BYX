from dataclasses import replace
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
from adaptive_trader.research.benchmarks import buy_and_hold
from adaptive_trader.research.datasets import explicit_split, validate_dataset
from adaptive_trader.research.experiment import ResearchExperimentRunner
from adaptive_trader.research.models import WalkForwardMode
from adaptive_trader.research.regime_analysis import analyze_regimes
from adaptive_trader.research.robustness import consolidate_runs
from adaptive_trader.research.splits import build_walk_forward_plan
from adaptive_trader.risk.manager import DefaultRiskManager


def make_candles(count: int = 120) -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(
            symbol="ETHUSDT",
            interval="1d",
            timestamp=start + timedelta(days=index),
            open=Decimal("100") + Decimal(index),
            high=Decimal("101") + Decimal(index),
            low=Decimal("99") + Decimal(index),
            close=Decimal("100") + Decimal(index),
            volume=Decimal("10"),
        )
        for index in range(count)
    )


def config(**updates: object) -> TradingConfig:
    values: dict[str, object] = {
        "interval": "1d",
        "short_ema_period": 2,
        "long_ema_period": 3,
        "atr_period": 2,
        "volume_period": 2,
        "warmup_candles": 1,
        "force_close_at_end": True,
    }
    values.update(updates)
    return TradingConfig(**values)  # type: ignore[arg-type]


class HoldAnalyzer:
    def __init__(self) -> None:
        self.first_context: MarketContext | None = None

    def analyze(self, context: MarketContext) -> MarketSignal:
        if self.first_context is None:
            self.first_context = context
        return MarketSignal(
            signal_id=f"hold-{context.latest_candle.open_time.isoformat()}",
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=SignalDirection.HOLD,
            regime=MarketRegime.UNKNOWN,
            confidence=Decimal("0"),
            entry_price=context.latest_candle.close,
            stop_loss=Decimal("0"),
            take_profit=Decimal("0"),
            suggested_quantity=Decimal("0"),
            rationale="regression hold",
            analyzer_name="warmup-regression",
        )


class BuyOnFirstEvaluationAnalyzer:
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, context: MarketContext) -> MarketSignal:
        self.calls += 1
        if self.calls > 1:
            direction = SignalDirection.HOLD
            stop = Decimal("0")
            target = Decimal("0")
            quantity = Decimal("0")
        else:
            direction = SignalDirection.BUY
            stop = context.latest_candle.close - Decimal("1")
            target = context.latest_candle.close + Decimal("20")
            quantity = Decimal("0.1")
        return MarketSignal(
            signal_id=f"signal-{self.calls}",
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=direction,
            regime=MarketRegime.TRENDING_UP,
            confidence=Decimal("1") if direction is SignalDirection.BUY else Decimal("0"),
            entry_price=context.latest_candle.close,
            stop_loss=stop,
            take_profit=target,
            suggested_quantity=quantity,
            rationale="regression signal",
            analyzer_name="warmup-regression",
        )


class SnapshotCollector:
    def __init__(self) -> None:
        self.snapshots = []

    def save_portfolio_snapshot(self, snapshot) -> None:
        self.snapshots.append(snapshot)

    def save_strategy_decision(self, record) -> None:
        pass

    def save_risk_decision(self, decision) -> None:
        pass

    def save_simulated_order(self, order) -> None:
        pass

    def save_fill(self, fill) -> None:
        pass

    def save_position(self, position) -> None:
        pass


def make_engine(analyzer: object, settings: TradingConfig, repository=None) -> BacktestEngine:
    return BacktestEngine(
        strategy=analyzer,  # type: ignore[arg-type]
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(
            BacktestExecutionConfig(
                taker_fee_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
                spread_bps=Decimal("0"),
            )
        ),
        config=settings,
        repository=repository,
    )


def test_warmup_is_excluded_from_result_state_and_metrics() -> None:
    candles = make_candles()
    evaluation_start = candles[100].open_time
    analyzer = HoldAnalyzer()
    collector = SnapshotCollector()
    result = make_engine(analyzer, config(), repository=collector).run(
        candles,
        evaluation_start_time=evaluation_start,
    )

    assert result.input_candle_count == 120
    assert result.warmup_candle_count == 100
    assert result.evaluated_candle_count == 20
    assert result.candle_count == 20
    assert result.input_start_time == candles[0].open_time
    assert result.requested_evaluation_start_time == evaluation_start
    assert result.start_time == evaluation_start
    assert result.evaluation_start_time == evaluation_start
    assert result.evaluation_end_time == candles[-1].open_time
    assert result.equity_curve == (Decimal("10000"),) + (Decimal("10000"),) * 20
    assert len(result.exposure_curve) == 20
    assert collector.snapshots
    assert all(snapshot.captured_at >= evaluation_start for snapshot in collector.snapshots)
    assert analyzer.first_context is not None
    assert analyzer.first_context.latest_candle == candles[100]
    assert analyzer.first_context.candles[0] == candles[0]


def test_warmup_cannot_create_pending_order() -> None:
    candles = make_candles()
    analyzer = BuyOnFirstEvaluationAnalyzer()
    result = make_engine(analyzer, config()).run(
        candles,
        evaluation_start_time=candles[100].open_time,
    )

    assert result.trades
    assert result.trades[0].entry_time == candles[101].open_time


def test_backtest_without_explicit_evaluation_preserves_legacy_scope() -> None:
    result = make_engine(HoldAnalyzer(), config(warmup_candles=100)).run(make_candles(3))

    assert result.input_candle_count == 3
    assert result.warmup_candle_count == 0
    assert result.evaluated_candle_count == 3
    assert result.start_time == make_candles(3)[0].open_time


def test_insufficient_warmup_shifts_effective_start_and_warns() -> None:
    candles = make_candles(5)
    result = make_engine(HoldAnalyzer(), config(warmup_candles=3)).run(
        candles,
        evaluation_start_time=candles[0].open_time,
    )

    assert result.start_time == candles[3].open_time
    assert result.warmup_candle_count == 3
    assert result.evaluated_candle_count == 2
    assert any("WARMUP_REDUCED_EVALUATION_PERIOD" in item for item in result.warnings)


def test_benchmark_ignores_changed_warmup_candles() -> None:
    candles = make_candles(40)
    changed = tuple(
        replace(
            candle,
            close=candle.close + Decimal("500"),
            open=candle.open + Decimal("500"),
            high=candle.high + Decimal("500"),
            low=candle.low + Decimal("500"),
        )
        if index < 5
        else candle
        for index, candle in enumerate(candles)
    )
    first_dataset = validate_dataset(candles)
    changed_dataset = validate_dataset(changed)
    first_split = explicit_split(
        first_dataset,
        train_start=candles[0].open_time,
        train_end=candles[10].open_time,
        validation_start=candles[10].open_time,
        validation_end=candles[15].open_time,
        test_start=candles[15].open_time,
        test_end=candles[-1].open_time + timedelta(days=1),
        warmup_candles=5,
    )
    changed_split = explicit_split(
        changed_dataset,
        train_start=changed[0].open_time,
        train_end=changed[10].open_time,
        validation_start=changed[10].open_time,
        validation_end=changed[15].open_time,
        test_start=changed[15].open_time,
        test_end=changed[-1].open_time + timedelta(days=1),
        warmup_candles=5,
    )
    settings = config()

    first_benchmark = buy_and_hold(first_split.test, settings)
    changed_benchmark = buy_and_hold(changed_split.test, settings)

    assert first_split.test.evaluated_candle_count == changed_split.test.evaluated_candle_count
    assert first_benchmark == changed_benchmark

    changed_evaluation = tuple(
        replace(
            candle,
            open=candle.open + Decimal("10"),
            high=candle.high + Decimal("10"),
            low=candle.low + Decimal("10"),
            close=candle.close + Decimal("10"),
        )
        if index == 15
        else candle
        for index, candle in enumerate(candles)
    )
    changed_evaluation_split = explicit_split(
        validate_dataset(changed_evaluation),
        train_start=changed_evaluation[0].open_time,
        train_end=changed_evaluation[10].open_time,
        validation_start=changed_evaluation[10].open_time,
        validation_end=changed_evaluation[15].open_time,
        test_start=changed_evaluation[15].open_time,
        test_end=changed_evaluation[-1].open_time + timedelta(days=1),
        warmup_candles=5,
    )
    assert buy_and_hold(first_split.test, settings) != buy_and_hold(
        changed_evaluation_split.test, settings
    )


def test_regime_metrics_count_only_evaluated_candles() -> None:
    candles = make_candles(20)
    dataset = validate_dataset(candles)
    split = explicit_split(
        dataset,
        train_start=candles[0].open_time,
        train_end=candles[10].open_time,
        validation_start=candles[10].open_time,
        validation_end=candles[15].open_time,
        test_start=candles[15].open_time,
        test_end=candles[-1].open_time + timedelta(days=1),
        warmup_candles=5,
    )
    run = ResearchExperimentRunner().run_segment(split.test, config())
    assert run.result is not None
    assert split.test.requested_evaluation_start_time == split.test.effective_evaluation_start_time
    assert split.test.input_candle_count == 10
    assert split.test.warmup_candle_count == 5
    metrics = analyze_regimes(
        split.test,
        run.result,
        short_period=2,
        long_period=3,
        maximum_atr_relative=Decimal("0.05"),
    )

    assert sum(item.candle_count for item in metrics) == split.test.evaluated_candle_count


def test_walk_forward_summary_sums_evaluated_candles_only() -> None:
    candles = make_candles(20)
    dataset = validate_dataset(candles)
    plan = build_walk_forward_plan(
        dataset,
        train_days=5,
        validation_days=2,
        step_days=2,
        warmup_candles=3,
        mode=WalkForwardMode.ROLLING,
    )
    runs = tuple(
        ResearchExperimentRunner().run_segment(fold.validation, config())
        for fold in plan.folds
    )
    summary = consolidate_runs(runs)

    assert summary.total_evaluated_candles == sum(
        run.segment.evaluated_candle_count for run in runs
    )
