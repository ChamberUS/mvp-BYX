"""Offline temporal robustness diagnostics for fixed Futures 1x configurations."""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from statistics import median

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.market import PositionSide
from adaptive_trader.domain.models import MarketRegime, serialize_model
from adaptive_trader.futures.datasets import validate_futures_dataset
from adaptive_trader.futures.engine import FuturesBacktestEngine
from adaptive_trader.futures.integrity import (
    FuturesGapPolicy,
    PublicDatasetIntegrity,
    ReadinessStatus,
    inspect_public_dataset,
)
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FuturesBacktestConfig,
    FuturesBacktestResult,
    FuturesCandle,
    FuturesExitReason,
    FuturesSignalDirection,
    FuturesTrade,
)
from adaptive_trader.futures.real_validation import (
    PredefinedFuturesVariant,
    base_futures_config,
    futures_cost_scenarios,
    predefined_futures_variants,
    variant_config,
)
from adaptive_trader.storage.sqlite import DatabaseRepository
from adaptive_trader.strategy.regime import DeterministicRegimeClassifier

type Row = dict[str, object]

_HOUR = timedelta(hours=1)
_EXPECTED_START = datetime(2022, 1, 1, tzinfo=UTC)
_EXPECTED_END = datetime(2025, 12, 31, 23, tzinfo=UTC)
_CONSUMED_START = datetime(2026, 1, 1, tzinfo=UTC)


class BootstrapStatus(StrEnum):
    POSITIVE_UNCERTAIN = "POSITIVE_UNCERTAIN"
    NEGATIVE_UNCERTAIN = "NEGATIVE_UNCERTAIN"
    INCLUDES_ZERO = "INCLUDES_ZERO"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


class StabilityStatus(StrEnum):
    STABLE = "STABLE"
    MIXED = "MIXED"
    UNSTABLE = "UNSTABLE"
    INCONCLUSIVE = "INCONCLUSIVE"


class TemporalClassification(StrEnum):
    ROBUSTNESS_SIGNAL = "ROBUSTNESS_SIGNAL"
    REGIME_SPECIFIC_SIGNAL = "REGIME_SPECIFIC_SIGNAL"
    NON_STATIONARY = "NON_STATIONARY"
    NO_EDGE = "NO_EDGE"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class TemporalRobustnessRequest:
    symbol: str
    interval: str
    start: datetime
    end: datetime
    dataset_hash: str
    leverage: Decimal
    bootstrap_iterations: int = 2000
    bootstrap_seed: int = 42
    funding_dominance_percent: Decimal = Decimal("50")

    def validate(self) -> None:
        if self.symbol != "ETHUSDT" or self.interval != "1h":
            raise ValueError("Sprint 3A.6 is pre-registered for ETHUSDT 1h only")
        if self.start != _EXPECTED_START or self.end != _EXPECTED_END:
            raise ValueError("Sprint 3A.6 periods must match 2022-2025 exactly")
        if self.end >= _CONSUMED_START:
            raise ValueError("Futures 2026 data is forbidden in Sprint 3A.6")
        if self.leverage != Decimal("1"):
            raise ValueError("Sprint 3A.6 temporal robustness permits only leverage 1")
        if not 1 <= self.bootstrap_iterations <= 10_000:
            raise ValueError("bootstrap iterations must be between 1 and 10000")
        if len(self.dataset_hash) != 64:
            raise ValueError("dataset hash must be a SHA-256 hexadecimal digest")
        if self.funding_dominance_percent <= 0:
            raise ValueError("funding dominance threshold must be positive")


@dataclass(frozen=True, slots=True)
class TemporalCandleContext:
    open_time: datetime
    regime: str
    volatility_bucket: str
    atr_relative: Decimal | None
    return_24h: Decimal | None
    return_7d: Decimal | None
    return_30d: Decimal | None
    long_ema_distance_percent: Decimal | None
    long_ema_slope_percent: Decimal | None
    directional_persistence: Decimal | None


@dataclass(frozen=True, slots=True)
class BootstrapSummary:
    configuration: str
    trade_count: int
    iterations: int
    seed: int
    block_by_month: bool
    status: BootstrapStatus
    sample_fingerprint: str
    intervals: dict[str, dict[str, Decimal]]


@dataclass(frozen=True, slots=True)
class TemporalRobustnessBundle:
    experiment_id: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: Decimal
    request: TemporalRobustnessRequest
    integrity: PublicDatasetIntegrity
    variants: tuple[PredefinedFuturesVariant, ...]
    volatility_boundaries: tuple[Decimal, Decimal, Decimal]
    yearly_rows: tuple[Row, ...]
    quarterly_rows: tuple[Row, ...]
    rolling_rows: tuple[Row, ...]
    walk_forward_rows: tuple[Row, ...]
    boundary_rows: tuple[Row, ...]
    leave_one_year_out_rows: tuple[Row, ...]
    regime_rows: tuple[Row, ...]
    transition_rows: tuple[Row, ...]
    volatility_rows: tuple[Row, ...]
    market_context_rows: tuple[Row, ...]
    side_rows: tuple[Row, ...]
    funding_rows: tuple[Row, ...]
    cost_rows: tuple[Row, ...]
    concentration_rows: tuple[Row, ...]
    bootstrap_summaries: tuple[BootstrapSummary, ...]
    scorecards: tuple[Row, ...]
    classifications: tuple[Row, ...]
    explanations_2025: tuple[Row, ...]
    warnings: tuple[str, ...]
    reproducibility_hash: str


def _sign(value: Decimal) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _datetime_field(row: Row, key: str) -> datetime:
    value = row[key]
    if not isinstance(value, datetime):
        raise TypeError(f"{key} must be datetime")
    return value


