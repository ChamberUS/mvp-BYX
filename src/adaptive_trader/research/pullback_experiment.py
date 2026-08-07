"""Offline orchestration for Sprint 3B.1 pullback-continuation research."""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from adaptive_trader.backtest.engine import BacktestEngine
from adaptive_trader.backtest.models import BacktestResult
from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.domain.models import (
    Candle,
    MarketRegime,
    SignalDirection,
)
from adaptive_trader.domain.protocols import MarketAnalyzer
from adaptive_trader.execution.backtest import (
    BacktestExecutionConfig,
    BacktestOrderExecutor,
)
from adaptive_trader.futures.datasets import FuturesDataset, validate_futures_dataset
from adaptive_trader.futures.engine import FuturesBacktestEngine
from adaptive_trader.futures.models import (
    FuturesBacktestConfig,
    FuturesBacktestResult,
    FuturesCandle,
    FuturesExitReason,
    FuturesRiskReasonCode,
    FuturesSignalDirection,
    MarkPriceCandle,
)
from adaptive_trader.futures.pullback import PullbackContinuationFuturesAnalyzer
from adaptive_trader.futures.real_validation import (
    base_futures_config,
    futures_cost_scenarios,
)
from adaptive_trader.futures.strategy import (
    DeterministicFuturesAnalyzer,
    FuturesMarketAnalyzer,
)
from adaptive_trader.research.costs import cost_scenarios
from adaptive_trader.research.datasets import (
    candles_hash,
    validate_dataset,
)
from adaptive_trader.research.models import GapPolicy
from adaptive_trader.research.pullback_analysis import (
    BootstrapResult,
    CandidateAssessment,
    DevelopmentSelection,
    PullbackClosedTrade,
    PullbackFold,
    PullbackRun,
    WalkForwardSummary,
    assess_candidate,
    bootstrap_trades,
    build_future_holdout_plan,
    concentration_metrics,
    cost_warning,
    no_development_assessment,
    select_development_hypotheses,
    summarize_folds,
)
from adaptive_trader.research.pullback_catalog import (
    CATALOG_FILE,
    PullbackExperimentPeriods,
    PullbackHypothesis,
    PullbackHypothesisCatalog,
    PullbackValidationLock,
    load_pullback_catalog,
)
from adaptive_trader.risk.manager import DefaultRiskManager
from adaptive_trader.storage.sqlite import DatabaseRepository
from adaptive_trader.strategy.deterministic import DeterministicAnalyzer
from adaptive_trader.strategy.pullback import (
    PullbackContinuationAnalyzer,
    PullbackDecisionTrace,
    PullbackParameters,
    PullbackReasonCode,
)

_HOUR = timedelta(hours=1)
_WARMUP_CANDLES = 100
_DEVELOPMENT_TRAIN_DAYS = 365
_VALIDATION_DAYS = 90
_STEP_DAYS = 90
_COST_SCENARIOS = ("LOW", "BASE", "HIGH", "STRESS")
_REQUIRED_REASON_CODES = tuple(
    code.value
    for code in PullbackReasonCode
    if code
    not in {
        PullbackReasonCode.INSUFFICIENT_DATA,
        PullbackReasonCode.REGIME_LOSS_EXIT,
        PullbackReasonCode.POSITION_MANAGED_BY_ENGINE,
    }
)


@dataclass(frozen=True, slots=True)
class PullbackExperimentRequest:
    symbol: str
    interval: str
    periods: PullbackExperimentPeriods
    markets: tuple[str, ...]
    futures_modes: tuple[str, ...]
    leverage: Decimal
    output_dir: Path

    def validate(self) -> None:
        self.periods.assert_pre_registered()
        self.periods.assert_research_range(
            self.periods.development_start,
            self.periods.development_end,
            "development",
        )
        self.periods.assert_research_range(
            self.periods.validation_start,
            self.periods.validation_end,
            "validation",
        )
        if self.symbol != "ETHUSDT" or self.interval != "1h":
            raise ValueError("Sprint 3B.1 is pre-registered for ETHUSDT 1h only")
        if self.leverage != Decimal("1"):
            raise ValueError("Sprint 3B.1 permits Futures leverage 1 only")
        if not self.markets or len(self.markets) != len(set(self.markets)):
            raise ValueError("markets must be a non-empty unique list")
        if any(market not in {"spot", "futures"} for market in self.markets):
            raise ValueError("markets accepts only spot,futures")
        if len(self.futures_modes) != len(set(self.futures_modes)):
            raise ValueError("futures modes must be unique")
        if any(
            mode not in {"long", "short", "long-short"}
            for mode in self.futures_modes
        ):
            raise ValueError("unsupported Futures mode")
        if "futures" in self.markets and not self.futures_modes:
            raise ValueError("Futures research requires at least one mode")


@dataclass(frozen=True, slots=True)
class PullbackFoldWindow:
    fold: int
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime


@dataclass(frozen=True, slots=True)
class PullbackExperimentBundle:
    experiment_id: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: Decimal
    request: PullbackExperimentRequest
    catalog: PullbackHypothesisCatalog
    catalog_file_sha256: str
    dataset_manifest: dict[str, object]
    selections: tuple[DevelopmentSelection, ...]
    validation_locks: tuple[PullbackValidationLock, ...]
    development_results: tuple[dict[str, object], ...]
    development_walk_forward: tuple[dict[str, object], ...]
    validation_results: tuple[dict[str, object], ...]
    validation_walk_forward: tuple[dict[str, object], ...]
    market_comparison: tuple[dict[str, object], ...]
    side_contribution: tuple[dict[str, object], ...]
    cost_scenarios: tuple[dict[str, object], ...]
    pullback_decision_funnel: tuple[dict[str, object], ...]
    pullback_reason_codes: tuple[dict[str, object], ...]
    pullback_entry_diagnostics: tuple[dict[str, object], ...]
    regime_loss_exit_diagnostics: tuple[dict[str, object], ...]
    concentration_analysis: tuple[dict[str, object], ...]
    bootstrap_uncertainty: tuple[BootstrapResult, ...]
    assessments: tuple[CandidateAssessment, ...]
    future_holdout_plan: dict[str, object]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PriceBar:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class _SpotSegment:
    candles: tuple[Candle, ...]
    bars: tuple[_PriceBar, ...]
    evaluation_start: datetime
    evaluation_end: datetime


@dataclass(frozen=True, slots=True)
class _FuturesSegment:
    candles: tuple[FuturesCandle, ...]
    marks: tuple[MarkPriceCandle, ...]
    funding: tuple[object, ...]
    bars: tuple[_PriceBar, ...]
    evaluation_start: datetime
    evaluation_end: datetime


def build_pullback_walk_forward_windows(
    *,
    period: str,
    periods: PullbackExperimentPeriods,
) -> tuple[PullbackFoldWindow, ...]:
    periods.assert_pre_registered()
    if period == "DEVELOPMENT":
        cursor = periods.development_start + timedelta(
            days=_DEVELOPMENT_TRAIN_DAYS
        )
        final_exclusive = periods.development_end + _HOUR
    elif period == "VALIDATION":
        cursor = periods.validation_start
        final_exclusive = periods.validation_end + _HOUR
    else:
        raise ValueError("walk-forward period must be DEVELOPMENT or VALIDATION")
    windows: list[PullbackFoldWindow] = []
    fold = 1
    while cursor + timedelta(days=_VALIDATION_DAYS) <= final_exclusive:
        validation_end = cursor + timedelta(days=_VALIDATION_DAYS) - _HOUR
        if period == "DEVELOPMENT":
            train_start = cursor - timedelta(days=_DEVELOPMENT_TRAIN_DAYS)
            train_end = cursor - _HOUR
        else:
            train_start = periods.development_start
            train_end = periods.development_end
        windows.append(
            PullbackFoldWindow(
                fold=fold,
                train_start=train_start,
                train_end=train_end,
                validation_start=cursor,
                validation_end=validation_end,
            )
        )
        fold += 1
        cursor += timedelta(days=_STEP_DAYS)
    if not windows:
        raise ValueError(f"no complete 90-day folds for {period}")
    return tuple(windows)


