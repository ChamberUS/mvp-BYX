"""Offline real-data validation for the six pre-registered Futures 1x variants."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.domain.models import Candle, MarketRegime, serialize_model
from adaptive_trader.futures.datasets import FuturesDataset, validate_futures_dataset
from adaptive_trader.futures.engine import FuturesBacktestEngine
from adaptive_trader.futures.integrity import (
    FuturesGapPolicy,
    PublicDatasetIntegrity,
    ReadinessStatus,
    inspect_public_dataset,
)
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FundingRate,
    FuturesBacktestConfig,
    FuturesBacktestResult,
    FuturesExitReason,
    FuturesTrade,
)
from adaptive_trader.storage.sqlite import DatabaseRepository

_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class RealValidationPeriods:
    development_start: datetime
    development_end: datetime
    validation_start: datetime
    validation_end: datetime
    consumed_test_start: datetime
    consumed_test_end: datetime

    def assert_safe(self) -> None:
        values = (
            self.development_start,
            self.development_end,
            self.validation_start,
            self.validation_end,
            self.consumed_test_start,
            self.consumed_test_end,
        )
        if any(item.tzinfo is None or item.utcoffset() is None for item in values):
            raise ValueError("real validation periods must be timezone-aware")
        if not (
            self.development_start <= self.development_end
            < self.validation_start
            <= self.validation_end
            < self.consumed_test_start
            <= self.consumed_test_end
        ):
            raise ValueError("real validation periods must be ordered and non-overlapping")
        if self.validation_end >= datetime(2026, 1, 1, tzinfo=UTC):
            raise ValueError("Futures 2026 data is forbidden in Sprint 3A.5")

    def assert_pre_registered(self) -> None:
        self.assert_safe()
        expected = RealValidationPeriods(
            development_start=datetime(2022, 1, 1, tzinfo=UTC),
            development_end=datetime(2024, 12, 31, 23, tzinfo=UTC),
            validation_start=datetime(2025, 1, 1, tzinfo=UTC),
            validation_end=datetime(2025, 12, 31, 23, tzinfo=UTC),
            consumed_test_start=datetime(2026, 1, 1, tzinfo=UTC),
            consumed_test_end=datetime(2026, 7, 1, tzinfo=UTC),
        )
        if self != expected:
            raise ValueError("Sprint 3A.5 periods must match the pre-registered protocol")


@dataclass(frozen=True, slots=True)
class PredefinedFuturesVariant:
    variant_id: str
    mode: TradingMode
    time_exit_candles: int | None
    target_r_multiple: Decimal
    leverage: Decimal = Decimal("1")


@dataclass(frozen=True, slots=True)
class RealWalkForwardRun:
    variant_id: str
    period: str
    fold: int
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    scenario: str
    result: FuturesBacktestResult
    dataset_hash: str


@dataclass(frozen=True, slots=True)
class RealValidationBundle:
    experiment_id: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: Decimal
    periods: RealValidationPeriods
    integrity: PublicDatasetIntegrity
    funding_events: tuple[FundingRate, ...]
    variants: tuple[PredefinedFuturesVariant, ...]
    segment_rows: tuple[dict[str, object], ...]
    walk_forward_rows: tuple[dict[str, object], ...]
    cost_rows: tuple[dict[str, object], ...]
    funding_rows: tuple[dict[str, object], ...]
    liquidation_rows: tuple[dict[str, object], ...]
    exit_reason_rows: tuple[dict[str, object], ...]
    regime_rows: tuple[dict[str, object], ...]
    benchmark_rows: tuple[dict[str, object], ...]
    comparison_rows: tuple[dict[str, object], ...]
    assessments: tuple[dict[str, object], ...]
    warnings: tuple[str, ...]
    reproducibility_hash: str


def predefined_futures_variants() -> tuple[PredefinedFuturesVariant, ...]:
    return (
        PredefinedFuturesVariant(
            "FUTURES_LONG_BASELINE_1X",
            TradingMode.FUTURES_LONG_ONLY,
            None,
            Decimal("2"),
        ),
        PredefinedFuturesVariant(
            "FUTURES_SHORT_MIRRORED_1X",
            TradingMode.FUTURES_SHORT_ONLY,
            None,
            Decimal("2"),
        ),
        PredefinedFuturesVariant(
            "FUTURES_LONG_SHORT_BASELINE_1X",
            TradingMode.FUTURES_LONG_SHORT,
            None,
            Decimal("2"),
        ),
        PredefinedFuturesVariant(
            "FUTURES_LONG_SHORT_TIME_EXIT_12_1X",
            TradingMode.FUTURES_LONG_SHORT,
            12,
            Decimal("2"),
        ),
        PredefinedFuturesVariant(
            "FUTURES_LONG_SHORT_TIME_EXIT_24_1X",
            TradingMode.FUTURES_LONG_SHORT,
            24,
            Decimal("2"),
        ),
        PredefinedFuturesVariant(
            "FUTURES_LONG_SHORT_TARGET_R_2_5_1X",
            TradingMode.FUTURES_LONG_SHORT,
            None,
            Decimal("2.5"),
        ),
    )


def base_futures_config(config: TradingConfig) -> FuturesBacktestConfig:
    return FuturesBacktestConfig(
        initial_balance=config.initial_balance,
        leverage=Decimal("1"),
        maximum_leverage=Decimal("1"),
        maximum_position_notional_percent=Decimal("25"),
        maker_fee_bps=Decimal("2"),
        taker_fee_bps=Decimal("5"),
        spread_bps=Decimal("2"),
        slippage_bps=Decimal("5"),
        funding_enabled=True,
        funding_missing_policy=FundingMissingPolicy.FAIL,
        symbol="ETHUSDT",
        interval="1h",
        warmup_candles=config.warmup_candles,
        short_ema_period=config.short_ema_period,
        long_ema_period=config.long_ema_period,
        atr_period=config.atr_period,
        volume_period=config.volume_period,
        minimum_volume_ratio=config.minimum_volume_ratio,
        maximum_atr_relative=config.maximum_atr_relative,
        stop_atr_multiple=config.stop_atr_multiple,
        target_r_multiple=Decimal("2"),
    )


def variant_config(
    base: FuturesBacktestConfig,
    variant: PredefinedFuturesVariant,
) -> FuturesBacktestConfig:
    if variant.leverage != Decimal("1"):
        raise ValueError("Sprint 3A.5 permits only leverage 1")
    return replace(
        base,
        trading_mode=variant.mode,
        leverage=Decimal("1"),
        maximum_leverage=Decimal("1"),
        time_exit_candles=variant.time_exit_candles,
        target_r_multiple=variant.target_r_multiple,
    )


def futures_cost_scenarios(
    config: FuturesBacktestConfig,
) -> dict[str, FuturesBacktestConfig]:
    return {
        "LOW_COST": replace(
            config,
            maker_fee_bps=config.maker_fee_bps / Decimal("2"),
            taker_fee_bps=config.taker_fee_bps / Decimal("2"),
            spread_bps=config.spread_bps / Decimal("2"),
            slippage_bps=config.slippage_bps / Decimal("2"),
        ),
        "BASE_COST": config,
        "HIGH_COST": replace(
            config,
            maker_fee_bps=config.maker_fee_bps * Decimal("2"),
            taker_fee_bps=config.taker_fee_bps * Decimal("2"),
            spread_bps=config.spread_bps * Decimal("2"),
            slippage_bps=config.slippage_bps * Decimal("2"),
        ),
        "STRESS_COST": replace(
            config,
            maker_fee_bps=config.maker_fee_bps * Decimal("4"),
            taker_fee_bps=config.taker_fee_bps * Decimal("4"),
            spread_bps=config.spread_bps * Decimal("4"),
            slippage_bps=config.slippage_bps * Decimal("4"),
        ),
    }


class FuturesRealValidationService:
    def __init__(
        self,
        repository: DatabaseRepository,
        config: TradingConfig,
    ) -> None:
        self._repository = repository
        self._config = config

    def run(
        self,
        *,
        symbol: str,
        interval: str,
        periods: RealValidationPeriods,
        leverage: Decimal,
    ) -> RealValidationBundle:
        started_clock = time.monotonic()
        started_at = datetime.now(tz=UTC)
        periods.assert_safe()
        if symbol != "ETHUSDT" or interval != "1h":
            raise ValueError("Sprint 3A.5 is pre-registered for ETHUSDT 1h only")
        if leverage != Decimal("1"):
            raise ValueError("Sprint 3A.5 real validation permits only leverage 1")
        candles = self._repository.get_futures_candles(
            symbol,
            interval,
            start_time=periods.development_start,
            end_time=periods.validation_end,
        )
        marks = self._repository.get_mark_prices(
            symbol,
            interval,
            start_time=periods.development_start,
            end_time=periods.validation_end,
        )
        funding = self._repository.get_funding_rates(
            symbol,
            start_time=periods.development_start,
            end_time=periods.validation_end,
        )
        if any(item.open_time >= periods.consumed_test_start for item in candles):
            raise ValueError("consumed Futures test data must not be loaded")
        integrity = inspect_public_dataset(
            candles,
            marks,
            funding,
            requested_start=periods.development_start,
            requested_end=periods.validation_end,
            gap_policy=FuturesGapPolicy.WARN,
        )
        if integrity.readiness is ReadinessStatus.NOT_READY:
            raise ValueError("Futures public dataset readiness is NOT_READY")
        dataset = validate_futures_dataset(
            candles,
            marks,
            funding,
            source="BINANCE_USD_M_PUBLIC_SQLITE",
            funding_enabled=True,
            funding_missing_policy=FundingMissingPolicy.FAIL,
        )
        base = base_futures_config(self._config)
        variants = predefined_futures_variants()
        segment_rows: list[dict[str, object]] = []
        walk_rows: list[dict[str, object]] = []
        cost_rows: list[dict[str, object]] = []
        funding_rows: list[dict[str, object]] = []
        liquidation_rows: list[dict[str, object]] = []
        exit_rows: list[dict[str, object]] = []
        regime_rows: list[dict[str, object]] = []
        benchmark_rows: list[dict[str, object]] = []
        assessment_inputs: dict[str, dict[str, object]] = {}
        segment_results: dict[tuple[str, str], FuturesBacktestResult] = {}
        base_fold_runs: dict[tuple[str, str], tuple[RealWalkForwardRun, ...]] = {}
        for variant in variants:
            run_config = variant_config(base, variant)
            variant_segments: dict[str, FuturesBacktestResult] = {}
            for period_name, period_start, period_end in (
                (
                    "DEVELOPMENT",
                    periods.development_start,
                    periods.development_end,
                ),
                (
                    "VALIDATION",
                    periods.validation_start,
                    periods.validation_end,
                ),
            ):
                segment_dataset = _slice_dataset(
                    dataset,
                    evaluation_start=period_start,
                    evaluation_end=period_end,
                    config=run_config,
                )
                result = _run(segment_dataset, run_config)
                segment_results[(variant.variant_id, period_name)] = result
                variant_segments[period_name] = result
                segment_rows.append(
                    _result_row(
                        variant.variant_id,
                        period_name,
                        "BASE_COST",
                        result,
                    )
                )
                liquidation_rows.extend(
                    _liquidation_rows(
                        result,
                        variant_id=variant.variant_id,
                        fold=period_name,
                    )
                )
                exit_rows.extend(
                    _exit_reason_rows(result, variant.variant_id, period_name)
                )
                regime_rows.extend(
                    _regime_rows(result, variant.variant_id, period_name)
                )
                if variant == variants[0]:
                    benchmark_rows.extend(
                        _benchmark_rows(
                            segment_dataset,
                            run_config,
                            period_name,
                            self._spot_candles(
                                symbol,
                                interval,
                                period_start,
                                period_end,
                            ),
                        )
                    )
            for period_name, period_start, period_end, first_validation in (
                (
                    "DEVELOPMENT",
                    periods.development_start,
                    periods.development_end,
                    periods.development_start + timedelta(days=365),
                ),
                (
                    "VALIDATION",
                    periods.validation_start,
                    periods.validation_end,
                    periods.validation_start,
                ),
            ):
                runs = run_real_walk_forward(
                    dataset,
                    run_config,
                    variant_id=variant.variant_id,
                    period=period_name,
                    period_start=period_start,
                    period_end=period_end,
                    first_validation_start=first_validation,
                    train_days=365,
                    validation_days=90,
                    step_days=90,
                    scenario="BASE_COST",
                )
                base_fold_runs[(variant.variant_id, period_name)] = runs
                walk_rows.extend(_walk_forward_rows(runs))
                for item in runs:
                    liquidation_rows.extend(
                        _liquidation_rows(
                            item.result,
                            variant_id=variant.variant_id,
                            fold=f"{period_name}-{item.fold}",
                        )
                    )
            validation_runs = base_fold_runs[(variant.variant_id, "VALIDATION")]
            scenario_runs: dict[str, tuple[RealWalkForwardRun, ...]] = {
                "BASE_COST": validation_runs
            }
            for scenario, scenario_config in futures_cost_scenarios(run_config).items():
                if scenario == "BASE_COST":
                    continue
                scenario_runs[scenario] = run_real_walk_forward(
                    dataset,
                    scenario_config,
                    variant_id=variant.variant_id,
                    period="VALIDATION",
                    period_start=periods.validation_start,
                    period_end=periods.validation_end,
                    first_validation_start=periods.validation_start,
                    train_days=365,
                    validation_days=90,
                    step_days=90,
                    scenario=scenario,
                )
            for scenario in ("LOW_COST", "BASE_COST", "HIGH_COST", "STRESS_COST"):
                rows = scenario_runs[scenario]
                cost_rows.extend(_cost_rows(rows))
            disabled_config = replace(
                run_config,
                funding_enabled=False,
                funding_missing_policy=FundingMissingPolicy.DISABLE_EXPLICITLY,
            )
            for period_name, period_start, period_end in (
                (
                    "DEVELOPMENT",
                    periods.development_start,
                    periods.development_end,
                ),
                (
                    "VALIDATION",
                    periods.validation_start,
                    periods.validation_end,
                ),
            ):
                disabled_dataset = _slice_dataset(
                    dataset,
                    evaluation_start=period_start,
                    evaluation_end=period_end,
                    config=disabled_config,
                )
                disabled_result = _run(disabled_dataset, disabled_config)
                enabled_result = segment_results[(variant.variant_id, period_name)]
                funding_rows.append(
                    _funding_impact_row(
                        variant.variant_id,
                        period_name,
                        enabled_result,
                        disabled_result,
                    )
                )
            development_runs = base_fold_runs[(variant.variant_id, "DEVELOPMENT")]
            stress_runs = scenario_runs["STRESS_COST"]
            assessment_inputs[variant.variant_id] = _assessment_input(
                variant,
                variant_segments["DEVELOPMENT"],
                variant_segments["VALIDATION"],
                development_runs,
                validation_runs,
                stress_runs,
                integrity,
            )
        assessments = tuple(
            _assess_variant(variant_id, values)
            for variant_id, values in assessment_inputs.items()
        )
        comparison_rows = _comparison_rows(
            variants,
            segment_results,
            base_fold_runs,
            assessments,
        )
        warnings = list(integrity.warnings)
        if liquidation_rows:
            warnings.append("UNEXPECTED_LIQUIDATION_AT_1X")
        warnings.extend(
            (
                "LIQUIDATION_MODEL_APPROXIMATE",
                "MAINTENANCE_MARGIN_APPROXIMATE",
                "FUNDING_DISABLED_DIAGNOSTIC_ONLY",
            )
        )
        reproducibility_hash = _reproducibility_hash(
            integrity,
            periods,
            variants,
            base,
        )
        completed_at = datetime.now(tz=UTC)
        experiment_id = (
            f"futures-real-1x-{started_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{integrity.combined_dataset_hash[:8]}"
        )
        return RealValidationBundle(
            experiment_id=experiment_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=Decimal(str(time.monotonic() - started_clock)),
            periods=periods,
            integrity=integrity,
            funding_events=funding,
            variants=variants,
            segment_rows=tuple(segment_rows),
            walk_forward_rows=tuple(walk_rows),
            cost_rows=tuple(cost_rows),
            funding_rows=tuple(funding_rows),
            liquidation_rows=tuple(liquidation_rows),
            exit_reason_rows=tuple(exit_rows),
            regime_rows=tuple(regime_rows),
            benchmark_rows=tuple(benchmark_rows),
            comparison_rows=comparison_rows,
            assessments=assessments,
            warnings=tuple(dict.fromkeys(warnings)),
            reproducibility_hash=reproducibility_hash,
        )

    def _spot_candles(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> tuple[Candle, ...]:
        return self._repository.get_candles(
            symbol,
            interval,
            start_time=start,
            end_time=end,
        )


def _slice_dataset(
    dataset: FuturesDataset,
    *,
    evaluation_start: datetime,
    evaluation_end: datetime,
    config: FuturesBacktestConfig,
) -> FuturesDataset:
    evaluation = tuple(
        item
        for item in dataset.candles
        if evaluation_start <= item.open_time <= evaluation_end
    )
    prior = tuple(
        item for item in dataset.candles if item.open_time < evaluation_start
    )[-config.warmup_candles :]
    candles = prior + evaluation
    if len(prior) < config.warmup_candles and evaluation_start > dataset.candles[0].open_time:
        raise ValueError("segment does not contain the required Futures warmup")
    if len(candles) <= config.warmup_candles:
        raise ValueError("Futures segment does not exceed warmup")
    first = candles[0].open_time
    last = candles[-1].close_time
    marks = tuple(
        item
        for item in dataset.mark_prices
        if first - _INTERVAL <= item.open_time <= candles[-1].open_time
    )
    funding = tuple(
        item
        for item in dataset.funding_rates
        if first <= item.funding_time <= last
    )
    return validate_futures_dataset(
        candles,
        marks,
        funding,
        source=dataset.source,
        funding_enabled=config.funding_enabled,
        funding_missing_policy=config.funding_missing_policy,
        price_source=config.price_source,
    )


def _run(
    dataset: FuturesDataset,
    config: FuturesBacktestConfig,
) -> FuturesBacktestResult:
    if config.leverage != Decimal("1"):
        raise ValueError("real validation cannot execute leverage above 1")
    return FuturesBacktestEngine(config).run(
        dataset.candles,
        dataset.mark_prices,
        dataset.funding_rates,
    )


def run_real_walk_forward(
    dataset: FuturesDataset,
    config: FuturesBacktestConfig,
    *,
    variant_id: str,
    period: str,
    period_start: datetime,
    period_end: datetime,
    first_validation_start: datetime,
    train_days: int,
    validation_days: int,
    step_days: int,
    scenario: str,
) -> tuple[RealWalkForwardRun, ...]:
    if config.leverage != Decimal("1"):
        raise ValueError("real walk-forward permits only leverage 1")
    if min(train_days, validation_days, step_days) < 1:
        raise ValueError("real walk-forward windows must be positive")
    cursor = first_validation_start
    final_exclusive = period_end + _INTERVAL
    runs: list[RealWalkForwardRun] = []
    fold = 1
    while cursor + timedelta(days=validation_days) <= final_exclusive:
        validation_end_exclusive = cursor + timedelta(days=validation_days)
        fold_dataset = _slice_dataset(
            dataset,
            evaluation_start=cursor,
            evaluation_end=validation_end_exclusive - _INTERVAL,
            config=config,
        )
        result = _run(fold_dataset, config)
        runs.append(
            RealWalkForwardRun(
                variant_id=variant_id,
                period=period,
                fold=fold,
                train_start=cursor - timedelta(days=train_days),
                train_end=cursor - timedelta(milliseconds=1),
                validation_start=cursor,
                validation_end=validation_end_exclusive - timedelta(milliseconds=1),
                scenario=scenario,
                result=result,
                dataset_hash=fold_dataset.combined_dataset_hash,
            )
        )
        fold += 1
        cursor += timedelta(days=step_days)
    if not runs:
        raise ValueError(f"no complete real walk-forward folds for {period}")
    if any(
        item.validation_start < period_start or item.validation_end > period_end + _INTERVAL
        for item in runs
    ):
        raise ValueError("walk-forward evaluation escaped its registered period")
    return tuple(runs)


def _profit_factor(trades: tuple[FuturesTrade, ...]) -> Decimal | None:
    gains = sum((item.net_pnl for item in trades if item.net_pnl > 0), Decimal("0"))
    losses = abs(
        sum((item.net_pnl for item in trades if item.net_pnl < 0), Decimal("0"))
    )
    return gains / losses if losses else None


def _expectancy(trades: tuple[FuturesTrade, ...]) -> Decimal | None:
    return (
        sum((item.net_pnl for item in trades), Decimal("0")) / Decimal(len(trades))
        if trades
        else None
    )


def _best_trade_concentration(trades: tuple[FuturesTrade, ...]) -> Decimal:
    positive = tuple(item.net_pnl for item in trades if item.net_pnl > 0)
    total = sum(positive, Decimal("0"))
    return max(positive, default=Decimal("0")) / total * Decimal("100") if total else Decimal("0")


def _result_row(
    variant_id: str,
    period: str,
    scenario: str,
    result: FuturesBacktestResult,
) -> dict[str, object]:
    metrics = result.metrics
    trades = result.trades
    wins = sum(item.net_pnl > 0 for item in trades)
    return {
        "configuration": variant_id,
        "period": period,
        "scenario": scenario,
        "leverage": result.leverage,
        "trades": metrics.trade_count,
        "long_trades": metrics.long_trade_count,
        "short_trades": metrics.short_trade_count,
        "net_return_percent": metrics.return_on_wallet,
        "gross_return_percent": (
            metrics.gross_pnl / metrics.initial_wallet * Decimal("100")
        ),
        "maximum_drawdown_percent": metrics.maximum_drawdown,
        "win_rate_percent": (
            Decimal(wins) / Decimal(len(trades)) * Decimal("100")
            if trades
            else None
        ),
        "profit_factor": _profit_factor(trades),
        "expectancy": _expectancy(trades),
        "trading_fees": metrics.trading_fees,
        "funding_paid": metrics.funding_paid,
        "funding_received": metrics.funding_received,
        "net_funding": metrics.net_funding,
        "liquidation_fees": metrics.liquidation_fees,
        "liquidation_count": metrics.liquidation_count,
        "exposure_long_percent": metrics.exposure_long_percent,
        "exposure_short_percent": metrics.exposure_short_percent,
        "average_margin_utilization_percent": metrics.average_margin_utilization,
        "average_effective_leverage": metrics.average_effective_leverage,
        "minimum_margin_ratio": metrics.minimum_margin_ratio,
        "best_trade_concentration_percent": _best_trade_concentration(trades),
        "bankrupt": metrics.bankrupt,
        "depleted": metrics.depleted,
        "warnings": ";".join(result.warnings),
    }


def _walk_forward_rows(
    runs: tuple[RealWalkForwardRun, ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            **_result_row(
                item.variant_id,
                item.period,
                item.scenario,
                item.result,
            ),
            "fold": item.fold,
            "train_start": item.train_start,
            "train_end": item.train_end,
            "validation_start": item.validation_start,
            "validation_end": item.validation_end,
            "dataset_hash": item.dataset_hash,
        }
        for item in runs
    )


def _cost_rows(
    runs: tuple[RealWalkForwardRun, ...],
) -> tuple[dict[str, object], ...]:
    rows = list(_walk_forward_rows(runs))
    returns = tuple(item.result.metrics.return_on_wallet for item in runs)
    rows.append(
        {
            "configuration": runs[0].variant_id,
            "period": runs[0].period,
            "scenario": runs[0].scenario,
            "fold": "CONSOLIDATED",
            "fold_count": len(runs),
            "positive_fold_percent": (
                Decimal(sum(item > 0 for item in returns))
                / Decimal(len(returns))
                * Decimal("100")
            ),
            "mean_return_percent": sum(returns, Decimal("0")) / Decimal(len(returns)),
            "median_return_percent": median(returns),
            "trades": sum(item.result.metrics.trade_count for item in runs),
            "trading_fees": sum(
                (item.result.metrics.trading_fees for item in runs), Decimal("0")
            ),
            "net_funding": sum(
                (item.result.metrics.net_funding for item in runs), Decimal("0")
            ),
            "funding_unchanged_by_cost_scenario": True,
        }
    )
    return tuple(rows)


def _funding_impact_row(
    variant_id: str,
    period: str,
    enabled: FuturesBacktestResult,
    disabled: FuturesBacktestResult,
) -> dict[str, object]:
    difference = enabled.metrics.net_pnl - disabled.metrics.net_pnl
    return {
        "configuration": variant_id,
        "period": period,
        "scenario": "FUNDING_DISABLED_EXPLICITLY",
        "diagnostic_only": True,
        "warning": "FUNDING_DISABLED_DIAGNOSTIC_ONLY",
        "with_funding_net_pnl": enabled.metrics.net_pnl,
        "without_funding_net_pnl": disabled.metrics.net_pnl,
        "absolute_difference": difference,
        "long_difference": enabled.metrics.long_pnl - disabled.metrics.long_pnl,
        "short_difference": enabled.metrics.short_pnl - disabled.metrics.short_pnl,
        "net_funding": enabled.metrics.net_funding,
        "pnl_explained_percent": (
            difference / abs(enabled.metrics.net_pnl) * Decimal("100")
            if enabled.metrics.net_pnl
            else None
        ),
        "candidate_assessment_eligible": False,
    }


def _liquidation_rows(
    result: FuturesBacktestResult,
    *,
    variant_id: str,
    fold: str,
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "timestamp": trade.exit_time,
            "side": trade.side.value,
            "entry": trade.entry_price,
            "mark": trade.mark_at_exit,
            "liquidation_price": trade.liquidation_price,
            "quantity": trade.quantity,
            "isolated_margin": trade.initial_margin,
            "maintenance_margin": trade.maintenance_margin_at_exit,
            "wallet_before": trade.wallet_before_exit,
            "loss": min(Decimal("0"), trade.net_pnl),
            "liquidation_fee": trade.liquidation_fee,
            "wallet_after": trade.wallet_after_exit,
            "ambiguity": trade.intrabar_ambiguous,
            "fold": fold,
            "configuration": variant_id,
            "warning": "UNEXPECTED_LIQUIDATION_AT_1X",
        }
        for trade in result.trades
        if trade.exit_reason is FuturesExitReason.LIQUIDATION
    )


def _exit_reason_rows(
    result: FuturesBacktestResult,
    variant_id: str,
    period: str,
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "configuration": variant_id,
            "period": period,
            "exit_reason": reason.value,
            "count": sum(item.exit_reason is reason for item in result.trades),
            "net_pnl": sum(
                (
                    item.net_pnl
                    for item in result.trades
                    if item.exit_reason is reason
                ),
                Decimal("0"),
            ),
        }
        for reason in FuturesExitReason
    )


def _regime_rows(
    result: FuturesBacktestResult,
    variant_id: str,
    period: str,
) -> tuple[dict[str, object], ...]:
    entry_regimes = {
        item.timestamp: item.regime
        for item in result.decision_traces
        if item.risk_reason_code is not None
    }
    rows: list[dict[str, object]] = []
    for regime in MarketRegime:
        trades = tuple(
            item
            for item in result.trades
            if entry_regimes.get(item.entry_time, MarketRegime.UNKNOWN) is regime
        )
        rows.append(
            {
                "configuration": variant_id,
                "period": period,
                "regime": regime.value,
                "trades": len(trades),
                "net_pnl": sum((item.net_pnl for item in trades), Decimal("0")),
                "win_rate_percent": (
                    Decimal(sum(item.net_pnl > 0 for item in trades))
                    / Decimal(len(trades))
                    * Decimal("100")
                    if trades
                    else None
                ),
                "profit_factor": _profit_factor(trades),
                "expectancy": _expectancy(trades),
            }
        )
    return tuple(rows)


def _benchmark_rows(
    dataset: FuturesDataset,
    config: FuturesBacktestConfig,
    period: str,
    spot_candles: tuple[Candle, ...],
) -> tuple[dict[str, object], ...]:
    first = dataset.candles[config.warmup_candles]
    last = dataset.candles[-1]
    rows: list[dict[str, object]] = [
        {
            "period": period,
            "benchmark": "CASH",
            "net_return_percent": Decimal("0"),
            "fees": Decimal("0"),
            "funding": Decimal("0"),
            "liquidations": 0,
        }
    ]
    adverse = (config.spread_bps + config.slippage_bps) / Decimal("10000")
    fee_rate = config.taker_fee_bps / Decimal("10000")
    for side, name in (
        (PositionSide.LONG, "FUTURES_LONG_1X"),
        (PositionSide.SHORT, "FUTURES_SHORT_1X"),
    ):
        entry = first.open * (
            Decimal("1") + adverse
            if side is PositionSide.LONG
            else Decimal("1") - adverse
        )
        exit_price = last.close * (
            Decimal("1") - adverse
            if side is PositionSide.LONG
            else Decimal("1") + adverse
        )
        quantity = config.initial_balance / entry
        gross = (
            (exit_price - entry) * quantity
            if side is PositionSide.LONG
            else (entry - exit_price) * quantity
        )
        fees = (entry + exit_price) * quantity * fee_rate
        funding = sum(
            (
                (
                    -(item.mark_price or first.close) * quantity * item.funding_rate
                    if side is PositionSide.LONG
                    else (item.mark_price or first.close) * quantity * item.funding_rate
                )
                for item in dataset.funding_rates
            ),
            Decimal("0"),
        )
        rows.append(
            {
                "period": period,
                "benchmark": name,
                "net_return_percent": (
                    (gross - fees + funding)
                    / config.initial_balance
                    * Decimal("100")
                ),
                "fees": fees,
                "funding": funding,
                "liquidations": 0,
                "warning": "BENCHMARK_NOT_SELECTABLE",
            }
        )
    if spot_candles:
        spot_entry = spot_candles[0].open
        spot_exit = spot_candles[-1].close
        spot_cost = (
            config.taker_fee_bps + config.spread_bps + config.slippage_bps
        ) / Decimal("10000")
        spot_return = (
            spot_exit * (Decimal("1") - spot_cost)
            / (spot_entry * (Decimal("1") + spot_cost))
            - Decimal("1")
        ) * Decimal("100")
        rows.append(
            {
                "period": period,
                "benchmark": "SPOT_BUY_AND_HOLD",
                "net_return_percent": spot_return,
                "fees": None,
                "funding": Decimal("0"),
                "liquidations": 0,
                "warning": "BENCHMARK_NOT_SELECTABLE",
            }
        )
    return tuple(rows)


def _fold_summary(runs: tuple[RealWalkForwardRun, ...]) -> dict[str, object]:
    returns = tuple(item.result.metrics.return_on_wallet for item in runs)
    trades = tuple(
        trade
        for item in runs
        for trade in item.result.trades
    )
    return {
        "fold_count": len(runs),
        "positive_fold_percent": (
            Decimal(sum(item > 0 for item in returns))
            / Decimal(len(returns))
            * Decimal("100")
        ),
        "zero_trade_fold_percent": (
            Decimal(sum(item.result.metrics.trade_count == 0 for item in runs))
            / Decimal(len(runs))
            * Decimal("100")
        ),
        "mean_return_percent": sum(returns, Decimal("0")) / Decimal(len(returns)),
        "median_return_percent": median(returns),
        "worst_return_percent": min(returns),
        "best_return_percent": max(returns),
        "worst_drawdown_percent": max(
            item.result.metrics.maximum_drawdown for item in runs
        ),
        "trades": sum(item.result.metrics.trade_count for item in runs),
        "long_trades": sum(item.result.metrics.long_trade_count for item in runs),
        "short_trades": sum(item.result.metrics.short_trade_count for item in runs),
        "fees": sum(
            (item.result.metrics.trading_fees for item in runs), Decimal("0")
        ),
        "funding": sum(
            (item.result.metrics.net_funding for item in runs), Decimal("0")
        ),
        "liquidations": sum(
            item.result.metrics.liquidation_count for item in runs
        ),
        "best_trade_concentration_percent": _best_trade_concentration(trades),
    }


def _assessment_input(
    variant: PredefinedFuturesVariant,
    development: FuturesBacktestResult,
    validation: FuturesBacktestResult,
    development_runs: tuple[RealWalkForwardRun, ...],
    validation_runs: tuple[RealWalkForwardRun, ...],
    stress_runs: tuple[RealWalkForwardRun, ...],
    integrity: PublicDatasetIntegrity,
) -> dict[str, object]:
    all_trades = development.trades + validation.trades
    return {
        "leverage": variant.leverage,
        "total_closed_trades": len(all_trades),
        "development": _fold_summary(development_runs),
        "validation": _fold_summary(validation_runs),
        "stress": _fold_summary(stress_runs),
        "validation_net_return_percent": validation.metrics.return_on_wallet,
        "worst_drawdown_percent": max(
            development.metrics.maximum_drawdown,
            validation.metrics.maximum_drawdown,
            max(item.result.metrics.maximum_drawdown for item in development_runs),
            max(item.result.metrics.maximum_drawdown for item in validation_runs),
        ),
        "best_trade_concentration_percent": _best_trade_concentration(all_trades),
        "bankrupt": development.metrics.bankrupt or validation.metrics.bankrupt,
        "depleted": development.metrics.depleted or validation.metrics.depleted,
        "unexplained_data_gaps": integrity.candles.unexplained_gap_count,
        "consumed_test_used": False,
        "proxy_mark_prices": False,
        "funding_enabled": True,
    }


def _assess_variant(
    variant_id: str,
    values: dict[str, object],
) -> dict[str, object]:
    development = values["development"]
    validation = values["validation"]
    stress = values["stress"]
    if not isinstance(development, dict) or not isinstance(validation, dict):
        raise TypeError("assessment fold summaries must be mappings")
    if not isinstance(stress, dict):
        raise TypeError("assessment stress summary must be a mapping")
    checks = {
        "minimum_total_closed_trades": int(str(values["total_closed_trades"])) >= 30,
        "development_median_fold_return": (
            Decimal(str(development["median_return_percent"])) >= 0
        ),
        "validation_median_fold_return": (
            Decimal(str(validation["median_return_percent"])) >= 0
        ),
        "development_positive_fold_percent": (
            Decimal(str(development["positive_fold_percent"])) >= 50
        ),
        "validation_positive_fold_percent": (
            Decimal(str(validation["positive_fold_percent"])) >= 50
        ),
        "validation_net_return": (
            Decimal(str(values["validation_net_return_percent"])) >= 0
        ),
        "maximum_worst_drawdown": (
            Decimal(str(values["worst_drawdown_percent"])) <= 10
        ),
        "maximum_zero_trade_folds": (
            max(
                Decimal(str(development["zero_trade_fold_percent"])),
                Decimal(str(validation["zero_trade_fold_percent"])),
            )
            <= 25
        ),
        "minimum_stress_positive_fold_percent": (
            Decimal(str(stress["positive_fold_percent"])) >= 30
        ),
        "maximum_best_trade_concentration": (
            Decimal(str(values["best_trade_concentration_percent"])) <= 50
        ),
        "no_bankruptcy": not bool(values["bankrupt"]),
        "no_depletion": not bool(values["depleted"]),
        "no_unexplained_data_gaps": int(str(values["unexplained_data_gaps"])) == 0,
        "no_consumed_test_usage": not bool(values["consumed_test_used"]),
        "no_proxy_mark_prices": not bool(values["proxy_mark_prices"]),
        "funding_enabled": bool(values["funding_enabled"]),
        "leverage_exactly_one": Decimal(str(values["leverage"])) == Decimal("1"),
    }
    insufficient = (
        int(str(development["fold_count"])) == 0
        or int(str(validation["fold_count"])) == 0
    )
    status = (
        "INCONCLUSIVE"
        if insufficient
        else "PROMISING_FOR_FURTHER_VALIDATION"
        if all(checks.values())
        else "NOT_PROMISING"
    )
    return {
        "configuration": variant_id,
        "status": status,
        "checks": checks,
        "failed_criteria": tuple(name for name, passed in checks.items() if not passed),
        "observed_metrics": values,
        "candidate_frozen": False,
        "declaration": "RESEARCH_ONLY_NOT_APPROVED_FOR_TRADING",
    }


def _comparison_rows(
    variants: tuple[PredefinedFuturesVariant, ...],
    segment_results: dict[tuple[str, str], FuturesBacktestResult],
    fold_runs: dict[tuple[str, str], tuple[RealWalkForwardRun, ...]],
    assessments: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    status_by_variant = {
        str(item["configuration"]): str(item["status"]) for item in assessments
    }
    rows: list[dict[str, object]] = [
        {
            "configuration": "SPOT_BASELINE_V1",
            "source_experiment": "spot-hypotheses-v1-20260730T194242Z-4f415516",
            "market_type": "SPOT",
            "mode": "SPOT_LONG_ONLY",
            "development_return_percent": Decimal(
                "-1.08406495597853372674200"
            ),
            "validation_return_percent": Decimal("0.1099725981614681200900"),
            "maximum_drawdown_percent": Decimal(
                "1.634885048262596260379657563"
            ),
            "development_positive_fold_percent": Decimal("37.500"),
            "validation_positive_fold_percent": Decimal("25.00"),
            "development_median_fold_return_percent": Decimal("0"),
            "validation_median_fold_return_percent": Decimal(
                "-0.09235637934184631261600"
            ),
            "trades": 47,
            "exposure_percent": Decimal(
                "0.1796637235185220237340996158"
            ),
            "fees": Decimal("110.2198451442634216232"),
            "funding": Decimal("0"),
            "liquidations": 0,
            "best_trade_concentration_percent": Decimal(
                "28.16301078979315537809807825"
            ),
            "status": "NOT_CANDIDATE",
            "combined_with_other_market": False,
        }
    ]
    for variant in variants:
        development = segment_results[(variant.variant_id, "DEVELOPMENT")]
        validation = segment_results[(variant.variant_id, "VALIDATION")]
        development_summary = _fold_summary(
            fold_runs[(variant.variant_id, "DEVELOPMENT")]
        )
        validation_summary = _fold_summary(
            fold_runs[(variant.variant_id, "VALIDATION")]
        )
        trades = development.trades + validation.trades
        rows.append(
            {
                "configuration": variant.variant_id,
                "source_experiment": "CURRENT_FUTURES_REAL_1X",
                "market_type": "USD_M_FUTURES",
                "mode": variant.mode.value,
                "development_return_percent": development.metrics.return_on_wallet,
                "validation_return_percent": validation.metrics.return_on_wallet,
                "maximum_drawdown_percent": max(
                    development.metrics.maximum_drawdown,
                    validation.metrics.maximum_drawdown,
                ),
                "development_positive_fold_percent": development_summary[
                    "positive_fold_percent"
                ],
                "validation_positive_fold_percent": validation_summary[
                    "positive_fold_percent"
                ],
                "development_median_fold_return_percent": development_summary[
                    "median_return_percent"
                ],
                "validation_median_fold_return_percent": validation_summary[
                    "median_return_percent"
                ],
                "trades": len(trades),
                "exposure_percent": (
                    development.metrics.exposure_long_percent
                    + development.metrics.exposure_short_percent
                    + validation.metrics.exposure_long_percent
                    + validation.metrics.exposure_short_percent
                )
                / Decimal("2"),
                "fees": (
                    development.metrics.trading_fees
                    + validation.metrics.trading_fees
                    + development.metrics.liquidation_fees
                    + validation.metrics.liquidation_fees
                ),
                "funding": (
                    development.metrics.net_funding
                    + validation.metrics.net_funding
                ),
                "liquidations": (
                    development.metrics.liquidation_count
                    + validation.metrics.liquidation_count
                ),
                "best_trade_concentration_percent": _best_trade_concentration(
                    trades
                ),
                "status": status_by_variant[variant.variant_id],
                "combined_with_other_market": False,
            }
        )
    return tuple(rows)


def _reproducibility_hash(
    integrity: PublicDatasetIntegrity,
    periods: RealValidationPeriods,
    variants: tuple[PredefinedFuturesVariant, ...],
    config: FuturesBacktestConfig,
) -> str:
    material = json.dumps(
        serialize_model(
            {
                "dataset_hash": integrity.combined_dataset_hash,
                "periods": periods,
                "variants": variants,
                "config": config,
                "walk_forward": {
                    "train_days": 365,
                    "validation_days": 90,
                    "step_days": 90,
                    "mode": "ROLLING",
                },
            }
        ),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