def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def _percentile(values: tuple[Decimal, ...], quantile: Decimal) -> Decimal:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not Decimal("0") <= quantile <= Decimal("1"):
        raise ValueError("quantile must be in [0, 1]")
    ordered = sorted(values)
    position = Decimal(len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def volatility_quantile_boundaries(
    values: tuple[Decimal, ...],
) -> tuple[Decimal, Decimal, Decimal]:
    return (
        _percentile(values, Decimal("0.25")),
        _percentile(values, Decimal("0.50")),
        _percentile(values, Decimal("0.75")),
    )


def volatility_bucket(
    value: Decimal | None,
    boundaries: tuple[Decimal, Decimal, Decimal],
) -> str:
    if value is None:
        return "UNKNOWN"
    low, medium, high = boundaries
    if value <= low:
        return "LOW"
    if value <= medium:
        return "MEDIUM"
    if value <= high:
        return "HIGH"
    return "EXTREME"


def _ema_series(values: tuple[Decimal, ...], period: int) -> tuple[Decimal | None, ...]:
    output: list[Decimal | None] = []
    current: Decimal | None = None
    multiplier = Decimal("2") / Decimal(period + 1)
    for index, value in enumerate(values):
        if index + 1 < period:
            output.append(None)
            continue
        if current is None:
            current = sum(values[:period], Decimal("0")) / Decimal(period)
        else:
            current = (value - current) * multiplier + current
        output.append(current)
    return tuple(output)


def compute_temporal_contexts(
    candles: tuple[FuturesCandle, ...],
    config: FuturesBacktestConfig,
    *,
    development_end: datetime,
) -> tuple[tuple[TemporalCandleContext, ...], tuple[Decimal, Decimal, Decimal]]:
    closes = tuple(item.close for item in candles)
    long_ema = _ema_series(closes, config.long_ema_period)
    true_ranges: list[Decimal] = []
    atr_values: list[Decimal | None] = []
    classifier = DeterministicRegimeClassifier(
        short_period=config.short_ema_period,
        long_period=config.long_ema_period,
        maximum_atr_relative=config.maximum_atr_relative,
    )
    indicator_candles = []
    raw: list[tuple[FuturesCandle, str, Decimal | None, Decimal | None, Decimal | None]] = []
    previous_close: Decimal | None = None
    for index, candle in enumerate(candles):
        indicator_candles.append(candle.as_indicator_candle())
        current_range = (
            candle.high - candle.low
            if previous_close is None
            else max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
        previous_close = candle.close
        true_ranges.append(current_range)
        atr_relative: Decimal | None = None
        if len(true_ranges) >= config.atr_period:
            atr_value = (
                sum(true_ranges[-config.atr_period :], Decimal("0"))
                / Decimal(config.atr_period)
            )
            atr_relative = atr_value / candle.close
        atr_values.append(atr_relative)
        classified = classifier.classify(indicator_candles).regime.value
        regime = (
            "HIGH_VOLATILITY"
            if atr_relative is not None
            and atr_relative > config.maximum_atr_relative
            else classified
        )
        ema_value = long_ema[index]
        ema_distance = (
            (candle.close - ema_value) / ema_value * Decimal("100")
            if ema_value is not None and ema_value
            else None
        )
        ema_slope = None
        if index >= 5 and ema_value is not None and long_ema[index - 5] is not None:
            prior_ema = long_ema[index - 5]
            assert prior_ema is not None
            ema_slope = (ema_value - prior_ema) / prior_ema * Decimal("100")
        raw.append((candle, regime, atr_relative, ema_distance, ema_slope))
    development_atr = tuple(
        value
        for candle, value in zip(candles, atr_values, strict=True)
        if value is not None and candle.open_time <= development_end
    )
    boundaries = volatility_quantile_boundaries(development_atr)
    contexts: list[TemporalCandleContext] = []
    for index, (candle, regime, atr_relative, ema_distance, ema_slope) in enumerate(raw):
        contexts.append(
            TemporalCandleContext(
                open_time=candle.open_time,
                regime=regime,
                volatility_bucket=volatility_bucket(atr_relative, boundaries),
                atr_relative=atr_relative,
                return_24h=_lookback_return(closes, index, 24),
                return_7d=_lookback_return(closes, index, 24 * 7),
                return_30d=_lookback_return(closes, index, 24 * 30),
                long_ema_distance_percent=ema_distance,
                long_ema_slope_percent=ema_slope,
                directional_persistence=_directional_persistence(closes, index, 24),
            )
        )
    return tuple(contexts), boundaries


def _lookback_return(
    closes: tuple[Decimal, ...],
    index: int,
    lookback: int,
) -> Decimal | None:
    if index < lookback or closes[index - lookback] == 0:
        return None
    return (
        (closes[index] - closes[index - lookback])
        / closes[index - lookback]
        * Decimal("100")
    )


def _directional_persistence(
    closes: tuple[Decimal, ...],
    index: int,
    lookback: int,
) -> Decimal | None:
    if index < lookback:
        return None
    changes = tuple(
        _sign(current - previous)
        for previous, current in zip(
            closes[index - lookback : index],
            closes[index - lookback + 1 : index + 1],
            strict=True,
        )
    )
    return Decimal(sum(changes)) / Decimal(len(changes))


def concentration_metrics(trades: tuple[FuturesTrade, ...]) -> Row:
    total = sum((item.net_pnl for item in trades), Decimal("0"))
    profitable = sorted(
        (item.net_pnl for item in trades if item.net_pnl > 0),
        reverse=True,
    )
    gross_profit = sum(profitable, Decimal("0"))

    def ratio(count: int) -> Decimal:
        return (
            sum(profitable[:count], Decimal("0")) / gross_profit * Decimal("100")
            if gross_profit
            else Decimal("0")
        )

    best = profitable[0] if profitable else Decimal("0")
    top_three = sum(profitable[:3], Decimal("0"))
    top_five = sum(profitable[:5], Decimal("0"))
    without_top_three = total - top_three
    warning = total > 0 and without_top_three <= 0
    return {
        "best_trade_concentration_percent": ratio(1),
        "top_3_concentration_percent": ratio(3),
        "top_5_concentration_percent": ratio(5),
        "result_without_best_trade": total - best,
        "result_without_top_3": without_top_three,
        "result_without_top_5": total - top_five,
        "warning": "RESULT_DEPENDS_ON_FEW_TRADES" if warning else "",
    }


def _drawdown(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        return Decimal("0")
    peak = values[0]
    maximum = Decimal("0")
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            maximum = max(maximum, (peak - value) / peak * Decimal("100"))
    return maximum


def _trade_drawdown(trades: tuple[FuturesTrade, ...], initial_balance: Decimal) -> Decimal:
    equity = initial_balance
    curve = [equity]
    for trade in sorted(trades, key=lambda item: item.exit_time):
        equity += trade.net_pnl
        curve.append(equity)
    return _drawdown(tuple(curve))


def _profit_factor(trades: tuple[FuturesTrade, ...]) -> Decimal | None:
    gains = sum((item.net_pnl for item in trades if item.net_pnl > 0), Decimal("0"))
    losses = abs(
        sum((item.net_pnl for item in trades if item.net_pnl < 0), Decimal("0"))
    )
    return gains / losses if losses else None


def _contexts_in_period(
    contexts: tuple[TemporalCandleContext, ...],
    start: datetime,
    end: datetime,
) -> tuple[TemporalCandleContext, ...]:
    return tuple(item for item in contexts if start <= item.open_time <= end)


def _trades_in_period(
    trades: tuple[FuturesTrade, ...],
    start: datetime,
    end: datetime,
) -> tuple[FuturesTrade, ...]:
    return tuple(item for item in trades if start <= item.exit_time <= end)


def aggregate_period(
    configuration: str,
    result: FuturesBacktestResult,
    contexts: tuple[TemporalCandleContext, ...],
    *,
    period: str,
    start: datetime,
    end: datetime,
) -> Row:
    effective_start = max(start, result.start_time)
    selected_trades = _trades_in_period(result.trades, effective_start, end)
    selected_contexts = _contexts_in_period(contexts, effective_start, end)
    close_traces = tuple(
        item
        for item in result.decision_traces
        if item.candle_index >= 0 and effective_start <= item.timestamp <= end
    )
    gross_pnl = sum((item.gross_pnl for item in selected_trades), Decimal("0"))
    net_pnl = sum((item.net_pnl for item in selected_trades), Decimal("0"))
    wins = sum(item.net_pnl > 0 for item in selected_trades)
    concentration = concentration_metrics(selected_trades)
    regime_counts: dict[str, int] = defaultdict(int)
    for context in selected_contexts:
        regime_counts[context.regime] += 1
    return {
        "configuration": configuration,
        "period": period,
        "start": effective_start,
        "end": end,
        "attribution_policy": "TRADE_EXIT_TIME",
        "net_return_percent": net_pnl / result.metrics.initial_wallet * Decimal("100"),
        "gross_return_percent": gross_pnl / result.metrics.initial_wallet * Decimal("100"),
        "net_pnl": net_pnl,
        "gross_pnl": gross_pnl,
        "trades": len(selected_trades),
        "long_trades": sum(item.side is PositionSide.LONG for item in selected_trades),
        "short_trades": sum(item.side is PositionSide.SHORT for item in selected_trades),
        "win_rate_percent": (
            Decimal(wins) / Decimal(len(selected_trades)) * Decimal("100")
            if selected_trades
            else None
        ),
        "profit_factor": _profit_factor(selected_trades),
        "expectancy": _mean(tuple(item.net_pnl for item in selected_trades)),
        "median_trade_pnl": (
            median(tuple(item.net_pnl for item in selected_trades))
            if selected_trades
            else None
        ),
        "maximum_drawdown_percent": _trade_drawdown(
            selected_trades,
            result.metrics.initial_wallet,
        ),
        "exposure_long_percent": (
            Decimal(
                sum(item.position_side is PositionSide.LONG for item in close_traces)
            )
            / Decimal(len(close_traces))
            * Decimal("100")
            if close_traces
            else Decimal("0")
        ),
        "exposure_short_percent": (
            Decimal(
                sum(item.position_side is PositionSide.SHORT for item in close_traces)
            )
            / Decimal(len(close_traces))
            * Decimal("100")
            if close_traces
            else Decimal("0")
        ),
        "trading_fees": sum(
            (item.trading_fees for item in selected_trades),
            Decimal("0"),
        ),
        "funding_paid": sum(
            (item.funding_paid for item in selected_trades),
            Decimal("0"),
        ),
        "funding_received": sum(
            (item.funding_received for item in selected_trades),
            Decimal("0"),
        ),
        "net_funding": sum(
            (item.net_funding for item in selected_trades),
            Decimal("0"),
        ),
        "liquidations": sum(
            item.exit_reason is FuturesExitReason.LIQUIDATION
            for item in selected_trades
        ),
        "positive": net_pnl > 0,
        "zero_trade": not selected_trades,
        "signal_count": sum(
            item.signal is not FuturesSignalDirection.HOLD for item in close_traces
        ),
        "stop_count": sum(
            item.exit_reason is FuturesExitReason.STOP_LOSS
            for item in selected_trades
        ),
        "target_count": sum(
            item.exit_reason is FuturesExitReason.TAKE_PROFIT
            for item in selected_trades
        ),
        "time_exit_count": sum(
            item.exit_reason is FuturesExitReason.TIME_EXIT
            for item in selected_trades
        ),
        "forced_end_count": sum(
            item.exit_reason is FuturesExitReason.FORCED_END
            for item in selected_trades
        ),
        "regime_distribution": dict(sorted(regime_counts.items())),
        **concentration,
    }


def calendar_year_periods(
    start_year: int = 2022,
    end_year: int = 2025,
) -> tuple[tuple[str, datetime, datetime], ...]:
    return tuple(
        (
            str(year),
            datetime(year, 1, 1, tzinfo=UTC),
            datetime(year + 1, 1, 1, tzinfo=UTC) - timedelta(microseconds=1),
        )
        for year in range(start_year, end_year + 1)
    )


def calendar_quarter_periods(
    start_year: int = 2022,
    end_year: int = 2025,
) -> tuple[tuple[str, datetime, datetime], ...]:
    rows: list[tuple[str, datetime, datetime]] = []
    for year in range(start_year, end_year + 1):
        for quarter, month in enumerate((1, 4, 7, 10), start=1):
            start = datetime(year, month, 1, tzinfo=UTC)
            next_start = (
                datetime(year + 1, 1, 1, tzinfo=UTC)
                if month == 10
                else datetime(year, month + 3, 1, tzinfo=UTC)
            )
            rows.append(
                (
                    f"{year}-Q{quarter}",
                    start,
                    next_start - timedelta(microseconds=1),
                )
            )
    return tuple(rows)


def rolling_periods(
    start: datetime,
    end: datetime,
    *,
    window_days: int,
    step_days: int,
) -> tuple[tuple[str, datetime, datetime], ...]:
    if window_days < 1 or step_days < 1:
        raise ValueError("rolling window and step must be positive")
    final_exclusive = end + _HOUR
    cursor = start
    rows: list[tuple[str, datetime, datetime]] = []
    index = 1
    while cursor + timedelta(days=window_days) <= final_exclusive:
        window_end = cursor + timedelta(days=window_days) - timedelta(microseconds=1)
        rows.append((f"{window_days}D-{index}", cursor, window_end))
        cursor += timedelta(days=step_days)
        index += 1
    return tuple(rows)


def walk_forward_design_periods(
    start: datetime,
    end: datetime,
) -> tuple[Row, ...]:
    designs = (
        ("ROLLING_365_90_90", "ROLLING", 365, 90, 90),
        ("EXPANDING_365_90_90", "EXPANDING", 365, 90, 90),
        ("ROLLING_730_90_90", "ROLLING", 730, 90, 90),
        ("ROLLING_365_180_90", "ROLLING", 365, 180, 90),
    )
    final_exclusive = end + _HOUR
    rows: list[Row] = []
    for design, mode, train_days, validation_days, step_days in designs:
        validation_start = start + timedelta(days=train_days)
        fold = 1
        while (
            validation_start + timedelta(days=validation_days)
            <= final_exclusive
        ):
            rows.append(
                {
                    "design": design,
                    "mode": mode,
                    "fold": fold,
                    "train_start": (
                        start
                        if mode == "EXPANDING"
                        else validation_start - timedelta(days=train_days)
                    ),
                    "train_end": validation_start - timedelta(microseconds=1),
                    "validation_start": validation_start,
                    "validation_end": (
                        validation_start
                        + timedelta(days=validation_days)
                        - timedelta(microseconds=1)
                    ),
                    "train_days": train_days,
                    "validation_days": validation_days,
                    "step_days": step_days,
                }
            )
            validation_start += timedelta(days=step_days)
            fold += 1
    return tuple(rows)


def predefined_temporal_boundaries() -> tuple[Row, ...]:
    return (
        {
            "boundary": "A",
            "development_start": datetime(2022, 1, 1, tzinfo=UTC),
            "development_end": datetime(2023, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
            "validation_start": datetime(2024, 1, 1, tzinfo=UTC),
            "validation_end": datetime(2024, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
            "reference_start": datetime(2025, 1, 1, tzinfo=UTC),
            "reference_end": datetime(2025, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
        },
        {
            "boundary": "B",
            "development_start": datetime(2022, 1, 1, tzinfo=UTC),
            "development_end": datetime(2024, 6, 30, 23, 59, 59, 999000, tzinfo=UTC),
            "validation_start": datetime(2024, 7, 1, tzinfo=UTC),
            "validation_end": datetime(2025, 6, 30, 23, 59, 59, 999000, tzinfo=UTC),
            "reference_start": None,
            "reference_end": None,
        },
        {
            "boundary": "C",
            "development_start": datetime(2022, 1, 1, tzinfo=UTC),
            "development_end": datetime(2024, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
            "validation_start": datetime(2025, 1, 1, tzinfo=UTC),
            "validation_end": datetime(2025, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
            "reference_start": None,
            "reference_end": None,
        },
        {
            "boundary": "D",
            "development_start": datetime(2022, 1, 1, tzinfo=UTC),
            "development_end": datetime(2023, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
            "validation_start": datetime(2024, 1, 1, tzinfo=UTC),
            "validation_end": datetime(2025, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
            "reference_start": None,
            "reference_end": None,
        },
    )


def bootstrap_trade_pnls(
    configuration: str,
    trades: tuple[FuturesTrade, ...],
    *,
    iterations: int,
    seed: int,
    block_by_month: bool = False,
) -> BootstrapSummary:
    if not 1 <= iterations <= 10_000:
        raise ValueError("bootstrap iterations must be between 1 and 10000")
    if len(trades) < 10:
        return BootstrapSummary(
            configuration=configuration,
            trade_count=len(trades),
            iterations=iterations,
            seed=seed,
            block_by_month=block_by_month,
            status=BootstrapStatus.INSUFFICIENT_SAMPLE,
            sample_fingerprint="",
            intervals={},
        )
    rng = random.Random(seed)
    values = tuple(item.net_pnl for item in trades)
    month_blocks: tuple[tuple[Decimal, ...], ...] = ()
    if block_by_month:
        grouped: dict[tuple[int, int], list[Decimal]] = defaultdict(list)
        for trade in trades:
            grouped[(trade.exit_time.year, trade.exit_time.month)].append(
                trade.net_pnl
            )
        month_blocks = tuple(tuple(grouped[key]) for key in sorted(grouped))
    samples: dict[str, list[Decimal]] = {
        "mean_trade_pnl": [],
        "median_trade_pnl": [],
        "total_trade_pnl": [],
        "win_rate_percent": [],
        "expectancy": [],
    }
    fingerprint = hashlib.sha256()
    for iteration in range(iterations):
        if block_by_month:
            sampled_values: list[Decimal] = []
            while len(sampled_values) < len(values):
                block_index = rng.randrange(len(month_blocks))
                sampled_values.extend(month_blocks[block_index])
                if iteration == 0:
                    fingerprint.update(f"b{block_index},".encode())
            sample = tuple(sampled_values[: len(values)])
        else:
            indexes = tuple(rng.randrange(len(values)) for _ in values)
            if iteration == 0:
                fingerprint.update(",".join(str(item) for item in indexes).encode())
            sample = tuple(values[index] for index in indexes)
        mean_value = sum(sample, Decimal("0")) / Decimal(len(sample))
        samples["mean_trade_pnl"].append(mean_value)
        samples["median_trade_pnl"].append(median(sample))
        samples["total_trade_pnl"].append(sum(sample, Decimal("0")))
        samples["win_rate_percent"].append(
            Decimal(sum(item > 0 for item in sample))
            / Decimal(len(sample))
            * Decimal("100")
        )
        samples["expectancy"].append(mean_value)
    intervals = {
        name: {
            "lower_95": _percentile(tuple(results), Decimal("0.025")),
            "median": _percentile(tuple(results), Decimal("0.5")),
            "upper_95": _percentile(tuple(results), Decimal("0.975")),
        }
        for name, results in samples.items()
    }
    total_interval = intervals["total_trade_pnl"]
    if total_interval["lower_95"] > 0:
        status = BootstrapStatus.POSITIVE_UNCERTAIN
    elif total_interval["upper_95"] < 0:
        status = BootstrapStatus.NEGATIVE_UNCERTAIN
    else:
        status = BootstrapStatus.INCLUDES_ZERO
    return BootstrapSummary(
        configuration=configuration,
        trade_count=len(trades),
        iterations=iterations,
        seed=seed,
        block_by_month=block_by_month,
        status=status,
        sample_fingerprint=fingerprint.hexdigest(),
        intervals=intervals,
    )


def _context_index(
    contexts: tuple[TemporalCandleContext, ...],
) -> dict[datetime, TemporalCandleContext]:
    return {item.open_time: item for item in contexts}


def _context_for_trade(
    trade: FuturesTrade,
    contexts_by_time: dict[datetime, TemporalCandleContext],
    *,
    at_exit: bool = False,
) -> TemporalCandleContext | None:
    timestamp = trade.exit_time if at_exit else trade.entry_time
    open_time = timestamp.replace(minute=0, second=0, microsecond=0)
    return contexts_by_time.get(open_time)


def _excursions(
    trade: FuturesTrade,
    candles: tuple[FuturesCandle, ...],
) -> tuple[Decimal, Decimal]:
    selected = tuple(
        item
        for item in candles
        if trade.entry_time <= item.close_time <= trade.exit_time
        or item.open_time == trade.entry_time
    )
    if not selected:
        return Decimal("0"), Decimal("0")
    if trade.side is PositionSide.LONG:
        mfe = (max(item.high for item in selected) - trade.entry_price) * trade.quantity
        mae = (min(item.low for item in selected) - trade.entry_price) * trade.quantity
    else:
        mfe = (trade.entry_price - min(item.low for item in selected)) * trade.quantity
        mae = (trade.entry_price - max(item.high for item in selected)) * trade.quantity
    return max(Decimal("0"), mfe), min(Decimal("0"), mae)


def _regime_rows(
    configuration: str,
    result: FuturesBacktestResult,
    candles: tuple[FuturesCandle, ...],
    contexts: tuple[TemporalCandleContext, ...],
) -> tuple[Row, ...]:
    by_time = _context_index(contexts)
    regimes = tuple(item.value for item in MarketRegime) + ("HIGH_VOLATILITY",)
    close_traces = tuple(item for item in result.decision_traces if item.candle_index >= 0)
    rows: list[Row] = []
    periods = (
        (
            "ALL",
            result.start_time,
            result.end_time + _HOUR - timedelta(microseconds=1),
        ),
    ) + tuple(
        (name, max(start, result.start_time), end)
        for name, start, end in calendar_year_periods()
    )
    for period, start, end in periods:
        for regime in regimes:
            regime_contexts = tuple(
                item
                for item in contexts
                if start <= item.open_time <= end and item.regime == regime
            )
            trades = tuple(
                item
                for item in result.trades
                if start <= item.exit_time <= end
                and (context := _context_for_trade(item, by_time)) is not None
                and context.regime == regime
            )
            traces = tuple(
                item
                for item in close_traces
                if start <= item.timestamp <= end
                and (
                    context := by_time.get(
                        item.timestamp.replace(minute=0, second=0, microsecond=0)
                    )
                )
                is not None
                and context.regime == regime
            )
            rows.append(
                {
                    "configuration": configuration,
                    "period": period,
                    "regime": regime,
                    "candle_count": len(regime_contexts),
                    "signal_count": sum(
                        item.signal is not FuturesSignalDirection.HOLD
                        for item in traces
                    ),
                    "entry_count": len(trades),
                    "long_entries": sum(
                        item.side is PositionSide.LONG for item in trades
                    ),
                    "short_entries": sum(
                        item.side is PositionSide.SHORT for item in trades
                    ),
                    "net_pnl": sum(
                        (item.net_pnl for item in trades),
                        Decimal("0"),
                    ),
                    "mean_trade_pnl": _mean(
                        tuple(item.net_pnl for item in trades)
                    ),
                    "median_trade_pnl": (
                        median(tuple(item.net_pnl for item in trades))
                        if trades
                        else None
                    ),
                    "win_rate_percent": (
                        Decimal(sum(item.net_pnl > 0 for item in trades))
                        / Decimal(len(trades))
                        * Decimal("100")
                        if trades
                        else None
                    ),
                    "expectancy": _mean(tuple(item.net_pnl for item in trades)),
                    "maximum_drawdown_percent": _trade_drawdown(
                        trades,
                        result.metrics.initial_wallet,
                    ),
                    "exposure_percent": (
                        Decimal(
                            sum(item.position_side is not None for item in traces)
                        )
                        / Decimal(len(traces))
                        * Decimal("100")
                        if traces
                        else Decimal("0")
                    ),
                    "fees": sum(
                        (item.trading_fees for item in trades),
                        Decimal("0"),
                    ),
                    "funding": sum(
                        (item.net_funding for item in trades),
                        Decimal("0"),
                    ),
                    **concentration_metrics(trades),
                }
            )
    return tuple(rows)


def _transition_rows(
    configuration: str,
    result: FuturesBacktestResult,
    candles: tuple[FuturesCandle, ...],
    contexts: tuple[TemporalCandleContext, ...],
) -> tuple[Row, ...]:
    by_time = _context_index(contexts)
    grouped: dict[tuple[str, str, str], list[tuple[FuturesTrade, Decimal, Decimal]]] = (
        defaultdict(list)
    )
    for trade in result.trades:
        entry = _context_for_trade(trade, by_time)
        exit_context = _context_for_trade(trade, by_time, at_exit=True)
        if entry is None or exit_context is None:
            continue
        path_values = tuple(
            item.regime
            for item in contexts
            if trade.entry_time <= item.open_time <= trade.exit_time
        )
        compressed: list[str] = []
        for value in path_values:
            if not compressed or compressed[-1] != value:
                compressed.append(value)
        path = "->".join(compressed) if compressed else f"{entry.regime}->{exit_context.regime}"
        mfe, mae = _excursions(trade, candles)
        grouped[(entry.regime, exit_context.regime, path)].append((trade, mfe, mae))
    rows: list[Row] = []
    for (entry_regime, exit_regime, path), items in sorted(grouped.items()):
        trades = tuple(item[0] for item in items)
        exit_reasons: dict[str, int] = defaultdict(int)
        for trade in trades:
            exit_reasons[trade.exit_reason.value] += 1
        rows.append(
            {
                "configuration": configuration,
                "entry_regime": entry_regime,
                "exit_regime": exit_regime,
                "transition": f"{entry_regime}->{exit_regime}",
                "transition_path": path,
                "trades": len(trades),
                "net_pnl": sum((item.net_pnl for item in trades), Decimal("0")),
                "win_rate_percent": (
                    Decimal(sum(item.net_pnl > 0 for item in trades))
                    / Decimal(len(trades))
                    * Decimal("100")
                ),
                "mean_mfe": _mean(tuple(item[1] for item in items)),
                "mean_mae": _mean(tuple(item[2] for item in items)),
                "mean_holding_candles": (
                    Decimal(sum(item.holding_candles for item in trades))
                    / Decimal(len(trades))
                ),
                "exit_reasons": dict(sorted(exit_reasons.items())),
            }
        )
    return tuple(rows)


def _volatility_rows(
    configuration: str,
    result: FuturesBacktestResult,
    contexts: tuple[TemporalCandleContext, ...],
) -> tuple[Row, ...]:
    by_time = _context_index(contexts)
    rows: list[Row] = []
    periods = (
        (
            "ALL",
            result.start_time,
            result.end_time + _HOUR - timedelta(microseconds=1),
        ),
    ) + tuple(
        (name, max(start, result.start_time), end)
        for name, start, end in calendar_year_periods()
    )
    for period, start, end in periods:
        for bucket in ("LOW", "MEDIUM", "HIGH", "EXTREME", "UNKNOWN"):
            trades = tuple(
                item
                for item in result.trades
                if start <= item.exit_time <= end
                and (context := _context_for_trade(item, by_time)) is not None
                and context.volatility_bucket == bucket
            )
            bucket_contexts = tuple(
                item
                for item in contexts
                if start <= item.open_time <= end
                and item.volatility_bucket == bucket
            )
            net_pnl = sum((item.net_pnl for item in trades), Decimal("0"))
            rows.append(
                {
                    "configuration": configuration,
                    "period": period,
                    "volatility_bucket": bucket,
                    "candles": len(bucket_contexts),
                    "trades": len(trades),
                    "long_trades": sum(
                        item.side is PositionSide.LONG for item in trades
                    ),
                    "short_trades": sum(
                        item.side is PositionSide.SHORT for item in trades
                    ),
                    "net_return_percent": (
                        net_pnl / result.metrics.initial_wallet * Decimal("100")
                    ),
                    "maximum_drawdown_percent": _trade_drawdown(
                        trades,
                        result.metrics.initial_wallet,
                    ),
                    "win_rate_percent": (
                        Decimal(sum(item.net_pnl > 0 for item in trades))
                        / Decimal(len(trades))
                        * Decimal("100")
                        if trades
                        else None
                    ),
                    "expectancy": _mean(tuple(item.net_pnl for item in trades)),
                    "costs": sum(
                        (item.trading_fees for item in trades),
                        Decimal("0"),
                    ),
                    "funding": sum(
                        (item.net_funding for item in trades),
                        Decimal("0"),
                    ),
                    **concentration_metrics(trades),
                }
            )
    return tuple(rows)


def _context_bucket(metric: str, value: Decimal | None) -> str:
    if value is None:
        return "UNKNOWN"
    if metric in {"return_24h", "return_7d", "return_30d", "long_ema_slope_percent"}:
        return "NEGATIVE" if value < 0 else "POSITIVE" if value > 0 else "FLAT"
    if metric == "long_ema_distance_percent":
        if value < Decimal("-2"):
            return "FAR_BELOW"
        if value < 0:
            return "BELOW"
        if value > Decimal("2"):
            return "FAR_ABOVE"
        return "ABOVE"
    if metric == "directional_persistence":
        if value < Decimal("-0.25"):
            return "DOWN"
        if value > Decimal("0.25"):
            return "UP"
        return "MIXED"
    return "UNKNOWN"


def _market_context_rows(
    configuration: str,
    result: FuturesBacktestResult,
    contexts: tuple[TemporalCandleContext, ...],
) -> tuple[Row, ...]:
    by_time = _context_index(contexts)
    metrics = (
        "return_24h",
        "return_7d",
        "return_30d",
        "long_ema_distance_percent",
        "long_ema_slope_percent",
        "directional_persistence",
    )
    grouped: dict[tuple[str, str], list[FuturesTrade]] = defaultdict(list)
    for trade in result.trades:
        context = _context_for_trade(trade, by_time)
        if context is None:
            continue
        for metric in metrics:
            grouped[(metric, _context_bucket(metric, getattr(context, metric)))].append(
                trade
            )
    rows: list[Row] = []
    for (metric, bucket), trades_list in sorted(grouped.items()):
        trades = tuple(trades_list)
        rows.append(
            {
                "configuration": configuration,
                "metric": metric,
                "bucket": bucket,
                "post_event_diagnostic_only": True,
                "trades": len(trades),
                "long_trades": sum(item.side is PositionSide.LONG for item in trades),
                "short_trades": sum(item.side is PositionSide.SHORT for item in trades),
                "net_pnl": sum((item.net_pnl for item in trades), Decimal("0")),
                "mean_trade_pnl": _mean(tuple(item.net_pnl for item in trades)),
                "win_rate_percent": (
                    Decimal(sum(item.net_pnl > 0 for item in trades))
                    / Decimal(len(trades))
                    * Decimal("100")
                ),
            }
        )
    return tuple(rows)


def _side_contribution_rows(
    configuration: str,
    result: FuturesBacktestResult,
    contexts: tuple[TemporalCandleContext, ...],
) -> tuple[Row, ...]:
    by_time = _context_index(contexts)
    grouped: dict[tuple[str, str, PositionSide], list[FuturesTrade]] = defaultdict(list)
    for trade in result.trades:
        year = str(trade.exit_time.year)
        quarter = f"{trade.exit_time.year}-Q{(trade.exit_time.month - 1) // 3 + 1}"
        context = _context_for_trade(trade, by_time)
        grouped[("YEAR", year, trade.side)].append(trade)
        grouped[("QUARTER", quarter, trade.side)].append(trade)
        if context is not None:
            grouped[("REGIME", context.regime, trade.side)].append(trade)
            grouped[
                ("VOLATILITY", context.volatility_bucket, trade.side)
            ].append(trade)
    rows: list[Row] = []
    for (dimension, period, side), items in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2].value),
    ):
        trades = tuple(items)
        rows.append(
            {
                "configuration": configuration,
                "dimension": dimension,
                "period": period,
                "side": side.value,
                "trades": len(trades),
                "gross_pnl": sum((item.gross_pnl for item in trades), Decimal("0")),
                "fees": sum((item.trading_fees for item in trades), Decimal("0")),
                "funding": sum((item.net_funding for item in trades), Decimal("0")),
                "net_pnl": sum((item.net_pnl for item in trades), Decimal("0")),
            }
        )
    return tuple(rows)


def _funding_impact_rows(
    configuration: str,
    enabled: FuturesBacktestResult,
    disabled: FuturesBacktestResult,
    *,
    threshold_percent: Decimal,
) -> tuple[Row, ...]:
    periods = tuple(
        ("YEAR", name, start, end)
        for name, start, end in calendar_year_periods()
    ) + tuple(
        ("QUARTER", name, start, end)
        for name, start, end in calendar_quarter_periods()
    )
    rows: list[Row] = []
    for period_type, name, start, end in periods:
        enabled_trades = _trades_in_period(enabled.trades, start, end)
        disabled_trades = _trades_in_period(disabled.trades, start, end)
        with_funding = sum((item.net_pnl for item in enabled_trades), Decimal("0"))
        without_funding = sum(
            (item.net_pnl for item in disabled_trades),
            Decimal("0"),
        )
        difference = with_funding - without_funding
        explained = (
            abs(difference) / abs(with_funding) * Decimal("100")
            if with_funding
            else None
        )
        rows.append(
            {
                "configuration": configuration,
                "period_type": period_type,
                "period": name,
                "with_funding_net_pnl": with_funding,
                "without_funding_net_pnl": without_funding,
                "difference": difference,
                "pnl_explained_percent": explained,
                "long_effect": sum(
                    (
                        item.net_funding
                        for item in enabled_trades
                        if item.side is PositionSide.LONG
                    ),
                    Decimal("0"),
                ),
                "short_effect": sum(
                    (
                        item.net_funding
                        for item in enabled_trades
                        if item.side is PositionSide.SHORT
                    ),
                    Decimal("0"),
                ),
                "warning": (
                    "FUNDING_DOMINATED_RESULT"
                    if explained is not None and explained >= threshold_percent
                    else ""
                ),
                "diagnostic_only": True,
            }
        )
    return tuple(rows)


def _cost_impact_rows(
    configuration: str,
    scenario_results: dict[str, FuturesBacktestResult],
    contexts: tuple[TemporalCandleContext, ...],
    rolling: tuple[tuple[str, datetime, datetime], ...],
) -> tuple[Row, ...]:
    periods = tuple(
        ("YEAR", name, start, end)
        for name, start, end in calendar_year_periods()
    ) + tuple(("ROLLING", name, start, end) for name, start, end in rolling)
    rows: list[Row] = []
    for period_type, name, start, end in periods:
        period_rows: list[Row] = []
        for scenario in ("LOW_COST", "BASE_COST", "HIGH_COST", "STRESS_COST"):
            result = scenario_results[scenario]
            row = aggregate_period(
                configuration,
                result,
                contexts,
                period=name,
                start=start,
                end=end,
            )
            period_rows.append(
                {
                    "configuration": configuration,
                    "period_type": period_type,
                    "period": name,
                    "scenario": scenario,
                    "net_return_percent": row["net_return_percent"],
                    "net_pnl": row["net_pnl"],
                    "gross_pnl": row["gross_pnl"],
                    "trades": row["trades"],
                    "costs": row["trading_fees"],
                    "funding": row["net_funding"],
                    "warning": "",
                }
            )
        by_scenario = {str(item["scenario"]): item for item in period_rows}
        low = Decimal(str(by_scenario["LOW_COST"]["net_pnl"]))
        base = Decimal(str(by_scenario["BASE_COST"]["net_pnl"]))
        stress = Decimal(str(by_scenario["STRESS_COST"]["net_pnl"]))
        gross = Decimal(str(by_scenario["BASE_COST"]["gross_pnl"]))
        base_costs = Decimal(str(by_scenario["BASE_COST"]["costs"]))
        warnings: list[str] = []
        if low > 0 and base <= 0:
            warnings.append("LOW_COST_ONLY_EDGE")
        if base > 0 and stress <= 0:
            warnings.append("STRESS_COLLAPSE")
        if base_costs >= abs(gross) and base_costs > 0:
            warnings.append("COST_DOMINATED_PERIOD")
        warning = ";".join(warnings)
        for row in period_rows:
            row["warning"] = warning
        rows.extend(period_rows)
    return tuple(rows)


def _temporal_concentration_rows(
    configuration: str,
    result: FuturesBacktestResult,
) -> tuple[Row, ...]:
    periods = tuple(
        ("YEAR", name, start, end)
        for name, start, end in calendar_year_periods()
    ) + tuple(
        ("QUARTER", name, start, end)
        for name, start, end in calendar_quarter_periods()
    )
    rows: list[Row] = []
    for period_type, name, start, end in periods:
        period_trades = _trades_in_period(result.trades, start, end)
        for side_name, trades in (
            ("ALL", period_trades),
            (
                "LONG",
                tuple(
                    item for item in period_trades if item.side is PositionSide.LONG
                ),
            ),
            (
                "SHORT",
                tuple(
                    item for item in period_trades if item.side is PositionSide.SHORT
                ),
            ),
        ):
            rows.append(
                {
                    "configuration": configuration,
                    "period_type": period_type,
                    "period": name,
                    "side": side_name,
                    "trades": len(trades),
                    "net_pnl": sum((item.net_pnl for item in trades), Decimal("0")),
                    **concentration_metrics(trades),
                }
            )
    return tuple(rows)


def _boundary_rows(
    configuration: str,
    result: FuturesBacktestResult,
    contexts: tuple[TemporalCandleContext, ...],
) -> tuple[Row, ...]:
    rows: list[Row] = []
    classifications: dict[str, str] = {}
    return_directions: dict[str, tuple[int, int]] = {}
    for boundary in predefined_temporal_boundaries():
        name = str(boundary["boundary"])
        development = aggregate_period(
            configuration,
            result,
            contexts,
            period=f"{name}-DEVELOPMENT",
            start=_datetime_field(boundary, "development_start"),
            end=_datetime_field(boundary, "development_end"),
        )
        validation = aggregate_period(
            configuration,
            result,
            contexts,
            period=f"{name}-VALIDATION",
            start=_datetime_field(boundary, "validation_start"),
            end=_datetime_field(boundary, "validation_end"),
        )
        development_return = Decimal(str(development["net_return_percent"]))
        validation_return = Decimal(str(validation["net_return_percent"]))
        label = (
            "BOTH_POSITIVE"
            if development_return >= 0 and validation_return >= 0
            else "BOTH_NEGATIVE"
            if development_return < 0 and validation_return < 0
            else "SIGN_CHANGE"
        )
        classifications[name] = label
        return_directions[name] = (
            _sign(development_return),
            _sign(validation_return),
        )
        for segment, item in (
            ("DEVELOPMENT", development),
            ("VALIDATION", validation),
        ):
            rows.append(
                {
                    **item,
                    "boundary": name,
                    "segment": segment,
                    "boundary_classification": label,
                    "selection_eligible": True,
                    "warning": "",
                }
            )
        reference_start = boundary["reference_start"]
        reference_end = boundary["reference_end"]
        if isinstance(reference_start, datetime) and isinstance(reference_end, datetime):
            rows.append(
                {
                    **aggregate_period(
                        configuration,
                        result,
                        contexts,
                        period=f"{name}-REFERENCE",
                        start=reference_start,
                        end=reference_end,
                    ),
                    "boundary": name,
                    "segment": "REFERENCE_ONLY",
                    "boundary_classification": label,
                    "selection_eligible": False,
                    "warning": "",
                }
            )
    if (
        len(set(classifications.values())) > 1
        or len(set(return_directions.values())) > 1
    ):
        for row in rows:
            row["warning"] = "BOUNDARY_SENSITIVE"
    return tuple(rows)


def leave_one_year_out(
    configuration: str,
    result: FuturesBacktestResult,
) -> tuple[Row, ...]:
    total = sum((item.net_pnl for item in result.trades), Decimal("0"))
    rows: list[Row] = []
    for year in range(2022, 2026):
        held_out = tuple(
            item for item in result.trades if item.exit_time.year == year
        )
        remaining = tuple(
            item for item in result.trades if item.exit_time.year != year
        )
        held_out_result = sum((item.net_pnl for item in held_out), Decimal("0"))
        remaining_result = sum((item.net_pnl for item in remaining), Decimal("0"))
        direction_change = _sign(total) != _sign(remaining_result)
        rows.append(
            {
                "configuration": configuration,
                "held_out_year": year,
                "result_without_year": remaining_result,
                "result_without_year_percent": (
                    remaining_result
                    / result.metrics.initial_wallet
                    * Decimal("100")
                ),
                "held_out_year_result": held_out_result,
                "held_out_year_result_percent": (
                    held_out_result
                    / result.metrics.initial_wallet
                    * Decimal("100")
                ),
                "remaining_trade_count": len(remaining),
                "held_out_trade_count": len(held_out),
                "consistency": _sign(remaining_result) == _sign(held_out_result),
                "direction_change": direction_change,
                "warning": "SINGLE_YEAR_DEPENDENCE" if direction_change else "",
            }
        )
    return tuple(rows)


def _walk_forward_rows(
    configuration: str,
    result: FuturesBacktestResult,
    contexts: tuple[TemporalCandleContext, ...],
) -> tuple[Row, ...]:
    fold_rows: list[Row] = []
    for definition in walk_forward_design_periods(_EXPECTED_START, _EXPECTED_END):
        row = aggregate_period(
            configuration,
            result,
            contexts,
            period=f"{definition['design']}-{definition['fold']}",
            start=_datetime_field(definition, "validation_start"),
            end=_datetime_field(definition, "validation_end"),
        )
        fold_rows.append(
            {
                **definition,
                **row,
                "fixed_parameters": True,
                "selection_performed": False,
            }
        )
    consolidated: list[Row] = []
    for design in sorted({str(item["design"]) for item in fold_rows}):
        selected = tuple(item for item in fold_rows if item["design"] == design)
        returns = tuple(
            Decimal(str(item["net_return_percent"])) for item in selected
        )
        consolidated.append(
            {
                "configuration": configuration,
                "design": design,
                "fold": "CONSOLIDATED",
                "fold_count": len(selected),
                "median_return_percent": median(returns),
                "mean_return_percent": _mean(returns),
                "positive_fold_percent": (
                    Decimal(sum(item > 0 for item in returns))
                    / Decimal(len(returns))
                    * Decimal("100")
                ),
                "zero_trade_fold_percent": (
                    Decimal(sum(bool(item["zero_trade"]) for item in selected))
                    / Decimal(len(selected))
                    * Decimal("100")
                ),
                "trades": sum(int(str(item["trades"])) for item in selected),
                "fixed_parameters": True,
                "selection_performed": False,
            }
        )
    return tuple(fold_rows + consolidated)


def _stability_dimension(
    positive_percent: Decimal | None,
    *,
    stable_minimum: Decimal = Decimal("60"),
) -> StabilityStatus:
    if positive_percent is None:
        return StabilityStatus.INCONCLUSIVE
    if positive_percent >= stable_minimum:
        return StabilityStatus.STABLE
    if positive_percent >= Decimal("40"):
        return StabilityStatus.MIXED
    return StabilityStatus.UNSTABLE


def _scorecard(
    configuration: str,
    yearly: tuple[Row, ...],
    quarterly: tuple[Row, ...],
    rolling: tuple[Row, ...],
    walk_forward: tuple[Row, ...],
    boundaries: tuple[Row, ...],
    leave_one_out: tuple[Row, ...],
    regimes: tuple[Row, ...],
    volatility: tuple[Row, ...],
    funding: tuple[Row, ...],
    costs: tuple[Row, ...],
    concentration: tuple[Row, ...],
    bootstrap: BootstrapSummary,
) -> Row:
    def positive_percent(rows: tuple[Row, ...], key: str = "net_return_percent") -> Decimal | None:
        values = tuple(Decimal(str(item[key])) for item in rows if item.get(key) is not None)
        return (
            Decimal(sum(item > 0 for item in values))
            / Decimal(len(values))
            * Decimal("100")
            if values
            else None
        )

    consolidated_wf = tuple(
        item for item in walk_forward if item["fold"] == "CONSOLIDATED"
    )
    boundary_warning = any(item.get("warning") == "BOUNDARY_SENSITIVE" for item in boundaries)
    single_year = any(
        item.get("warning") == "SINGLE_YEAR_DEPENDENCE" for item in leave_one_out
    )
    funding_warning = any(
        item.get("warning") == "FUNDING_DOMINATED_RESULT" for item in funding
    )
    cost_warning = any(bool(item.get("warning")) for item in costs)
    concentration_warning = any(
        item.get("warning") == "RESULT_DEPENDS_ON_FEW_TRADES"
        for item in concentration
    )
    regime_positive = positive_percent(
        tuple(
            item
            for item in regimes
            if item["period"] == "ALL" and int(str(item["entry_count"])) > 0
        ),
        "net_pnl",
    )
    volatility_positive = positive_percent(
        tuple(
            item
            for item in volatility
            if item["period"] == "ALL" and int(str(item["trades"])) > 0
        ),
        "net_return_percent",
    )
    sample_size = bootstrap.trade_count
    dimensions = {
        "yearly_consistency": _stability_dimension(positive_percent(yearly)),
        "quarterly_consistency": _stability_dimension(positive_percent(quarterly)),
        "rolling_window_consistency": _stability_dimension(
            positive_percent(rolling)
        ),
        "boundary_stability": (
            StabilityStatus.UNSTABLE if boundary_warning else StabilityStatus.STABLE
        ),
        "walk_forward_design_stability": _stability_dimension(
            positive_percent(consolidated_wf, "median_return_percent")
        ),
        "side_stability": (
            StabilityStatus.UNSTABLE if single_year else StabilityStatus.MIXED
        ),
        "regime_stability": _stability_dimension(regime_positive),
        "volatility_stability": _stability_dimension(volatility_positive),
        "cost_stability": (
            StabilityStatus.UNSTABLE if cost_warning else StabilityStatus.STABLE
        ),
        "funding_stability": (
            StabilityStatus.UNSTABLE if funding_warning else StabilityStatus.STABLE
        ),
        "concentration": (
            StabilityStatus.UNSTABLE
            if concentration_warning
            else StabilityStatus.STABLE
        ),
        "uncertainty": (
            StabilityStatus.STABLE
            if bootstrap.status is BootstrapStatus.POSITIVE_UNCERTAIN
            else StabilityStatus.UNSTABLE
            if bootstrap.status is BootstrapStatus.NEGATIVE_UNCERTAIN
            else StabilityStatus.MIXED
            if bootstrap.status is BootstrapStatus.INCLUDES_ZERO
            else StabilityStatus.INCONCLUSIVE
        ),
        "sample_size": (
            StabilityStatus.STABLE
            if sample_size >= 100
            else StabilityStatus.MIXED
            if sample_size >= 30
            else StabilityStatus.INCONCLUSIVE
        ),
    }
    counts = {
        status.value: sum(value is status for value in dimensions.values())
        for status in StabilityStatus
    }
    overall = (
        StabilityStatus.INCONCLUSIVE
        if dimensions["sample_size"] is StabilityStatus.INCONCLUSIVE
        else StabilityStatus.UNSTABLE
        if counts[StabilityStatus.UNSTABLE.value] >= 4
        else StabilityStatus.STABLE
        if counts[StabilityStatus.STABLE.value] >= 9
        else StabilityStatus.MIXED
    )
    return {
        "configuration": configuration,
        "dimensions": dimensions,
        "dimension_counts": counts,
        "overall": overall,
        "single_score_used": False,
    }


def _configuration_classification(
    configuration: str,
    yearly: tuple[Row, ...],
    rolling: tuple[Row, ...],
    boundaries: tuple[Row, ...],
    regimes: tuple[Row, ...],
    concentration: tuple[Row, ...],
    bootstrap: BootstrapSummary,
    scorecard: Row,
) -> Row:
    total_trades = sum(int(str(item["trades"])) for item in yearly)
    yearly_returns = {
        str(item["period"]): Decimal(str(item["net_return_percent"]))
        for item in yearly
    }
    pre_2025 = tuple(value for year, value in yearly_returns.items() if year != "2025")
    positive_years = sum(value > 0 for value in yearly_returns.values())
    rolling_positive = (
        Decimal(
            sum(Decimal(str(item["net_return_percent"])) > 0 for item in rolling)
        )
        / Decimal(len(rolling))
        * Decimal("100")
        if rolling
        else Decimal("0")
    )
    boundary_sensitive = any(
        item.get("warning") == "BOUNDARY_SENSITIVE" for item in boundaries
    )
    concentrated = any(
        item.get("warning") == "RESULT_DEPENDS_ON_FEW_TRADES"
        for item in concentration
    )
    positive_regime_years: dict[str, set[str]] = defaultdict(set)
    for item in regimes:
        if (
            item["period"] != "ALL"
            and int(str(item["entry_count"])) > 0
            and Decimal(str(item["net_pnl"])) > 0
        ):
            positive_regime_years[str(item["regime"])].add(str(item["period"]))
    repeated_regime = any(
        len(years) >= 2 for years in positive_regime_years.values()
    )
    checks = {
        "minimum_sample": total_trades >= 30,
        "multiple_positive_years": positive_years >= 3,
        "rolling_majority_positive": rolling_positive >= 60,
        "boundary_stable": not boundary_sensitive,
        "not_concentrated": not concentrated,
        "bootstrap_not_negative": (
            bootstrap.status is not BootstrapStatus.NEGATIVE_UNCERTAIN
        ),
        "pre_2025_support": sum(pre_2025, Decimal("0")) > 0,
        "repeated_regime_pattern": repeated_regime,
    }
    if total_trades < 30:
        classification = TemporalClassification.INCONCLUSIVE
        rationale = "sample below 30 closed trades"
    elif all(
        checks[key]
        for key in (
            "multiple_positive_years",
            "rolling_majority_positive",
            "boundary_stable",
            "not_concentrated",
            "bootstrap_not_negative",
            "pre_2025_support",
        )
    ):
        classification = TemporalClassification.ROBUSTNESS_SIGNAL
        rationale = "positive evidence repeats across years, windows and boundaries"
    elif (
        repeated_regime
        and checks["pre_2025_support"]
        and scorecard["overall"] is not StabilityStatus.UNSTABLE
    ):
        classification = TemporalClassification.REGIME_SPECIFIC_SIGNAL
        rationale = "positive results repeat in an observed point-in-time regime"
    elif yearly_returns.get("2025", Decimal("0")) > 0 and sum(
        pre_2025,
        Decimal("0"),
    ) <= 0:
        classification = TemporalClassification.NON_STATIONARY
        rationale = "2025 is positive without aggregate support in 2022-2024"
    elif positive_years <= 1 and bootstrap.status in {
        BootstrapStatus.NEGATIVE_UNCERTAIN,
        BootstrapStatus.INCLUDES_ZERO,
    }:
        classification = TemporalClassification.NO_EDGE
        rationale = "most annual evidence is weak or negative"
    else:
        classification = TemporalClassification.NON_STATIONARY
        rationale = "signs and temporal diagnostics are not stable"
    return {
        "configuration": configuration,
        "classification": classification,
        "rationale": rationale,
        "checks": checks,
        "candidate_declared": False,
        "candidate_frozen": False,
        "production_approved": False,
    }


def _explain_2025(
    configuration: str,
    quarterly: tuple[Row, ...],
    side_rows: tuple[Row, ...],
    regimes: tuple[Row, ...],
    volatility: tuple[Row, ...],
    funding: tuple[Row, ...],
    concentration: tuple[Row, ...],
    yearly: tuple[Row, ...],
    cost_rows: tuple[Row, ...],
) -> Row:
    quarters_2025 = tuple(
        item for item in quarterly if str(item["period"]).startswith("2025-Q")
    )
    side_2025 = tuple(
        item
        for item in side_rows
        if item["dimension"] == "YEAR" and item["period"] == "2025"
    )
    funding_2025 = next(
        (
            item
            for item in funding
            if item["period_type"] == "YEAR" and item["period"] == "2025"
        ),
        None,
    )
    concentration_2025 = next(
        (
            item
            for item in concentration
            if item["period_type"] == "YEAR"
            and item["period"] == "2025"
            and item["side"] == "ALL"
        ),
        None,
    )
    cost_2025 = tuple(
        item
        for item in cost_rows
        if item["period_type"] == "YEAR" and item["period"] == "2025"
    )
    yearly_returns = {
        str(item["period"]): Decimal(str(item["net_return_percent"]))
        for item in yearly
    }
    prior_positive = tuple(
        year
        for year, value in yearly_returns.items()
        if year != "2025" and value > 0
    )
    top_quarters = sorted(
        (
            (str(item["period"]), Decimal(str(item["net_pnl"])))
            for item in quarters_2025
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    top_sides = sorted(
        (
            (str(item["side"]), Decimal(str(item["net_pnl"])))
            for item in side_2025
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    top_regimes = sorted(
        (
            (str(item["regime"]), Decimal(str(item["net_pnl"])))
            for item in regimes
            if item["period"] == "2025"
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    top_volatility = sorted(
        (
            (
                str(item["volatility_bucket"]),
                Decimal(str(item["net_return_percent"])),
            )
            for item in volatility
            if item["period"] == "2025"
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    low = next(
        (
            Decimal(str(item["net_pnl"]))
            for item in cost_2025
            if item["scenario"] == "LOW_COST"
        ),
        Decimal("0"),
    )
    base = next(
        (
            Decimal(str(item["net_pnl"]))
            for item in cost_2025
            if item["scenario"] == "BASE_COST"
        ),
        Decimal("0"),
    )
    evidence = (
        "REPEATED_PATTERN"
        if prior_positive
        else "NOT_OBSERVED_PREVIOUSLY"
        if yearly_returns.get("2025", Decimal("0")) > 0
        else "INSUFFICIENT_EVIDENCE"
    )
    return {
        "configuration": configuration,
        "language_policy": (
            "ASSOCIATED_WITH;CONCENTRATED_IN;NOT_OBSERVED_PREVIOUSLY;"
            "REPEATED_PATTERN;INSUFFICIENT_EVIDENCE"
        ),
        "top_quarters": top_quarters,
        "side_contribution": top_sides,
        "top_regimes_full_period_diagnostic": top_regimes,
        "top_volatility_buckets_full_period_diagnostic": top_volatility,
        "top_5_concentration_percent": (
            concentration_2025["top_5_concentration_percent"]
            if concentration_2025
            else None
        ),
        "result_without_top_5": (
            concentration_2025["result_without_top_5"]
            if concentration_2025
            else None
        ),
        "funding_effect": (
            funding_2025["difference"] if funding_2025 else None
        ),
        "low_cost_minus_base_pnl": low - base,
        "prior_positive_years": prior_positive,
        "historical_pattern": evidence,
        "causal_claim": False,
    }


class FuturesTemporalRobustnessService:
    def __init__(
        self,
        repository: DatabaseRepository,
        config: TradingConfig,
    ) -> None:
        self._repository = repository
        self._config = config

    def run(
        self,
        request: TemporalRobustnessRequest,
    ) -> TemporalRobustnessBundle:
        request.validate()
        started_at = datetime.now(tz=UTC)
        started_clock = time.monotonic()
        candles = self._repository.get_futures_candles(
            request.symbol,
            request.interval,
            start_time=request.start,
            end_time=request.end,
        )
        marks = self._repository.get_mark_prices(
            request.symbol,
            request.interval,
            start_time=request.start,
            end_time=request.end,
        )
        funding = self._repository.get_funding_rates(
            request.symbol,
            start_time=request.start,
            end_time=request.end,
        )
        if any(item.open_time >= _CONSUMED_START for item in candles):
            raise ValueError("consumed Futures test data must not be loaded")
        integrity = inspect_public_dataset(
            candles,
            marks,
            funding,
            requested_start=request.start,
            requested_end=request.end,
            gap_policy=FuturesGapPolicy.WARN,
        )
        if integrity.readiness is ReadinessStatus.NOT_READY:
            raise ValueError("Futures public dataset readiness is NOT_READY")
        if integrity.combined_dataset_hash != request.dataset_hash:
            raise ValueError(
                "dataset hash mismatch: expected "
                f"{request.dataset_hash}, observed {integrity.combined_dataset_hash}"
            )
        dataset = validate_futures_dataset(
            candles,
            marks,
            funding,
            source="BINANCE_USD_M_PUBLIC_SQLITE",
            funding_enabled=True,
            funding_missing_policy=FundingMissingPolicy.FAIL,
        )
        base_config = base_futures_config(self._config)
        variants = predefined_futures_variants()
        contexts, volatility_boundaries = compute_temporal_contexts(
            dataset.candles,
            base_config,
            development_end=datetime(2024, 12, 31, 23, tzinfo=UTC),
        )
        yearly_rows: list[Row] = []
        quarterly_rows: list[Row] = []
        rolling_rows: list[Row] = []
        walk_rows: list[Row] = []
        boundary_rows: list[Row] = []
        leave_rows: list[Row] = []
        regime_rows: list[Row] = []
        transition_rows: list[Row] = []
        volatility_rows: list[Row] = []
        market_context_rows: list[Row] = []
        side_rows: list[Row] = []
        funding_rows: list[Row] = []
        cost_rows: list[Row] = []
        concentration_rows: list[Row] = []
        bootstrap_summaries: list[BootstrapSummary] = []
        rolling_definitions = tuple(
            period
            for window_days, step_days in ((90, 30), (180, 60), (365, 90))
            for period in rolling_periods(
                request.start,
                request.end,
                window_days=window_days,
                step_days=step_days,
            )
        )
        for variant in variants:
            configuration = variant.variant_id
            run_config = variant_config(base_config, variant)
            scenario_results: dict[str, FuturesBacktestResult] = {}
            for scenario, scenario_config in futures_cost_scenarios(
                run_config
            ).items():
                scenario_results[scenario] = FuturesBacktestEngine(
                    scenario_config
                ).run(
                    dataset.candles,
                    dataset.mark_prices,
                    dataset.funding_rates,
                )
            result = scenario_results["BASE_COST"]
            disabled_config = replace(
                run_config,
                funding_enabled=False,
                funding_missing_policy=FundingMissingPolicy.DISABLE_EXPLICITLY,
            )
            disabled_result = FuturesBacktestEngine(disabled_config).run(
                dataset.candles,
                dataset.mark_prices,
                dataset.funding_rates,
            )
            yearly = tuple(
                aggregate_period(
                    configuration,
                    result,
                    contexts,
                    period=name,
                    start=start,
                    end=end,
                )
                for name, start, end in calendar_year_periods()
            )
            quarterly = tuple(
                aggregate_period(
                    configuration,
                    result,
                    contexts,
                    period=name,
                    start=start,
                    end=end,
                )
                for name, start, end in calendar_quarter_periods()
            )
            rolling = tuple(
                {
                    **aggregate_period(
                        configuration,
                        result,
                        contexts,
                        period=name,
                        start=start,
                        end=end,
                    ),
                    "window_days": int(name.split("D-", maxsplit=1)[0]),
                    "step_days": (
                        30
                        if name.startswith("90D-")
                        else 60
                        if name.startswith("180D-")
                        else 90
                    ),
                }
                for name, start, end in rolling_definitions
            )
            yearly_rows.extend(yearly)
            quarterly_rows.extend(quarterly)
            rolling_rows.extend(rolling)
            walk_rows.extend(_walk_forward_rows(configuration, result, contexts))
            boundary_rows.extend(_boundary_rows(configuration, result, contexts))
            leave_rows.extend(leave_one_year_out(configuration, result))
            regime_rows.extend(
                _regime_rows(configuration, result, dataset.candles, contexts)
            )
            transition_rows.extend(
                _transition_rows(configuration, result, dataset.candles, contexts)
            )
            volatility_rows.extend(
                _volatility_rows(configuration, result, contexts)
            )
            market_context_rows.extend(
                _market_context_rows(configuration, result, contexts)
            )
            side_rows.extend(
                _side_contribution_rows(configuration, result, contexts)
            )
            funding_rows.extend(
                _funding_impact_rows(
                    configuration,
                    result,
                    disabled_result,
                    threshold_percent=request.funding_dominance_percent,
                )
            )
            cost_rows.extend(
                _cost_impact_rows(
                    configuration,
                    scenario_results,
                    contexts,
                    rolling_definitions,
                )
            )
            concentration_rows.extend(
                _temporal_concentration_rows(configuration, result)
            )
            bootstrap_summaries.append(
                bootstrap_trade_pnls(
                    configuration,
                    result.trades,
                    iterations=request.bootstrap_iterations,
                    seed=request.bootstrap_seed,
                )
            )
        scorecards: list[Row] = []
        classifications: list[Row] = []
        explanations: list[Row] = []
        for variant in variants:
            configuration = variant.variant_id
            selected_yearly = tuple(
                item for item in yearly_rows if item["configuration"] == configuration
            )
            selected_quarterly = tuple(
                item
                for item in quarterly_rows
                if item["configuration"] == configuration
            )
            selected_rolling = tuple(
                item
                for item in rolling_rows
                if item["configuration"] == configuration
            )
            selected_walk = tuple(
                item for item in walk_rows if item["configuration"] == configuration
            )
            selected_boundaries = tuple(
                item
                for item in boundary_rows
                if item["configuration"] == configuration
            )
            selected_leave = tuple(
                item for item in leave_rows if item["configuration"] == configuration
            )
            selected_regimes = tuple(
                item
                for item in regime_rows
                if item["configuration"] == configuration
            )
            selected_volatility = tuple(
                item
                for item in volatility_rows
                if item["configuration"] == configuration
            )
            selected_funding = tuple(
                item
                for item in funding_rows
                if item["configuration"] == configuration
            )
            selected_costs = tuple(
                item
                for item in cost_rows
                if item["configuration"] == configuration
            )
            selected_concentration = tuple(
                item
                for item in concentration_rows
                if item["configuration"] == configuration
            )
            bootstrap = next(
                item
                for item in bootstrap_summaries
                if item.configuration == configuration
            )
            scorecard = _scorecard(
                configuration,
                selected_yearly,
                selected_quarterly,
                selected_rolling,
                selected_walk,
                selected_boundaries,
                selected_leave,
                selected_regimes,
                selected_volatility,
                selected_funding,
                selected_costs,
                selected_concentration,
                bootstrap,
            )
            scorecards.append(scorecard)
            classifications.append(
                _configuration_classification(
                    configuration,
                    selected_yearly,
                    selected_rolling,
                    selected_boundaries,
                    selected_regimes,
                    selected_concentration,
                    bootstrap,
                    scorecard,
                )
            )
            explanations.append(
                _explain_2025(
                    configuration,
                    selected_quarterly,
                    tuple(
                        item
                        for item in side_rows
                        if item["configuration"] == configuration
                    ),
                    selected_regimes,
                    selected_volatility,
                    selected_funding,
                    selected_concentration,
                    selected_yearly,
                    selected_costs,
                )
            )
        warnings = tuple(
            dict.fromkeys(
                tuple(integrity.warnings)
                + tuple(
                    warning
                    for rows in (
                        boundary_rows,
                        leave_rows,
                        funding_rows,
                        cost_rows,
                        concentration_rows,
                    )
                    for row in rows
                    if row.get("warning")
                    for warning in str(row["warning"]).split(";")
                )
                + (
                    "POST_EVENT_DIAGNOSTICS_ONLY",
                    "NO_CAUSAL_INTERPRETATION",
                    "NO_CANDIDATE_FREEZE",
                )
            )
        )
        reproducibility_hash = hashlib.sha256(
            json.dumps(
                serialize_model(
                    {
                        "experiment_version": "FUTURES_TEMPORAL_ROBUSTNESS_V1",
                        "request": request,
                        "dataset_hash": integrity.combined_dataset_hash,
                        "variants": variants,
                        "volatility_boundaries": volatility_boundaries,
                        "cost_scenarios": tuple(
                            futures_cost_scenarios(base_config)
                        ),
                    }
                ),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        completed_at = datetime.now(tz=UTC)
        experiment_id = (
            f"futures-temporal-robustness-"
            f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{integrity.combined_dataset_hash[:8]}"
        )
        return TemporalRobustnessBundle(
            experiment_id=experiment_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=Decimal(str(time.monotonic() - started_clock)),
            request=request,
            integrity=integrity,
            variants=variants,
            volatility_boundaries=volatility_boundaries,
            yearly_rows=tuple(yearly_rows),
            quarterly_rows=tuple(quarterly_rows),
            rolling_rows=tuple(rolling_rows),
            walk_forward_rows=tuple(walk_rows),
            boundary_rows=tuple(boundary_rows),
            leave_one_year_out_rows=tuple(leave_rows),
            regime_rows=tuple(regime_rows),
            transition_rows=tuple(transition_rows),
            volatility_rows=tuple(volatility_rows),
            market_context_rows=tuple(market_context_rows),
            side_rows=tuple(side_rows),
            funding_rows=tuple(funding_rows),
            cost_rows=tuple(cost_rows),
            concentration_rows=tuple(concentration_rows),
            bootstrap_summaries=tuple(bootstrap_summaries),
            scorecards=tuple(scorecards),
            classifications=tuple(classifications),
            explanations_2025=tuple(explanations),
            warnings=warnings,
            reproducibility_hash=reproducibility_hash,
        )