class PullbackExperimentService:
    def __init__(
        self,
        repository: DatabaseRepository,
        config: TradingConfig,
        *,
        catalog_path: Path = CATALOG_FILE,
    ) -> None:
        self._repository = repository
        self._config = config
        self._catalog_path = catalog_path

    def run(self, request: PullbackExperimentRequest) -> PullbackExperimentBundle:
        request.validate()
        started_clock = time.monotonic()
        started_at = datetime.now(tz=UTC)
        catalog_bytes = self._catalog_path.read_bytes()
        catalog_file_sha256 = hashlib.sha256(catalog_bytes).hexdigest()
        catalog = load_pullback_catalog(self._catalog_path)
        spot_candles, futures_dataset, dataset_manifest = self._load_datasets(
            request
        )
        groups = _market_groups(request)
        hypotheses = catalog.hypotheses
        complexity = {
            hypothesis.variant_id: hypothesis.complexity_rank
            for hypothesis in hypotheses
        }

        development_results: list[dict[str, object]] = []
        development_walk: list[dict[str, object]] = []
        validation_results: list[dict[str, object]] = []
        validation_walk: list[dict[str, object]] = []
        market_rows: list[dict[str, object]] = []
        side_rows: list[dict[str, object]] = []
        cost_rows: list[dict[str, object]] = []
        funnel_rows: list[dict[str, object]] = []
        reason_rows: list[dict[str, object]] = []
        entry_rows: list[dict[str, object]] = []
        regime_exit_rows: list[dict[str, object]] = []
        concentration_rows: list[dict[str, object]] = []
        bootstraps: list[BootstrapResult] = []
        warnings: list[str] = []

        base_runs: dict[tuple[str, str, str, str], PullbackRun] = {}
        base_summaries: dict[
            tuple[str, str, str, str], WalkForwardSummary
        ] = {}
        stress_summaries: dict[
            tuple[str, str, str, str], WalkForwardSummary
        ] = {}
        selections: list[DevelopmentSelection] = []
        locks: list[PullbackValidationLock] = []

        for market, mode in groups:
            development_summaries: list[WalkForwardSummary] = []
            for hypothesis in hypotheses:
                runs, base_folds, stress_folds, bars = self._evaluate_configuration(
                    request=request,
                    market=market,
                    mode=mode,
                    hypothesis=hypothesis,
                    period="DEVELOPMENT",
                    spot_candles=spot_candles,
                    futures_dataset=futures_dataset,
                )
                base = runs["BASE"]
                base_runs[(market, mode, hypothesis.variant_id, "DEVELOPMENT")] = (
                    base
                )
                development_results.append(_result_row(base))
                market_rows.append(_market_row(base))
                side_rows.extend(_side_rows(base))
                funnel_rows.append(_funnel_row(base))
                reason_rows.extend(_reason_rows(base))
                concentration_rows.append(_concentration_row(base))
                if not hypothesis.is_baseline:
                    entry_rows.extend(
                        _entry_diagnostic_rows(base, bars, hypothesis)
                    )
                    regime_exit_rows.extend(
                        _regime_loss_rows(base, bars, hypothesis)
                    )
                bootstrap = bootstrap_trades(
                    market=market,
                    mode=mode,
                    variant_id=hypothesis.variant_id,
                    period="DEVELOPMENT",
                    trades=base.trades,
                )
                bootstraps.append(bootstrap)
                cost_rows.extend(_cost_rows(runs))
                warnings.extend(_cost_warnings(runs))
                base_summary = summarize_folds(base_folds)
                stress_summary = summarize_folds(stress_folds)
                base_summaries[
                    (market, mode, hypothesis.variant_id, "DEVELOPMENT")
                ] = base_summary
                stress_summaries[
                    (market, mode, hypothesis.variant_id, "DEVELOPMENT")
                ] = stress_summary
                development_summaries.append(base_summary)
                development_walk.extend(
                    _walk_forward_rows(base_folds, base_summary)
                )
                development_walk.extend(
                    _walk_forward_rows(stress_folds, stress_summary)
                )
            selection = select_development_hypotheses(
                tuple(development_summaries),
                complexity_by_variant=complexity,
            )
            selections.append(selection)
            validation_ids = (
                "ORIGINAL_BASELINE",
                *selection.selected_variant_ids,
            )
            lock = PullbackValidationLock.create(
                market=market,
                mode=mode,
                variant_ids=validation_ids,
                catalog_hash=catalog.content_hash,
            )
            locks.append(lock)

        for selection, lock in zip(selections, locks, strict=True):
            validation_ids = (
                "ORIGINAL_BASELINE",
                *selection.selected_variant_ids,
            )
            lock.assert_unchanged(
                market=selection.market,
                mode=selection.mode,
                variant_ids=validation_ids,
                catalog_hash=catalog.content_hash,
            )
            for variant_id in validation_ids:
                hypothesis = catalog.by_id(variant_id)
                runs, base_folds, stress_folds, bars = self._evaluate_configuration(
                    request=request,
                    market=selection.market,
                    mode=selection.mode,
                    hypothesis=hypothesis,
                    period="VALIDATION",
                    spot_candles=spot_candles,
                    futures_dataset=futures_dataset,
                )
                base = runs["BASE"]
                key = (
                    selection.market,
                    selection.mode,
                    variant_id,
                    "VALIDATION",
                )
                base_runs[key] = base
                validation_results.append(_result_row(base))
                market_rows.append(_market_row(base))
                side_rows.extend(_side_rows(base))
                funnel_rows.append(_funnel_row(base))
                reason_rows.extend(_reason_rows(base))
                concentration_rows.append(_concentration_row(base))
                if not hypothesis.is_baseline:
                    entry_rows.extend(
                        _entry_diagnostic_rows(base, bars, hypothesis)
                    )
                    regime_exit_rows.extend(
                        _regime_loss_rows(base, bars, hypothesis)
                    )
                bootstrap = bootstrap_trades(
                    market=selection.market,
                    mode=selection.mode,
                    variant_id=variant_id,
                    period="VALIDATION",
                    trades=base.trades,
                )
                bootstraps.append(bootstrap)
                cost_rows.extend(_cost_rows(runs))
                warnings.extend(_cost_warnings(runs))
                base_summary = summarize_folds(base_folds)
                stress_summary = summarize_folds(stress_folds)
                base_summaries[key] = base_summary
                stress_summaries[key] = stress_summary
                validation_walk.extend(
                    _walk_forward_rows(base_folds, base_summary)
                )
                validation_walk.extend(
                    _walk_forward_rows(stress_folds, stress_summary)
                )
            lock.assert_unchanged(
                market=selection.market,
                mode=selection.mode,
                variant_ids=validation_ids,
                catalog_hash=catalog.content_hash,
            )

        assessments = self._assess(
            selections=tuple(selections),
            locks=tuple(locks),
            base_runs=base_runs,
            base_summaries=base_summaries,
            stress_summaries=stress_summaries,
            bootstraps=tuple(bootstraps),
            catalog_hash=catalog.content_hash,
        )
        holdout_plan = build_future_holdout_plan(assessments)
        if self._catalog_path.read_bytes() != catalog_bytes:
            raise RuntimeError("pullback catalog changed during experiment execution")
        completed_at = datetime.now(tz=UTC)
        duration = Decimal(str(time.monotonic() - started_clock))
        experiment_id = (
            "pullback-continuation-"
            f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{catalog.content_hash[:8]}"
        )
        return PullbackExperimentBundle(
            experiment_id=experiment_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            request=request,
            catalog=catalog,
            catalog_file_sha256=catalog_file_sha256,
            dataset_manifest=dataset_manifest,
            selections=tuple(selections),
            validation_locks=tuple(locks),
            development_results=tuple(development_results),
            development_walk_forward=tuple(development_walk),
            validation_results=tuple(validation_results),
            validation_walk_forward=tuple(validation_walk),
            market_comparison=tuple(market_rows),
            side_contribution=tuple(side_rows),
            cost_scenarios=tuple(cost_rows),
            pullback_decision_funnel=tuple(funnel_rows),
            pullback_reason_codes=tuple(reason_rows),
            pullback_entry_diagnostics=tuple(entry_rows),
            regime_loss_exit_diagnostics=tuple(regime_exit_rows),
            concentration_analysis=tuple(concentration_rows),
            bootstrap_uncertainty=tuple(bootstraps),
            assessments=assessments,
            future_holdout_plan=holdout_plan,
            warnings=tuple(
                dict.fromkeys(
                    (
                        *warnings,
                        "RESEARCH_ONLY_NO_ORDERS",
                        "CONSUMED_2025_2026_EXCLUDED",
                        "NO_CANDIDATE_FROZEN",
                    )
                )
            ),
        )

    def _load_datasets(
        self,
        request: PullbackExperimentRequest,
    ) -> tuple[
        tuple[Candle, ...],
        FuturesDataset | None,
        dict[str, object],
    ]:
        periods = request.periods
        spot_candles: tuple[Candle, ...] = ()
        futures_dataset: FuturesDataset | None = None
        manifest: dict[str, object] = {
            "query_start": periods.development_start,
            "query_end": periods.validation_end,
            "consumed_reference": {
                "start": periods.consumed_start,
                "end": periods.consumed_end,
                "loaded": False,
                "used": False,
                "purpose": "EXCLUDED_REFERENCE_ONLY",
            },
        }
        if "spot" in request.markets:
            spot_candles = self._repository.get_candles(
                request.symbol,
                request.interval,
                start_time=periods.development_start,
                end_time=periods.validation_end,
            )
            _assert_dataset_boundaries(
                tuple(candle.open_time for candle in spot_candles),
                periods,
                "Spot",
            )
            spot_dataset = validate_dataset(
                spot_candles,
                source="BINANCE_SPOT_PUBLIC_SQLITE",
                gap_policy=GapPolicy.WARN,
            )
            manifest["spot"] = {
                "source": spot_dataset.source,
                "candle_count": spot_dataset.candle_count,
                "first_open_time": spot_dataset.first_open_time,
                "last_open_time": spot_candles[-1].open_time,
                "content_hash": spot_dataset.content_hash,
                "development_hash": candles_hash(
                    tuple(
                        candle
                        for candle in spot_candles
                        if candle.open_time <= periods.development_end
                    )
                ),
                "validation_hash": candles_hash(
                    tuple(
                        candle
                        for candle in spot_candles
                        if periods.validation_start
                        <= candle.open_time
                        <= periods.validation_end
                    )
                ),
                "duplicate_count": spot_dataset.duplicate_count,
                "gap_count": spot_dataset.gap_count,
                "missing_candle_count": spot_dataset.missing_candle_count,
                "gap_details": _spot_gap_details(spot_candles),
                "all_closed": all(candle.is_closed for candle in spot_candles),
                "warnings": spot_dataset.warnings,
            }
        if "futures" in request.markets:
            futures_candles = self._repository.get_futures_candles(
                request.symbol,
                request.interval,
                start_time=periods.development_start,
                end_time=periods.validation_end,
            )
            marks = self._repository.get_mark_prices(
                request.symbol,
                request.interval,
                start_time=periods.development_start,
                end_time=periods.validation_end,
            )
            funding = self._repository.get_funding_rates(
                request.symbol,
                start_time=periods.development_start,
                end_time=periods.validation_end,
            )
            _assert_dataset_boundaries(
                tuple(candle.open_time for candle in futures_candles),
                periods,
                "Futures",
            )
            futures_dataset = validate_futures_dataset(
                futures_candles,
                marks,
                funding,
                source="BINANCE_USD_M_PUBLIC_SQLITE",
            )
            manifest["futures"] = {
                "source": futures_dataset.source,
                "candle_count": len(futures_dataset.candles),
                "mark_price_count": len(futures_dataset.mark_prices),
                "funding_event_count": len(futures_dataset.funding_rates),
                "first_open_time": futures_dataset.candles[0].open_time,
                "last_open_time": futures_dataset.candles[-1].open_time,
                "candle_hash": futures_dataset.candle_hash,
                "mark_price_hash": futures_dataset.mark_price_hash,
                "funding_hash": futures_dataset.funding_hash,
                "combined_dataset_hash": futures_dataset.combined_dataset_hash,
                "duplicate_count": futures_dataset.duplicate_count,
                "gap_count": futures_dataset.gap_count,
                "mark_price_missing_count": (
                    futures_dataset.mark_price_missing_count
                ),
                "funding_gap_count": futures_dataset.funding_gap_count,
                "all_closed": all(
                    candle.is_closed for candle in futures_dataset.candles
                ),
                "warnings": futures_dataset.warnings,
            }
        return spot_candles, futures_dataset, manifest

    def _evaluate_configuration(
        self,
        *,
        request: PullbackExperimentRequest,
        market: str,
        mode: str,
        hypothesis: PullbackHypothesis,
        period: str,
        spot_candles: tuple[Candle, ...],
        futures_dataset: FuturesDataset | None,
    ) -> tuple[
        dict[str, PullbackRun],
        tuple[PullbackFold, ...],
        tuple[PullbackFold, ...],
        tuple[_PriceBar, ...],
    ]:
        period_start, period_end = _period_range(request.periods, period)
        if market == "SPOT":
            spot_segment = _spot_segment(
                spot_candles,
                evaluation_start=period_start,
                evaluation_end=period_end,
            )
            spot_configs = _spot_cost_configs(
                replace(
                    self._config,
                    symbol=request.symbol,
                    interval=request.interval,
                )
            )
            runs = {
                scenario: _run_spot(
                    segment=spot_segment,
                    config=spot_configs[scenario],
                    hypothesis=hypothesis,
                    scenario=scenario,
                    period=period,
                )
                for scenario in _COST_SCENARIOS
            }
            bars = spot_segment.bars
        else:
            if futures_dataset is None:
                raise ValueError("Futures dataset was not loaded")
            futures_segment = _futures_segment(
                futures_dataset,
                evaluation_start=period_start,
                evaluation_end=period_end,
            )
            base = _futures_variant_config(
                base_futures_config(self._config),
                mode=mode,
                hypothesis=hypothesis,
            )
            futures_configs = _futures_cost_configs(base)
            runs = {
                scenario: _run_futures(
                    segment=futures_segment,
                    config=futures_configs[scenario],
                    hypothesis=hypothesis,
                    scenario=scenario,
                    period=period,
                    mode=mode,
                )
                for scenario in _COST_SCENARIOS
            }
            bars = futures_segment.bars
        windows = build_pullback_walk_forward_windows(
            period=period,
            periods=request.periods,
        )
        base_folds = tuple(
            self._run_fold(
                request=request,
                market=market,
                mode=mode,
                hypothesis=hypothesis,
                period=period,
                scenario="BASE",
                window=window,
                spot_candles=spot_candles,
                futures_dataset=futures_dataset,
            )
            for window in windows
        )
        stress_folds = tuple(
            self._run_fold(
                request=request,
                market=market,
                mode=mode,
                hypothesis=hypothesis,
                period=period,
                scenario="STRESS",
                window=window,
                spot_candles=spot_candles,
                futures_dataset=futures_dataset,
            )
            for window in windows
        )
        return runs, base_folds, stress_folds, bars

    def _run_fold(
        self,
        *,
        request: PullbackExperimentRequest,
        market: str,
        mode: str,
        hypothesis: PullbackHypothesis,
        period: str,
        scenario: str,
        window: PullbackFoldWindow,
        spot_candles: tuple[Candle, ...],
        futures_dataset: FuturesDataset | None,
    ) -> PullbackFold:
        if market == "SPOT":
            spot_segment = _spot_segment(
                spot_candles,
                evaluation_start=window.validation_start,
                evaluation_end=window.validation_end,
            )
            spot_configs = _spot_cost_configs(
                replace(
                    self._config,
                    symbol=request.symbol,
                    interval=request.interval,
                )
            )
            run = _run_spot(
                segment=spot_segment,
                config=spot_configs[scenario],
                hypothesis=hypothesis,
                scenario=scenario,
                period=period,
            )
        else:
            if futures_dataset is None:
                raise ValueError("Futures dataset was not loaded")
            futures_segment = _futures_segment(
                futures_dataset,
                evaluation_start=window.validation_start,
                evaluation_end=window.validation_end,
            )
            base = _futures_variant_config(
                base_futures_config(self._config),
                mode=mode,
                hypothesis=hypothesis,
            )
            futures_configs = _futures_cost_configs(base)
            run = _run_futures(
                segment=futures_segment,
                config=futures_configs[scenario],
                hypothesis=hypothesis,
                scenario=scenario,
                period=period,
                mode=mode,
            )
        return PullbackFold(
            fold=window.fold,
            train_start=window.train_start,
            train_end=window.train_end,
            validation_start=window.validation_start,
            validation_end=window.validation_end,
            run=run,
        )

    @staticmethod
    def _assess(
        *,
        selections: tuple[DevelopmentSelection, ...],
        locks: tuple[PullbackValidationLock, ...],
        base_runs: Mapping[tuple[str, str, str, str], PullbackRun],
        base_summaries: Mapping[
            tuple[str, str, str, str], WalkForwardSummary
        ],
        stress_summaries: Mapping[
            tuple[str, str, str, str], WalkForwardSummary
        ],
        bootstraps: tuple[BootstrapResult, ...],
        catalog_hash: str,
    ) -> tuple[CandidateAssessment, ...]:
        bootstrap_by_key = {
            (
                item.market,
                item.mode,
                item.variant_id,
                item.period,
            ): item
            for item in bootstraps
        }
        lock_by_group = {
            (lock.market, lock.mode): lock
            for lock in locks
        }
        assessments: list[CandidateAssessment] = []
        for selection in selections:
            if not selection.selected_variant_ids:
                assessments.append(
                    no_development_assessment(
                        market=selection.market,
                        mode=selection.mode,
                    )
                )
                continue
            lock = lock_by_group[(selection.market, selection.mode)]
            locked_ids = (
                "ORIGINAL_BASELINE",
                *selection.selected_variant_ids,
            )
            lock_unchanged = True
            try:
                lock.assert_unchanged(
                    market=selection.market,
                    mode=selection.mode,
                    variant_ids=locked_ids,
                    catalog_hash=catalog_hash,
                )
            except ValueError:
                lock_unchanged = False
            for variant_id in selection.selected_variant_ids:
                development_key = (
                    selection.market,
                    selection.mode,
                    variant_id,
                    "DEVELOPMENT",
                )
                validation_key = (
                    selection.market,
                    selection.mode,
                    variant_id,
                    "VALIDATION",
                )
                development_run = base_runs[development_key]
                validation_run = base_runs[validation_key]
                concentration = concentration_metrics(validation_run.trades)
                assessments.append(
                    assess_candidate(
                        market=selection.market,
                        mode=selection.mode,
                        variant_id=variant_id,
                        development=base_summaries[development_key],
                        validation=base_summaries[validation_key],
                        validation_stress=stress_summaries[validation_key],
                        validation_run=validation_run,
                        concentration=concentration,
                        bootstrap=bootstrap_by_key[validation_key],
                        total_trade_count=(
                            len(development_run.trades)
                            + len(validation_run.trades)
                        ),
                        consumed_period_used=False,
                        validation_lock_unchanged=lock_unchanged,
                    )
                )
        return tuple(assessments)


