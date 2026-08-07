"""Research-only orchestration for deterministic backtest experiments."""

from adaptive_trader.research.daily_aggregation import (
    DailyAggregationAction,
    DailyAggregationAudit,
    DailyAggregationConfig,
    DailyAggregationError,
    DailyAggregationIntegrity,
    DailyAggregationResult,
    DailyCandleAggregator,
    IncompleteDayPolicy,
)
from adaptive_trader.research.models import (
    DatasetSegment,
    GapPolicy,
    ResearchDataset,
    ResearchSummary,
    TemporalSplit,
    WalkForwardFold,
    WalkForwardPlan,
)
from adaptive_trader.research.periods import ConsumedTestError, ResearchPeriods

__all__ = [
    "DailyAggregationAction",
    "DailyAggregationAudit",
    "DailyAggregationConfig",
    "DailyAggregationError",
    "DailyAggregationIntegrity",
    "DailyAggregationResult",
    "DailyCandleAggregator",
    "DatasetSegment",
    "GapPolicy",
    "IncompleteDayPolicy",
    "ResearchDataset",
    "ResearchSummary",
    "TemporalSplit",
    "WalkForwardFold",
    "WalkForwardPlan",
    "ConsumedTestError",
    "ResearchPeriods",
]
