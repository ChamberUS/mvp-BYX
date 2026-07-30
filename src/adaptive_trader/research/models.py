"""Typed models used by the research laboratory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from adaptive_trader.backtest.models import BacktestResult
from adaptive_trader.domain.models import Candle, MarketRegime, SerializedValue


class GapPolicy(StrEnum):
    FAIL = "FAIL"
    WARN = "WARN"
    ALLOW = "ALLOW"


class WalkForwardMode(StrEnum):
    ROLLING = "ROLLING"
    EXPANDING = "EXPANDING"


class SelectionMode(StrEnum):
    FIXED_PARAMETERS = "FIXED_PARAMETERS"
    SELECT_FROM_PREDEFINED_GRID = "SELECT_FROM_PREDEFINED_GRID"


class SelectionCriterion(StrEnum):
    NET_RETURN = "net_return"
    PROFIT_FACTOR = "profit_factor"
    EXPECTANCY = "expectancy"
    MAXIMUM_DRAWDOWN_PERCENT = "maximum_drawdown_percent"
    RETURN_TO_DRAWDOWN = "return_to_drawdown"
    COMPOSITE_SCORE = "composite_score"


@dataclass(frozen=True, slots=True)
class ResearchDataset:
    dataset_id: str
    exchange: str
    symbol: str
    interval: str
    start_time: datetime
    end_time: datetime
    candle_count: int
    first_open_time: datetime
    last_close_time: datetime
    source: str
    created_at: datetime
    content_hash: str
    missing_candle_count: int
    duplicate_count: int
    gap_count: int
    warnings: tuple[str, ...]
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        if self.candle_count != len(self.candles):
            raise ValueError("dataset candle_count must match candles")
        if self.candle_count < 1:
            raise ValueError("dataset requires candles")
        if self.missing_candle_count < 0 or self.duplicate_count < 0 or self.gap_count < 0:
            raise ValueError("dataset counters must not be negative")


@dataclass(frozen=True, slots=True)
class DatasetSegment:
    name: str
    start_time: datetime
    end_time: datetime
    candle_count: int
    content_hash: str
    warmup_start_time: datetime
    evaluation_start_time: datetime
    candles: tuple[Candle, ...]
    evaluation_candles: tuple[Candle, ...]

    @property
    def warmup_candle_count(self) -> int:
        return len(self.candles) - len(self.evaluation_candles)

    def __post_init__(self) -> None:
        if self.candle_count != len(self.evaluation_candles):
            raise ValueError("segment candle_count must match evaluation candles")
        if not self.evaluation_candles:
            raise ValueError("segment requires evaluation candles")
        if not self.candles:
            raise ValueError("segment requires warmup or evaluation candles")
        if self.warmup_candle_count < 0:
            raise ValueError("segment warmup cannot be negative")


@dataclass(frozen=True, slots=True)
class TemporalSplit:
    split_id: str
    train: DatasetSegment
    validation: DatasetSegment
    test: DatasetSegment
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not (
            self.train.end_time <= self.validation.start_time
            and self.validation.end_time <= self.test.start_time
        ):
            raise ValueError("temporal split segments must be chronological")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_id: str
    train: DatasetSegment
    validation: DatasetSegment
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.train.end_time > self.validation.start_time:
            raise ValueError("walk-forward train must precede validation")


@dataclass(frozen=True, slots=True)
class WalkForwardPlan:
    plan_id: str
    mode: WalkForwardMode
    folds: tuple[WalkForwardFold, ...]
    train_days: int
    validation_days: int
    step_days: int
    warmup_candles: int


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    name: str
    initial_capital: Decimal
    final_capital: Decimal
    gross_return_percent: Decimal
    net_return_percent: Decimal
    maximum_drawdown_percent: Decimal
    volatility_percent: Decimal
    exposure_percent: Decimal
    total_costs: Decimal
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SegmentRun:
    segment: DatasetSegment
    result: BacktestResult | None
    benchmarks: tuple[BenchmarkResult, ...]
    parameters: dict[str, SerializedValue]
    failed: bool = False
    error: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WalkForwardFoldResult:
    fold: WalkForwardFold
    selected_parameters: dict[str, SerializedValue]
    train: SegmentRun | None
    validation: SegmentRun | None
    warnings: tuple[str, ...] = ()
    selection_status: str = "FIXED_PARAMETERS"


@dataclass(frozen=True, slots=True)
class RegimeMetric:
    regime: MarketRegime
    candle_count: int
    entry_count: int
    closed_trade_count: int
    net_return: Decimal
    win_rate: Decimal | None
    profit_factor: Decimal | None
    expectancy: Decimal | None
    maximum_drawdown_percent: Decimal
    exposure_percent: Decimal
    total_costs: Decimal


@dataclass(frozen=True, slots=True)
class RobustnessDiagnostics:
    train_validation_return_gap: Decimal | None
    train_validation_profit_factor_gap: Decimal | None
    train_validation_expectancy_gap: Decimal | None
    positive_fold_percent: Decimal
    benchmark_win_percent: Decimal
    best_trade_profit_percent: Decimal | None
    top_five_trade_profit_percent: Decimal | None
    result_without_best_trade: Decimal | None
    best_day_profit_percent: Decimal | None
    top_five_day_profit_percent: Decimal | None
    result_without_top_five_trades: Decimal | None
    positive_month_count: int
    negative_month_count: int
    longest_period_without_new_top_days: int
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResearchSummary:
    fold_count: int
    completed_fold_count: int
    failed_fold_count: int
    total_evaluated_candles: int
    total_entries: int
    total_closed_trades: int
    positive_fold_count: int
    negative_fold_count: int
    flat_fold_count: int
    positive_fold_percent: Decimal
    mean_net_return: Decimal | None
    median_net_return: Decimal | None
    worst_net_return: Decimal | None
    best_net_return: Decimal | None
    mean_max_drawdown: Decimal | None
    worst_max_drawdown: Decimal | None
    mean_profit_factor: Decimal | None
    median_profit_factor: Decimal | None
    mean_expectancy: Decimal | None
    benchmark_win_count: int
    benchmark_loss_count: int
    benchmark_tie_count: int
    benchmark_win_percent: Decimal
    parameter_selection_frequency: dict[str, int]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    experiment_id: str
    experiment_name: str
    executed_at: datetime
    project_version: str
    git_commit: str | None
    git_dirty: bool | None
    python_version: str
    operating_system: str
    dataset_id: str
    dataset_hash: str
    strategy_name: str
    strategy_version: str
    report_version: str
    configuration: dict[str, SerializedValue]
    strategy_parameters: dict[str, SerializedValue]
    risk_parameters: dict[str, SerializedValue]
    execution_parameters: dict[str, SerializedValue]
    cost_parameters: dict[str, SerializedValue]
    intrabar_policy: str
    gap_policy: str
    split: dict[str, SerializedValue]
    segment_hashes: dict[str, str]
    output_files: tuple[str, ...]
    warnings: tuple[str, ...]
    config_hash: str
    reproducibility_hash: str


@dataclass(frozen=True, slots=True)
class ResearchExperimentResult:
    experiment_id: str
    manifest: ExperimentManifest
    dataset: ResearchDataset
    segments: tuple[SegmentRun, ...]
    summary: ResearchSummary
    benchmarks: tuple[BenchmarkResult, ...]
    diagnostics: RobustnessDiagnostics
    warnings: tuple[str, ...]