def _market_groups(
    request: PullbackExperimentRequest,
) -> tuple[tuple[str, str], ...]:
    groups: list[tuple[str, str]] = []
    if "spot" in request.markets:
        groups.append(("SPOT", "LONG"))
    if "futures" in request.markets:
        mode_names = {
            "long": "LONG",
            "short": "SHORT",
            "long-short": "LONG_SHORT",
        }
        groups.extend(
            ("FUTURES", mode_names[mode])
            for mode in request.futures_modes
        )
    return tuple(groups)


def _assert_dataset_boundaries(
    timestamps: tuple[datetime, ...],
    periods: PullbackExperimentPeriods,
    name: str,
) -> None:
    if not timestamps:
        raise ValueError(f"no local {name} candles for Sprint 3B.1")
    if timestamps[0] != periods.development_start:
        raise ValueError(
            f"{name} first candle does not match development start: "
            f"{timestamps[0].isoformat()}"
        )
    if timestamps[-1] != periods.validation_end:
        raise ValueError(
            f"{name} last candle does not match validation end: "
            f"{timestamps[-1].isoformat()}"
        )
    if any(timestamp >= periods.consumed_start for timestamp in timestamps):
        raise ValueError(f"{name} consumed 2025-2026 data was loaded")


def _spot_gap_details(
    candles: tuple[Candle, ...],
) -> tuple[dict[str, object], ...]:
    details: list[dict[str, object]] = []
    for previous, current in zip(candles, candles[1:], strict=False):
        missing = previous.open_time + _HOUR
        while missing < current.open_time:
            details.append(
                {
                    "missing_open_time": missing,
                    "previous_open_time": previous.open_time,
                    "next_open_time": current.open_time,
                    "explanation": (
                        "Candle absent from the persisted public Spot dataset; "
                        "not fabricated or forward-filled; accepted under WARN."
                    ),
                }
            )
            missing += _HOUR
    return tuple(details)


def _period_range(
    periods: PullbackExperimentPeriods,
    period: str,
) -> tuple[datetime, datetime]:
    if period == "DEVELOPMENT":
        return periods.development_start, periods.development_end
    if period == "VALIDATION":
        return periods.validation_start, periods.validation_end
    raise ValueError("period must be DEVELOPMENT or VALIDATION")


def _spot_segment(
    all_candles: tuple[Candle, ...],
    *,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> _SpotSegment:
    evaluation = tuple(
        candle
        for candle in all_candles
        if evaluation_start <= candle.open_time <= evaluation_end
    )
    if not evaluation:
        raise ValueError("Spot segment has no evaluation candles")
    prior = tuple(
        candle for candle in all_candles if candle.open_time < evaluation_start
    )[-_WARMUP_CANDLES:]
    candles = prior + evaluation
    if len(candles) <= _WARMUP_CANDLES:
        raise ValueError("Spot segment does not exceed indicator warmup")
    return _SpotSegment(
        candles=candles,
        bars=tuple(_spot_bar(candle) for candle in evaluation),
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
    )


def _futures_segment(
    dataset: FuturesDataset,
    *,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> _FuturesSegment:
    evaluation = tuple(
        candle
        for candle in dataset.candles
        if evaluation_start <= candle.open_time <= evaluation_end
    )
    if not evaluation:
        raise ValueError("Futures segment has no evaluation candles")
    prior = tuple(
        candle
        for candle in dataset.candles
        if candle.open_time < evaluation_start
    )[-_WARMUP_CANDLES:]
    candles = prior + evaluation
    if len(candles) <= _WARMUP_CANDLES:
        raise ValueError("Futures segment does not exceed indicator warmup")
    candle_times = {candle.open_time for candle in candles}
    marks = tuple(
        mark for mark in dataset.mark_prices if mark.open_time in candle_times
    )
    first = candles[0].open_time
    last = candles[-1].close_time
    funding = tuple(
        event
        for event in dataset.funding_rates
        if first <= event.funding_time <= last
    )
    return _FuturesSegment(
        candles=candles,
        marks=marks,
        funding=funding,
        bars=tuple(_futures_bar(candle) for candle in evaluation),
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
    )


def _spot_bar(candle: Candle) -> _PriceBar:
    return _PriceBar(
        open_time=candle.open_time,
        close_time=candle.close_time or candle.open_time + _HOUR,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
    )


def _futures_bar(candle: FuturesCandle) -> _PriceBar:
    return _PriceBar(
        open_time=candle.open_time,
        close_time=candle.close_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
    )


def _spot_cost_configs(config: TradingConfig) -> dict[str, TradingConfig]:
    existing = cost_scenarios(config)
    return {
        "LOW": existing["LOW_COST"],
        "BASE": existing["BASE_COST"],
        "HIGH": existing["HIGH_COST"],
        "STRESS": existing["STRESS_COST"],
    }


def _futures_cost_configs(
    config: FuturesBacktestConfig,
) -> dict[str, FuturesBacktestConfig]:
    existing = futures_cost_scenarios(config)
    return {
        "LOW": existing["LOW_COST"],
        "BASE": existing["BASE_COST"],
        "HIGH": existing["HIGH_COST"],
        "STRESS": existing["STRESS_COST"],
    }


def _parameters(
    hypothesis: PullbackHypothesis,
    *,
    minimum_volume_ratio: Decimal,
    maximum_atr_relative: Decimal,
    stop_atr_multiple: Decimal,
    target_r_multiple: Decimal,
) -> PullbackParameters:
    if hypothesis.is_baseline:
        raise ValueError("baseline does not use pullback parameters")
    return PullbackParameters(
        trend_persistence_candles=hypothesis.trend_persistence_candles,
        pullback_min_candles=hypothesis.pullback_min_candles,
        pullback_max_candles=hypothesis.pullback_max_candles,
        minimum_pullback_depth_atr=hypothesis.minimum_pullback_depth_atr,
        maximum_pullback_depth_atr=hypothesis.maximum_pullback_depth_atr,
        maximum_entry_extension_atr=hypothesis.maximum_entry_extension_atr,
        minimum_volume_ratio=(
            hypothesis.minimum_volume_ratio
            if hypothesis.minimum_volume_ratio is not None
            else minimum_volume_ratio
        ),
        maximum_atr_relative=(
            hypothesis.maximum_atr_relative
            if hypothesis.maximum_atr_relative is not None
            else maximum_atr_relative
        ),
        stop_atr_multiple=stop_atr_multiple,
        target_r_multiple=target_r_multiple,
        regime_loss_exit=hypothesis.regime_loss_exit,
        directional_close_confirmation=(
            hypothesis.directional_close_confirmation
        ),
    )


def _run_spot(
    *,
    segment: _SpotSegment,
    config: TradingConfig,
    hypothesis: PullbackHypothesis,
    scenario: str,
    period: str,
) -> PullbackRun:
    pullback_analyzer: PullbackContinuationAnalyzer | None = None
    analyzer: MarketAnalyzer
    if hypothesis.is_baseline:
        analyzer = DeterministicAnalyzer(
            short_period=config.short_ema_period,
            long_period=config.long_ema_period,
            minimum_volume_ratio=config.minimum_volume_ratio,
            maximum_atr_relative=config.maximum_atr_relative,
            stop_atr_multiple=config.stop_atr_multiple,
            target_r_multiple=config.target_r_multiple,
        )
    else:
        pullback_analyzer = PullbackContinuationAnalyzer(
            _parameters(
                hypothesis,
                minimum_volume_ratio=config.minimum_volume_ratio,
                maximum_atr_relative=config.maximum_atr_relative,
                stop_atr_multiple=config.stop_atr_multiple,
                target_r_multiple=config.target_r_multiple,
            ),
            short_period=config.short_ema_period,
            long_period=config.long_ema_period,
        )
        analyzer = pullback_analyzer
    result = BacktestEngine(
        strategy=analyzer,
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(
            BacktestExecutionConfig(
                maker_fee_bps=config.maker_fee_bps,
                taker_fee_bps=config.taker_fee_bps,
                slippage_bps=config.slippage_bps,
                spread_bps=config.spread_bps,
            )
        ),
        config=config,
        time_exit_candles=hypothesis.time_exit_candles,
        allow_strategy_exit=hypothesis.regime_loss_exit,
        strategy_version=f"pullback-research-{hypothesis.variant_id}",
    ).run(
        segment.candles,
        evaluation_start_time=segment.evaluation_start,
    )
    traces = (
        pullback_analyzer.traces
        if pullback_analyzer is not None
        else ()
    )
    return _spot_run(
        result=result,
        hypothesis=hypothesis,
        traces=traces,
        scenario=scenario,
        period=period,
        requested_start=segment.evaluation_start,
        requested_end=segment.evaluation_end,
    )


def _spot_run(
    *,
    result: BacktestResult,
    hypothesis: PullbackHypothesis,
    traces: tuple[PullbackDecisionTrace, ...],
    scenario: str,
    period: str,
    requested_start: datetime,
    requested_end: datetime,
) -> PullbackRun:
    trades = tuple(
        PullbackClosedTrade(
            market="SPOT",
            mode="LONG",
            variant_id=hypothesis.variant_id,
            period=period,
            scenario=scenario,
            side="LONG",
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            gross_pnl=trade.gross_pnl,
            fees=trade.fees,
            execution_costs=trade.slippage_cost + trade.spread_cost,
            funding_paid=Decimal("0"),
            funding_received=Decimal("0"),
            net_funding=Decimal("0"),
            liquidation_fee=Decimal("0"),
            net_pnl=trade.net_pnl,
            holding_candles=trade.holding_candles,
            exit_reason=trade.exit_reason,
            liquidated=False,
        )
        for trade in result.trades
    )
    generic = result.decision_traces
    reason_counts = Counter(
        (
            trace.reason_code.value
            for trace in traces
        )
        if traces
        else (
            trace.strategy_reason_code
            for trace in generic
        )
    )
    for trace in generic:
        if trace.strategy_reason_code == PullbackReasonCode.REGIME_LOSS_EXIT.value:
            reason_counts[trace.strategy_reason_code] += 1
    if traces:
        trend_detected = sum(trace.trend_confirmed for trace in traces)
        persistence = sum(
            trace.trend_confirmed
            and trace.trend_persistence_count
            >= hypothesis.trend_persistence_candles
            for trace in traces
        )
        pullbacks = sum(trace.pullback_detected for trace in traces)
        valid_pullbacks = sum(trace.pullback_valid for trace in traces)
        resumed = sum(trace.resumed for trace in traces)
        long_signals = sum(trace.long_eligible for trace in traces)
    else:
        trend_detected = sum(
            trace.regime is MarketRegime.TRENDING_UP for trace in generic
        )
        persistence = 0
        pullbacks = 0
        valid_pullbacks = 0
        resumed = 0
        long_signals = sum(
            trace.signal_direction is SignalDirection.BUY
            for trace in generic
        )
    approvals = sum(
        trace.signal_direction is SignalDirection.BUY
        and trace.risk_approved is True
        for trace in generic
    )
    executions = sum(
        trace.signal_direction is SignalDirection.BUY
        and trace.execution_status == "EXECUTED"
        for trace in generic
    )
    metrics = result.metrics
    return PullbackRun(
        market="SPOT",
        mode="LONG",
        variant_id=hypothesis.variant_id,
        period=period,
        scenario=scenario,
        evaluation_start=requested_start,
        evaluation_end=requested_end,
        initial_capital=metrics.initial_capital,
        final_capital=metrics.final_capital,
        gross_pnl=metrics.gross_return,
        net_pnl=metrics.final_capital - metrics.initial_capital,
        net_return_percent=(
            (metrics.final_capital - metrics.initial_capital)
            / metrics.initial_capital
            * Decimal("100")
        ),
        maximum_drawdown_percent=metrics.maximum_drawdown_percent,
        total_costs=(
            metrics.total_fees
            + metrics.estimated_slippage
            + metrics.total_spread_cost
        ),
        fees=metrics.total_fees,
        funding_paid=Decimal("0"),
        funding_received=Decimal("0"),
        net_funding=Decimal("0"),
        liquidation_count=0,
        evaluated_candles=result.evaluated_candle_count,
        entry_count=metrics.entry_count,
        approvals=approvals,
        executions=executions,
        trend_detected=trend_detected,
        persistence_accepted=persistence,
        pullbacks_detected=pullbacks,
        pullbacks_valid=valid_pullbacks,
        resumptions=resumed,
        long_signals=long_signals,
        short_signals=0,
        buy_and_hold_return_percent=metrics.buy_and_hold_return,
        long_pnl=sum((trade.net_pnl for trade in trades), Decimal("0")),
        short_pnl=Decimal("0"),
        trades=trades,
        pullback_traces=traces,
        reason_counts=tuple(sorted(reason_counts.items())),
        warnings=result.warnings,
    )


def _futures_variant_config(
    base: FuturesBacktestConfig,
    *,
    mode: str,
    hypothesis: PullbackHypothesis,
) -> FuturesBacktestConfig:
    trading_modes = {
        "LONG": TradingMode.FUTURES_LONG_ONLY,
        "SHORT": TradingMode.FUTURES_SHORT_ONLY,
        "LONG_SHORT": TradingMode.FUTURES_LONG_SHORT,
    }
    if mode not in trading_modes:
        raise ValueError("unsupported Futures mode")
    return replace(
        base,
        trading_mode=trading_modes[mode],
        leverage=Decimal("1"),
        maximum_leverage=Decimal("1"),
        time_exit_candles=hypothesis.time_exit_candles,
    )


def _run_futures(
    *,
    segment: _FuturesSegment,
    config: FuturesBacktestConfig,
    hypothesis: PullbackHypothesis,
    scenario: str,
    period: str,
    mode: str,
) -> PullbackRun:
    if config.leverage != Decimal("1"):
        raise ValueError("pullback Futures execution forbids leverage above 1")
    pullback_analyzer: PullbackContinuationFuturesAnalyzer | None = None
    analyzer: FuturesMarketAnalyzer
    if hypothesis.is_baseline:
        analyzer = DeterministicFuturesAnalyzer()
    else:
        pullback_analyzer = PullbackContinuationFuturesAnalyzer(
            _parameters(
                hypothesis,
                minimum_volume_ratio=config.minimum_volume_ratio,
                maximum_atr_relative=config.maximum_atr_relative,
                stop_atr_multiple=config.stop_atr_multiple,
                target_r_multiple=config.target_r_multiple,
            )
        )
        analyzer = pullback_analyzer
    from adaptive_trader.futures.models import FundingRate

    funding = tuple(
        event
        for event in segment.funding
        if isinstance(event, FundingRate)
    )
    result = FuturesBacktestEngine(
        config,
        analyzer=analyzer,
        strategy_version=f"pullback-research-{hypothesis.variant_id}",
    ).run(
        segment.candles,
        segment.marks,
        funding,
    )
    traces = (
        pullback_analyzer.traces
        if pullback_analyzer is not None
        else ()
    )
    return _futures_run(
        result=result,
        hypothesis=hypothesis,
        traces=traces,
        scenario=scenario,
        period=period,
        mode=mode,
        requested_start=segment.evaluation_start,
        requested_end=segment.evaluation_end,
        bars=segment.bars,
    )


def _futures_run(
    *,
    result: FuturesBacktestResult,
    hypothesis: PullbackHypothesis,
    traces: tuple[PullbackDecisionTrace, ...],
    scenario: str,
    period: str,
    mode: str,
    requested_start: datetime,
    requested_end: datetime,
    bars: tuple[_PriceBar, ...],
) -> PullbackRun:
    trades = tuple(
        PullbackClosedTrade(
            market="FUTURES",
            mode=mode,
            variant_id=hypothesis.variant_id,
            period=period,
            scenario=scenario,
            side=trade.side.value,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            gross_pnl=trade.gross_pnl,
            fees=trade.trading_fees,
            execution_costs=Decimal("0"),
            funding_paid=trade.funding_paid,
            funding_received=trade.funding_received,
            net_funding=trade.net_funding,
            liquidation_fee=trade.liquidation_fee,
            net_pnl=trade.net_pnl,
            holding_candles=trade.holding_candles,
            exit_reason=trade.exit_reason.value,
            liquidated=trade.exit_reason is FuturesExitReason.LIQUIDATION,
        )
        for trade in result.trades
    )
    generic = result.decision_traces
    close_traces = tuple(trace for trace in generic if trace.risk_reason_code is None)
    reason_counts = Counter(
        (
            trace.reason_code.value
            for trace in traces
        )
        if traces
        else (trace.reason_code for trace in close_traces)
    )
    for trace in close_traces:
        if trace.reason_code == PullbackReasonCode.REGIME_LOSS_EXIT.value:
            reason_counts[trace.reason_code] += 1
    if traces:
        trend_detected = sum(trace.trend_confirmed for trace in traces)
        persistence = sum(
            trace.trend_confirmed
            and trace.trend_persistence_count
            >= hypothesis.trend_persistence_candles
            for trace in traces
        )
        pullbacks = sum(trace.pullback_detected for trace in traces)
        valid_pullbacks = sum(trace.pullback_valid for trace in traces)
        resumed = sum(trace.resumed for trace in traces)
        long_signals = sum(trace.long_eligible for trace in traces)
        short_signals = sum(trace.short_eligible for trace in traces)
    else:
        trend_detected = sum(
            trace.regime
            in {MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN}
            for trace in close_traces
        )
        persistence = 0
        pullbacks = 0
        valid_pullbacks = 0
        resumed = 0
        long_signals = sum(
            trace.signal is FuturesSignalDirection.ENTER_LONG
            for trace in close_traces
        )
        short_signals = sum(
            trace.signal is FuturesSignalDirection.ENTER_SHORT
            for trace in close_traces
        )
    approvals = sum(
        trace.risk_reason_code is FuturesRiskReasonCode.APPROVED
        for trace in generic
    )
    metrics = result.metrics
    buy_hold = (
        (bars[-1].close - bars[0].open) / bars[0].open * Decimal("100")
        if bars and bars[0].open > 0
        else None
    )
    return PullbackRun(
        market="FUTURES",
        mode=mode,
        variant_id=hypothesis.variant_id,
        period=period,
        scenario=scenario,
        evaluation_start=requested_start,
        evaluation_end=requested_end,
        initial_capital=metrics.initial_wallet,
        final_capital=metrics.final_wallet,
        gross_pnl=metrics.gross_pnl,
        net_pnl=metrics.net_pnl,
        net_return_percent=metrics.return_on_wallet,
        maximum_drawdown_percent=metrics.maximum_drawdown,
        total_costs=metrics.trading_fees + metrics.liquidation_fees,
        fees=metrics.trading_fees + metrics.liquidation_fees,
        funding_paid=metrics.funding_paid,
        funding_received=metrics.funding_received,
        net_funding=metrics.net_funding,
        liquidation_count=metrics.liquidation_count,
        evaluated_candles=result.evaluated_candle_count,
        entry_count=metrics.trade_count,
        approvals=approvals,
        executions=metrics.trade_count,
        trend_detected=trend_detected,
        persistence_accepted=persistence,
        pullbacks_detected=pullbacks,
        pullbacks_valid=valid_pullbacks,
        resumptions=resumed,
        long_signals=long_signals,
        short_signals=short_signals,
        buy_and_hold_return_percent=buy_hold,
        long_pnl=metrics.long_pnl,
        short_pnl=metrics.short_pnl,
        trades=trades,
        pullback_traces=traces,
        reason_counts=tuple(sorted(reason_counts.items())),
        warnings=result.warnings,
    )


def _result_row(run: PullbackRun) -> dict[str, object]:
    wins = sum(trade.net_pnl > 0 for trade in run.trades)
    gains = sum(
        (trade.net_pnl for trade in run.trades if trade.net_pnl > 0),
        Decimal("0"),
    )
    losses = abs(
        sum(
            (trade.net_pnl for trade in run.trades if trade.net_pnl < 0),
            Decimal("0"),
        )
    )
    return {
        "market": run.market,
        "mode": run.mode,
        "variant_id": run.variant_id,
        "period": run.period,
        "scenario": run.scenario,
        "evaluation_start": run.evaluation_start,
        "evaluation_end": run.evaluation_end,
        "evaluated_candles": run.evaluated_candles,
        "initial_capital": run.initial_capital,
        "final_capital": run.final_capital,
        "gross_pnl": run.gross_pnl,
        "net_pnl": run.net_pnl,
        "net_return_percent": run.net_return_percent,
        "maximum_drawdown_percent": run.maximum_drawdown_percent,
        "trades": len(run.trades),
        "wins": wins,
        "win_rate_percent": (
            Decimal(wins) / Decimal(len(run.trades)) * Decimal("100")
            if run.trades
            else None
        ),
        "profit_factor": gains / losses if losses else None,
        "expectancy": (
            run.net_pnl / Decimal(len(run.trades))
            if run.trades
            else None
        ),
        "total_costs": run.total_costs,
        "fees": run.fees,
        "funding_paid": run.funding_paid,
        "funding_received": run.funding_received,
        "net_funding": run.net_funding,
        "liquidation_count": run.liquidation_count,
        "buy_and_hold_return_percent": run.buy_and_hold_return_percent,
        "warnings": ";".join(run.warnings),
    }


def _market_row(run: PullbackRun) -> dict[str, object]:
    return {
        "market": run.market,
        "mode": run.mode,
        "variant_id": run.variant_id,
        "period": run.period,
        "net_return_percent": run.net_return_percent,
        "maximum_drawdown_percent": run.maximum_drawdown_percent,
        "trades": len(run.trades),
        "long_pnl": run.long_pnl,
        "short_pnl": run.short_pnl,
        "total_costs": run.total_costs,
        "net_funding": run.net_funding,
        "buy_and_hold_return_percent": run.buy_and_hold_return_percent,
        "separate_capital": True,
        "balance_transfer": False,
    }


def _side_rows(run: PullbackRun) -> tuple[dict[str, object], ...]:
    sides = ("LONG",) if run.market == "SPOT" else ("LONG", "SHORT")
    rows: list[dict[str, object]] = []
    for side in sides:
        trades = tuple(trade for trade in run.trades if trade.side == side)
        rows.append(
            {
                "market": run.market,
                "mode": run.mode,
                "variant_id": run.variant_id,
                "period": run.period,
                "side": side,
                "trades": len(trades),
                "gross_pnl": sum(
                    (trade.gross_pnl for trade in trades), Decimal("0")
                ),
                "net_pnl": sum(
                    (trade.net_pnl for trade in trades), Decimal("0")
                ),
                "fees": sum((trade.fees for trade in trades), Decimal("0")),
                "net_funding": sum(
                    (trade.net_funding for trade in trades), Decimal("0")
                ),
                "wins": sum(trade.net_pnl > 0 for trade in trades),
            }
        )
    return tuple(rows)


def _funnel_row(run: PullbackRun) -> dict[str, object]:
    return {
        "market": run.market,
        "mode": run.mode,
        "variant_id": run.variant_id,
        "period": run.period,
        "candles_evaluated": run.evaluated_candles,
        "trend_detected": run.trend_detected,
        "persistence_accepted": run.persistence_accepted,
        "pullbacks_detected": run.pullbacks_detected,
        "pullbacks_valid": run.pullbacks_valid,
        "resumptions": run.resumptions,
        "long_signals": run.long_signals,
        "short_signals": run.short_signals,
        "risk_approvals": run.approvals,
        "executions": run.executions,
        "closed_trades": len(run.trades),
    }


def _reason_rows(run: PullbackRun) -> tuple[dict[str, object], ...]:
    counts = dict(run.reason_counts)
    names = tuple(
        dict.fromkeys(
            (
                *_REQUIRED_REASON_CODES,
                *counts,
            )
        )
    )
    return tuple(
        {
            "market": run.market,
            "mode": run.mode,
            "variant_id": run.variant_id,
            "period": run.period,
            "reason_code": name,
            "count": counts.get(name, 0),
        }
        for name in names
    )


def _walk_forward_rows(
    folds: tuple[PullbackFold, ...],
    summary: WalkForwardSummary,
) -> tuple[dict[str, object], ...]:
    rows = [
        {
            "market": fold.run.market,
            "mode": fold.run.mode,
            "variant_id": fold.run.variant_id,
            "period": fold.run.period,
            "scenario": fold.run.scenario,
            "fold": fold.fold,
            "train_start": fold.train_start,
            "train_end": fold.train_end,
            "validation_start": fold.validation_start,
            "validation_end": fold.validation_end,
            "net_return_percent": fold.run.net_return_percent,
            "maximum_drawdown_percent": fold.run.maximum_drawdown_percent,
            "trades": len(fold.run.trades),
            "total_costs": fold.run.total_costs,
            "net_funding": fold.run.net_funding,
            "long_pnl": fold.run.long_pnl,
            "short_pnl": fold.run.short_pnl,
            "locked_parameters": fold.run.period == "VALIDATION",
        }
        for fold in folds
    ]
    rows.append(
        {
            "market": summary.market,
            "mode": summary.mode,
            "variant_id": summary.variant_id,
            "period": summary.period,
            "scenario": summary.scenario,
            "fold": "CONSOLIDATED",
            "fold_count": summary.fold_count,
            "positive_fold_count": summary.positive_fold_count,
            "positive_fold_percent": summary.positive_fold_percent,
            "zero_trade_fold_count": summary.zero_trade_fold_count,
            "zero_trade_fold_percent": summary.zero_trade_fold_percent,
            "median_return_percent": summary.median_return_percent,
            "mean_return_percent": summary.mean_return_percent,
            "worst_fold_return_percent": summary.worst_fold_return_percent,
            "best_fold_return_percent": summary.best_fold_return_percent,
            "trades": summary.trades,
            "maximum_drawdown_percent": summary.maximum_drawdown_percent,
            "total_costs": summary.total_costs,
            "net_funding": summary.net_funding,
            "best_trade_concentration_percent": (
                summary.best_trade_concentration_percent
            ),
            "net_pnl_without_top_three": summary.net_pnl_without_top_three,
            "long_pnl": summary.long_pnl,
            "short_pnl": summary.short_pnl,
            "locked_parameters": summary.period == "VALIDATION",
        }
    )
    return tuple(rows)


def _cost_rows(
    runs: Mapping[str, PullbackRun],
) -> tuple[dict[str, object], ...]:
    warnings = _cost_warnings(runs)
    return tuple(
        {
            "market": run.market,
            "mode": run.mode,
            "variant_id": run.variant_id,
            "period": run.period,
            "scenario": scenario,
            "net_return_percent": run.net_return_percent,
            "gross_pnl": run.gross_pnl,
            "net_pnl": run.net_pnl,
            "trades": len(run.trades),
            "maximum_drawdown_percent": run.maximum_drawdown_percent,
            "total_costs": run.total_costs,
            "fees": run.fees,
            "funding_paid": run.funding_paid,
            "funding_received": run.funding_received,
            "net_funding": run.net_funding,
            "warnings": ";".join(warnings),
        }
        for scenario, run in (
            (name, runs[name]) for name in _COST_SCENARIOS
        )
    )


def _cost_warnings(
    runs: Mapping[str, PullbackRun],
) -> tuple[str, ...]:
    return cost_warning(
        low=runs["LOW"],
        base=runs["BASE"],
        high=runs["HIGH"],
        stress=runs["STRESS"],
    )


def _concentration_row(run: PullbackRun) -> dict[str, object]:
    metrics = concentration_metrics(run.trades)
    return {
        "market": run.market,
        "mode": run.mode,
        "variant_id": run.variant_id,
        "period": run.period,
        "trades": len(run.trades),
        **metrics,
    }


def _entry_diagnostic_rows(
    run: PullbackRun,
    bars: tuple[_PriceBar, ...],
    hypothesis: PullbackHypothesis,
) -> tuple[dict[str, object], ...]:
    approved = tuple(
        trace
        for trace in run.pullback_traces
        if trace.reason_code
        in {
            PullbackReasonCode.ENTER_LONG_APPROVED,
            PullbackReasonCode.ENTER_SHORT_APPROVED,
        }
    )
    rows: list[dict[str, object]] = []
    for trade in run.trades:
        trace = _entry_trace(approved, trade)
        if trace is None:
            continue
        mfe, mae = _mfe_mae(trade, bars, through=trade.exit_time)
        rows.append(
            {
                "market": run.market,
                "mode": run.mode,
                "variant_id": run.variant_id,
                "period": run.period,
                "side": trade.side,
                "entry_time": trade.entry_time,
                "exit_time": trade.exit_time,
                "trend_persistence": trace.trend_persistence_count,
                "pullback_candles": trace.pullback_age,
                "pullback_depth_atr": trace.pullback_depth_atr,
                "distance_to_short_ema": trace.price_to_short_ema,
                "distance_to_long_ema": trace.price_to_long_ema,
                "entry_extension_atr": (
                    abs(trace.price_to_long_ema) / trace.atr
                    if trace.atr
                    else None
                ),
                "atr_relative": trace.atr_relative,
                "volume_ratio": trace.volume_ratio,
                "mfe": mfe,
                "mae": mae,
                "holding_candles": trade.holding_candles,
                "gross_pnl": trade.gross_pnl,
                "fees": trade.fees,
                "funding": trade.net_funding,
                "net_pnl": trade.net_pnl,
                "exit_reason": trade.exit_reason,
                "point_in_time": True,
                "minimum_pullback_depth_atr": (
                    hypothesis.minimum_pullback_depth_atr
                ),
                "maximum_pullback_depth_atr": (
                    hypothesis.maximum_pullback_depth_atr
                ),
            }
        )
    return tuple(rows)


def _entry_trace(
    approved: tuple[PullbackDecisionTrace, ...],
    trade: PullbackClosedTrade,
) -> PullbackDecisionTrace | None:
    expected = (
        PullbackReasonCode.ENTER_LONG_APPROVED
        if trade.side == PositionSide.LONG.value
        else PullbackReasonCode.ENTER_SHORT_APPROVED
    )
    candidates = tuple(
        trace
        for trace in approved
        if trace.reason_code is expected and trace.timestamp <= trade.entry_time
    )
    return candidates[-1] if candidates else None


def _mfe_mae(
    trade: PullbackClosedTrade,
    bars: tuple[_PriceBar, ...],
    *,
    through: datetime,
) -> tuple[Decimal, Decimal]:
    relevant = tuple(
        bar
        for bar in bars
        if trade.entry_time <= bar.open_time <= through
    )
    if not relevant:
        return Decimal("0"), Decimal("0")
    if trade.side == PositionSide.LONG.value:
        favorable = max(bar.high for bar in relevant) - trade.entry_price
        adverse = trade.entry_price - min(bar.low for bar in relevant)
    else:
        favorable = trade.entry_price - min(bar.low for bar in relevant)
        adverse = max(bar.high for bar in relevant) - trade.entry_price
    return (
        max(favorable, Decimal("0")) * trade.quantity,
        max(adverse, Decimal("0")) * trade.quantity,
    )


def _regime_loss_rows(
    run: PullbackRun,
    bars: tuple[_PriceBar, ...],
    hypothesis: PullbackHypothesis,
) -> tuple[dict[str, object], ...]:
    if not hypothesis.regime_loss_exit:
        return ()
    approved = tuple(
        trace
        for trace in run.pullback_traces
        if trace.reason_code
        in {
            PullbackReasonCode.ENTER_LONG_APPROVED,
            PullbackReasonCode.ENTER_SHORT_APPROVED,
        }
    )
    rows: list[dict[str, object]] = []
    for trade in run.trades:
        if trade.exit_reason != PullbackReasonCode.REGIME_LOSS_EXIT.value:
            continue
        trace = _entry_trace(approved, trade)
        if trace is None:
            continue
        risk = trace.atr * Decimal("2")
        if trade.side == PositionSide.LONG.value:
            stop = trace.close_price - risk
            target = trace.close_price + risk * Decimal("2")
        else:
            stop = trace.close_price + risk
            target = trace.close_price - risk * Decimal("2")
        prior_mfe, prior_mae = _mfe_mae(
            trade,
            bars,
            through=trade.exit_time,
        )
        outcome, outcome_price, outcome_time = _counterfactual_outcome(
            trade=trade,
            bars=bars,
            stop=stop,
            target=target,
        )
        rows.append(
            {
                "market": run.market,
                "mode": run.mode,
                "variant_id": run.variant_id,
                "period": run.period,
                "side": trade.side,
                "exit_time": trade.exit_time,
                "actual_net_pnl": trade.net_pnl,
                "mfe_before_exit": prior_mfe,
                "mae_before_exit": prior_mae,
                "counterfactual_stop": stop,
                "counterfactual_target": target,
                "counterfactual_outcome": outcome,
                "counterfactual_exit_price": outcome_price,
                "counterfactual_exit_time": outcome_time,
                "counterfactual_gross_pnl": (
                    _side_pnl(
                        trade.side,
                        trade.entry_price,
                        outcome_price,
                        trade.quantity,
                    )
                    if outcome_price is not None
                    else None
                ),
                "return_after_6_candles_percent": _future_return(
                    trade, bars, 6
                ),
                "return_after_12_candles_percent": _future_return(
                    trade, bars, 12
                ),
                "return_after_24_candles_percent": _future_return(
                    trade, bars, 24
                ),
                "offline_post_event_only": True,
                "used_by_strategy": False,
                "used_for_additional_selection": False,
            }
        )
    return tuple(rows)


def _counterfactual_outcome(
    *,
    trade: PullbackClosedTrade,
    bars: tuple[_PriceBar, ...],
    stop: Decimal,
    target: Decimal,
) -> tuple[str, Decimal | None, datetime | None]:
    future = tuple(
        bar for bar in bars if bar.open_time > trade.exit_time
    )
    for bar in future:
        if trade.side == PositionSide.LONG.value:
            stop_hit = bar.low <= stop
            target_hit = bar.high >= target
        else:
            stop_hit = bar.high >= stop
            target_hit = bar.low <= target
        if stop_hit:
            return "STOP_LOSS", stop, bar.open_time
        if target_hit:
            return "TAKE_PROFIT", target, bar.open_time
    if future:
        return "PERIOD_END", future[-1].close, future[-1].close_time
    return "NO_FUTURE_CANDLES_IN_PERIOD", None, None


def _future_return(
    trade: PullbackClosedTrade,
    bars: tuple[_PriceBar, ...],
    candles: int,
) -> Decimal | None:
    future = tuple(
        bar for bar in bars if bar.open_time > trade.exit_time
    )
    if len(future) < candles:
        return None
    price = future[candles - 1].close
    raw = (price - trade.exit_price) / trade.exit_price * Decimal("100")
    return raw if trade.side == PositionSide.LONG.value else -raw


def _side_pnl(
    side: str,
    entry: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
) -> Decimal:
    if side == PositionSide.LONG.value:
        return (exit_price - entry) * quantity
    return (entry - exit_price) * quantity
