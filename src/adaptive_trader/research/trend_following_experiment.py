"""Offline orchestration for the pre-registered Sprint 3C.1 experiment."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from statistics import pstdev

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.market import PositionSide
from adaptive_trader.domain.models import Candle
from adaptive_trader.futures.accounting import approximate_liquidation_price
from adaptive_trader.futures.integrity import (
    funding_content_hash,
    mark_price_content_hash,
)
from adaptive_trader.futures.models import (
    FundingRate,
    FuturesCandle,
    MarkPriceCandle,
)
from adaptive_trader.research.daily_aggregation import (
    DailyAggregationResult,
    DailyCandleAggregator,
    IncompleteDayPolicy,
)
from adaptive_trader.research.trend_following_analysis import (
    BootstrapStatus,
    CostScenario,
    DefensiveRiskComparison,
    RiskProfileMetrics,
    TrendFollowingDevelopmentSelection,
    TrendFollowingLockedSelection,
    TrendFollowingOperationalAssessment,
    TrendFollowingOperationalMetrics,
    TrendFollowingOperationalStatus,
    TrendFollowingSelectionMetric,
    TrendFollowingValidationLock,
    assess_operational_viability,
    bootstrap_trade_pnls,
    compare_defensive_risk,
    select_development_hypothesis,
)
from adaptive_trader.research.trend_following_catalog import (
    TREND_FOLLOWING_CATALOG_FILE,
    TrendFollowingCatalog,
    TrendFollowingHypothesis,
    TrendFollowingMarketGroup,
    TrendFollowingPeriods,
    TrendFollowingRiskModel,
    build_market_groups,
    load_trend_following_catalog,
)
from adaptive_trader.research.trend_following_engine import (
    TrendFollowingEngine,
    TrendFollowingEngineConfig,
    TrendFollowingRun,
)
from adaptive_trader.storage.sqlite import DatabaseRepository

ZERO = Decimal("0")
HUNDRED = Decimal("100")
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class TrendFollowingExperimentRequest:
    symbol: str
    source_interval: str
    strategy_interval: str
    periods: TrendFollowingPeriods
    markets: tuple[str, ...]
    futures_modes: tuple[str, ...]
    leverage: Decimal
    output_dir: Path
    incomplete_day_policy: IncompleteDayPolicy = IncompleteDayPolicy.WARN_AND_EXCLUDE

    def validate(self) -> None:
        self.periods.assert_pre_registered()
        self.periods.assert_development_range(
            self.periods.development_start,
            self.periods.development_end,
        )
        self.periods.assert_validation_range(
            self.periods.validation_start,
            self.periods.validation_end,
        )
        if self.symbol != "ETHUSDT":
            raise ValueError("Sprint 3C.1 is pre-registered for ETHUSDT only")
        if self.source_interval != "1h":
            raise ValueError("Sprint 3C.1 requires source interval 1h")
        if self.strategy_interval != "1d":
            raise ValueError("Sprint 3C.1 requires strategy interval 1d")
        if self.leverage != Decimal("1"):
            raise ValueError("Sprint 3C.1 permits leverage 1 only")
        if self.incomplete_day_policy is not IncompleteDayPolicy.WARN_AND_EXCLUDE:
            raise ValueError("valid Sprint 3C.1 research requires WARN_AND_EXCLUDE")
        build_market_groups(
            markets=self.markets,
            futures_modes=self.futures_modes,
        )


@dataclass(frozen=True, slots=True)
class TrendFollowingExperimentBundle:
    experiment_id: str
    output_path: Path
    started_at: datetime
    completed_at: datetime
    duration_seconds: Decimal
    request: TrendFollowingExperimentRequest
    catalog: TrendFollowingCatalog
    aggregation_integrity: tuple[dict[str, object], ...]
    daily_dataset_hashes: tuple[dict[str, object], ...]
    decision_funnel: tuple[dict[str, object], ...]
    decision_traces: tuple[dict[str, object], ...]
    development_results: tuple[dict[str, object], ...]
    development_walk_forward: tuple[dict[str, object], ...]
    operational_viability: tuple[dict[str, object], ...]
    development_selection: tuple[dict[str, object], ...]
    validation_lock: dict[str, object]
    validation_lock_bytes: bytes
    validation_results: tuple[dict[str, object], ...]
    validation_walk_forward: tuple[dict[str, object], ...]
    defensive_risk_comparison: tuple[dict[str, object], ...]
    cost_scenarios: tuple[dict[str, object], ...]
    funding_impact: tuple[dict[str, object], ...]
    side_contribution: tuple[dict[str, object], ...]
    concentration_analysis: tuple[dict[str, object], ...]
    bootstrap_uncertainty: tuple[dict[str, object], ...]
    assessments: tuple[dict[str, object], ...]
    future_confirmation_plan: dict[str, object]
    manifest: dict[str, object]


@dataclass(frozen=True, slots=True)
class _PeriodData:
    period: str
    spot_hourly: tuple[Candle, ...]
    spot_aggregation: DailyAggregationResult[Candle] | None
    futures_hourly: tuple[FuturesCandle, ...]
    futures_aggregation: DailyAggregationResult[FuturesCandle] | None
    marks: tuple[MarkPriceCandle, ...]
    funding: tuple[FundingRate, ...]

    def daily_for(self, group: TrendFollowingMarketGroup) -> tuple[Candle, ...]:
        if group.market == "SPOT":
            if self.spot_aggregation is None:
                raise ValueError("Spot daily data was not loaded")
            return self.spot_aggregation.candles
        if self.futures_aggregation is None:
            raise ValueError("Futures daily data was not loaded")
        return tuple(candle.as_indicator_candle() for candle in self.futures_aggregation.candles)


@dataclass(frozen=True, slots=True)
class _CostParameters:
    scenario: str
    fee_bps: Decimal
    spread_bps: Decimal
    slippage_bps: Decimal


@dataclass(frozen=True, slots=True)
class _FoldWindow:
    fold: int
    train_start: datetime
    train_end: datetime
    evaluation_start: datetime
    evaluation_end: datetime


@dataclass(frozen=True, slots=True)
class _FoldSummary:
    fold_count: int
    folds_with_trades: int
    zero_trade_fold_percent: Decimal
    median_return_percent: Decimal
    mean_return_percent: Decimal
    positive_fold_percent: Decimal
    worst_return_percent: Decimal
    maximum_drawdown_percent: Decimal
    trades: int


class TrendFollowingExperimentService:
    """Run development, persist its lock, then load and run validation."""

    def __init__(
        self,
        repository: DatabaseRepository,
        config: TradingConfig,
        *,
        catalog_path: Path = TREND_FOLLOWING_CATALOG_FILE,
    ) -> None:
        self._repository = repository
        self._config = config
        self._catalog_path = catalog_path
        self._engine = TrendFollowingEngine()
        self._aggregator = DailyCandleAggregator(policy=IncompleteDayPolicy.WARN_AND_EXCLUDE)

    def run(
        self,
        request: TrendFollowingExperimentRequest,
        *,
        git_commit: str,
        git_dirty: bool,
    ) -> TrendFollowingExperimentBundle:
        request.validate()
        started_clock = time.monotonic()
        started_at = datetime.now(tz=UTC)
        catalog_bytes = self._catalog_path.read_bytes()
        catalog = load_trend_following_catalog(self._catalog_path)
        groups = build_market_groups(
            markets=request.markets,
            futures_modes=request.futures_modes,
        )

        # Development is the only dataset touched before selection and lock.
        development = self._load_period(
            request,
            period="DEVELOPMENT",
            start=request.periods.development_start,
            end=request.periods.development_end,
        )
        development_hashes = _lock_dataset_hashes(development)
        development_runs: dict[tuple[str, str, str], TrendFollowingRun] = {}
        development_summaries: dict[tuple[str, str], _FoldSummary] = {}
        development_results: list[dict[str, object]] = []
        development_walk: list[dict[str, object]] = []
        cost_rows: list[dict[str, object]] = []
        funnel_rows: list[dict[str, object]] = []
        trace_rows: list[dict[str, object]] = []
        viability_by_key: dict[tuple[str, str], TrendFollowingOperationalAssessment] = {}
        selection_metric_by_key: dict[tuple[str, str], TrendFollowingSelectionMetric] = {}
        selections: list[TrendFollowingDevelopmentSelection] = []

        for group in groups:
            group_metrics: list[TrendFollowingSelectionMetric] = []
            for hypothesis in catalog.applicable_to(group):
                scenario_runs = self._run_cost_scenarios(
                    request=request,
                    group=group,
                    hypothesis=hypothesis,
                    period_data=development,
                    signal_daily=development.daily_for(group),
                    period="DEVELOPMENT",
                    evaluation_start=request.periods.development_start,
                    evaluation_end=request.periods.development_end,
                )
                base = scenario_runs["BASE"]
                key = (group.key, hypothesis.variant_id)
                development_runs[(group.key, hypothesis.variant_id, "BASE")] = base
                folds = self._run_folds(
                    request=request,
                    group=group,
                    hypothesis=hypothesis,
                    period_data=development,
                    signal_daily=development.daily_for(group),
                    period="DEVELOPMENT",
                    windows=_development_windows(request.periods),
                    costs=_cost_parameters(self._config, group, "BASE"),
                )
                summary = _summarize_folds(folds)
                development_summaries[key] = summary
                development_results.append(_run_row(base, summary))
                development_walk.extend(_walk_forward_rows(group, hypothesis, folds, summary))
                cost_rows.extend(_cost_rows(scenario_runs, development_hashes))
                funnel_rows.extend(
                    _funnel_rows(
                        base,
                        daily_count=_daily_count_inside(
                            development.daily_for(group),
                            request.periods.development_start,
                            request.periods.development_end,
                        ),
                    )
                )
                trace_rows.extend(asdict(trace) for trace in base.traces)
                operational = assess_operational_viability(
                    TrendFollowingOperationalMetrics(
                        market=group.market,
                        mode=group.mode,
                        variant_id=hypothesis.variant_id,
                        development_trade_count=len(base.trades),
                        fold_count=summary.fold_count,
                        folds_with_trades=summary.folds_with_trades,
                        exposure_percent=base.exposure_percent,
                    )
                )
                viability_by_key[key] = operational
                metric = TrendFollowingSelectionMetric(
                    market=group.market,
                    mode=group.mode,
                    variant_id=hypothesis.variant_id,
                    operational_status=operational.status,
                    median_walk_forward_net_return=summary.median_return_percent,
                    positive_fold_percent=summary.positive_fold_percent,
                    worst_drawdown_percent=summary.maximum_drawdown_percent,
                    top_three_concentration_percent=(base.top_three_concentration_percent),
                    development_trade_count=len(base.trades),
                    exposure_percent=base.exposure_percent,
                    complexity_rank=hypothesis.complexity_rank,
                    catalog_order=hypothesis.catalog_order,
                )
                selection_metric_by_key[key] = metric
                group_metrics.append(metric)
            selections.append(select_development_hypothesis(tuple(group_metrics)))
            development_results.extend(
                _benchmark_rows(
                    group=group,
                    data=development,
                    daily=development.daily_for(group),
                    period="DEVELOPMENT",
                    start=request.periods.development_start,
                    end=request.periods.development_end,
                    costs=_cost_parameters(self._config, group, "BASE"),
                )
            )

        locked: list[TrendFollowingLockedSelection] = []
        selection_by_group = {
            (selection.market, selection.mode): selection for selection in selections
        }
        for group in groups:
            selection = selection_by_group[(group.market, group.mode)]
            if selection.selected_variant_id is None:
                continue
            hypothesis = catalog.by_id(selection.selected_variant_id)
            locked.append(
                TrendFollowingLockedSelection(
                    group=group,
                    hypothesis=hypothesis,
                    development_metric=selection_metric_by_key[(group.key, hypothesis.variant_id)],
                )
            )

        selection_timestamp = datetime.now(tz=UTC)
        lock = TrendFollowingValidationLock.create(
            selections=tuple(locked),
            catalog=catalog,
            dataset_hashes=development_hashes,
            periods=request.periods,
            git_commit=git_commit,
            git_dirty=git_dirty,
            leverage=request.leverage,
            cost_parameters=_locked_cost_parameters(self._config),
            risk_model="FIXED_AND_DEFENSIVE_PRE_REGISTERED",
            selection_timestamp=selection_timestamp,
        )
        lock_payload = {
            **asdict(lock),
            "locked_before_validation": True,
            "validation_loaded_before_lock": False,
            "validation_executed_before_lock": False,
        }
        lock_bytes = _json_bytes(lock_payload)
        experiment_id = (
            "daily-trend-following-"
            f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{catalog.canonical_hash[:8]}"
        )
        output_path = request.output_dir / experiment_id
        output_path.mkdir(parents=True, exist_ok=False)
        lock_path = output_path / "trend_following_validation_lock.json"
        lock_path.write_bytes(lock_bytes)

        # Only now is the 2024 range queried and aggregated.
        validation_started_at = datetime.now(tz=UTC)
        if validation_started_at < selection_timestamp:
            raise RuntimeError("validation timestamp precedes development selection")
        validation = self._load_period(
            request,
            period="VALIDATION",
            start=request.periods.validation_start,
            end=request.periods.validation_end,
        )
        if lock_path.read_bytes() != lock_bytes:
            raise RuntimeError("validation lock changed before validation execution")
        lock.assert_valid()
        validation_hashes = _lock_dataset_hashes(validation)
        validation_results: list[dict[str, object]] = []
        validation_walk: list[dict[str, object]] = []
        validation_runs: dict[tuple[str, str, str], TrendFollowingRun] = {}
        validation_summaries: dict[tuple[str, str], _FoldSummary] = {}

        for group in groups:
            combined_daily = _combine_daily(
                development.daily_for(group),
                validation.daily_for(group),
            )
            selection = selection_by_group[(group.market, group.mode)]
            if selection.selected_variant_id is not None:
                hypothesis = catalog.by_id(selection.selected_variant_id)
                scenario_runs = self._run_cost_scenarios(
                    request=request,
                    group=group,
                    hypothesis=hypothesis,
                    period_data=validation,
                    signal_daily=combined_daily,
                    period="VALIDATION",
                    evaluation_start=request.periods.validation_start,
                    evaluation_end=request.periods.validation_end,
                )
                base = scenario_runs["BASE"]
                validation_runs[(group.key, hypothesis.variant_id, "BASE")] = base
                for scenario, run in scenario_runs.items():
                    validation_runs[(group.key, hypothesis.variant_id, scenario)] = run
                folds = self._run_folds(
                    request=request,
                    group=group,
                    hypothesis=hypothesis,
                    period_data=validation,
                    signal_daily=combined_daily,
                    period="VALIDATION",
                    windows=_validation_windows(request.periods),
                    costs=_cost_parameters(self._config, group, "BASE"),
                )
                summary = _summarize_folds(folds)
                validation_summaries[(group.key, hypothesis.variant_id)] = summary
                validation_results.append(_run_row(base, summary))
                validation_walk.extend(_walk_forward_rows(group, hypothesis, folds, summary))
                cost_rows.extend(_cost_rows(scenario_runs, validation_hashes))
                funnel_rows.extend(
                    _funnel_rows(
                        base,
                        daily_count=_daily_count_inside(
                            validation.daily_for(group),
                            request.periods.validation_start,
                            request.periods.validation_end,
                        ),
                    )
                )
                trace_rows.extend(asdict(trace) for trace in base.traces)
            validation_results.extend(
                _benchmark_rows(
                    group=group,
                    data=validation,
                    daily=validation.daily_for(group),
                    period="VALIDATION",
                    start=request.periods.validation_start,
                    end=request.periods.validation_end,
                    costs=_cost_parameters(self._config, group, "BASE"),
                )
            )
        if lock_path.read_bytes() != lock_bytes:
            raise RuntimeError("validation lock changed during validation execution")
        if self._catalog_path.read_bytes() != catalog_bytes:
            raise RuntimeError("trend-following catalog changed during experiment")

        base_runs = tuple(
            (
                *(
                    run
                    for (group_key, variant_id, scenario), run in development_runs.items()
                    if group_key and variant_id and scenario == "BASE"
                ),
                *(
                    run
                    for (group_key, variant_id, scenario), run in validation_runs.items()
                    if group_key and variant_id and scenario == "BASE"
                ),
            )
        )
        defensive_comparisons = _defensive_comparisons(
            groups,
            catalog,
            development_runs,
        )
        bootstrap_rows = _bootstrap_rows(base_runs)
        concentration_rows = tuple(_concentration_row(run) for run in base_runs)
        side_rows = tuple(row for run in base_runs for row in _side_rows(run))
        funding_rows = tuple(
            _funding_row(
                run,
                (development_hashes if run.period == "DEVELOPMENT" else validation_hashes),
            )
            for run in base_runs
            if run.market == "futures"
        )
        assessments = _assessments(
            groups=groups,
            catalog=catalog,
            selections=tuple(selections),
            viability_by_key=viability_by_key,
            development_runs=development_runs,
            development_summaries=development_summaries,
            validation_runs=validation_runs,
            validation_summaries=validation_summaries,
            bootstrap_rows=bootstrap_rows,
            lock_preserved=lock_path.read_bytes() == lock_bytes,
        )
        confirmation_plan = _confirmation_plan(assessments)
        completed_at = datetime.now(tz=UTC)
        aggregation_rows = (
            *_aggregation_rows(development),
            *_aggregation_rows(validation),
        )
        hash_rows = (
            *_daily_hash_rows(development),
            *_daily_hash_rows(validation),
        )
        manifest: dict[str, object] = {
            "sprint": "3C.1",
            "research_only": True,
            "symbol": request.symbol,
            "source_interval": request.source_interval,
            "strategy_interval": request.strategy_interval,
            "periods": request.periods,
            "warmup_daily_candles": 199,
            "development_train_days": 365,
            "development_validation_days": 90,
            "development_step_days": 90,
            "validation_windows": "CALENDAR_QUARTERS",
            "incomplete_day_policy": request.incomplete_day_policy,
            "markets": request.markets,
            "futures_modes": request.futures_modes,
            "leverage": request.leverage,
            "selection_timestamp": selection_timestamp,
            "lock_written_at": selection_timestamp,
            "validation_started_at": validation_started_at,
            "lock_persisted_before_validation": True,
            "validation_lock_preserved": True,
            "catalog_unchanged": True,
            "development_dataset_hashes": development_hashes,
            "validation_dataset_hashes": validation_hashes,
            "consumed_reference": {
                "start": request.periods.consumed_start,
                "end": request.periods.consumed_end,
                "loaded": False,
                "executed": False,
                "used_for_selection": False,
                "purpose": "EXCLUDED_REFERENCE_ONLY",
            },
            "network_used": False,
            "downloads_performed": False,
            "authenticated_api_used": False,
            "external_orders_sent": False,
            "paper_trading_enabled": False,
            "candidate_frozen": False,
            "profitability_claimed": False,
            "warnings": (
                "WARMUP_REDUCED_EVALUATION_PERIOD",
                "CONSUMED_2025_2026_EXCLUDED",
                "RESEARCH_ONLY_NO_ORDERS",
                "NO_CANDIDATE_FREEZE",
            ),
        }
        duration = Decimal(str(time.monotonic() - started_clock))
        return TrendFollowingExperimentBundle(
            experiment_id=experiment_id,
            output_path=output_path,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            request=request,
            catalog=catalog,
            aggregation_integrity=tuple(aggregation_rows),
            daily_dataset_hashes=tuple(hash_rows),
            decision_funnel=tuple(funnel_rows),
            decision_traces=tuple(trace_rows),
            development_results=tuple(development_results),
            development_walk_forward=tuple(development_walk),
            operational_viability=tuple(asdict(item) for item in viability_by_key.values()),
            development_selection=tuple(asdict(item) for item in selections),
            validation_lock=lock_payload,
            validation_lock_bytes=lock_bytes,
            validation_results=tuple(validation_results),
            validation_walk_forward=tuple(validation_walk),
            defensive_risk_comparison=tuple(asdict(item) for item in defensive_comparisons),
            cost_scenarios=tuple(cost_rows),
            funding_impact=funding_rows,
            side_contribution=side_rows,
            concentration_analysis=concentration_rows,
            bootstrap_uncertainty=bootstrap_rows,
            assessments=assessments,
            future_confirmation_plan=confirmation_plan,
            manifest=manifest,
        )

    def _load_period(
        self,
        request: TrendFollowingExperimentRequest,
        *,
        period: str,
        start: datetime,
        end: datetime,
    ) -> _PeriodData:
        if period == "DEVELOPMENT":
            request.periods.assert_development_range(start, end)
        elif period == "VALIDATION":
            request.periods.assert_validation_range(start, end)
        else:
            raise ValueError("period must be DEVELOPMENT or VALIDATION")
        spot_hourly: tuple[Candle, ...] = ()
        spot_aggregation: DailyAggregationResult[Candle] | None = None
        futures_hourly: tuple[FuturesCandle, ...] = ()
        futures_aggregation: DailyAggregationResult[FuturesCandle] | None = None
        marks: tuple[MarkPriceCandle, ...] = ()
        funding: tuple[FundingRate, ...] = ()
        if "spot" in request.markets:
            spot_hourly = self._repository.get_candles(
                request.symbol,
                request.source_interval,
                start_time=start,
                end_time=end,
            )
            _assert_loaded_boundaries(
                tuple(item.open_time for item in spot_hourly),
                start,
                end,
                "Spot",
            )
            spot_aggregation = self._aggregator.aggregate_spot(spot_hourly)
        if "futures" in request.markets:
            futures_hourly = self._repository.get_futures_candles(
                request.symbol,
                request.source_interval,
                start_time=start,
                end_time=end,
            )
            marks = self._repository.get_mark_prices(
                request.symbol,
                request.source_interval,
                start_time=start,
                end_time=end,
            )
            funding = self._repository.get_funding_rates(
                request.symbol,
                start_time=start,
                end_time=end,
            )
            _assert_loaded_boundaries(
                tuple(item.open_time for item in futures_hourly),
                start,
                end,
                "Futures",
            )
            _assert_loaded_boundaries(
                tuple(item.open_time for item in marks),
                start,
                end,
                "mark price",
            )
            if any(item.funding_time < start or item.funding_time > end for item in funding):
                raise ValueError("funding query escaped the requested period")
            futures_aggregation = self._aggregator.aggregate_futures(futures_hourly)
        return _PeriodData(
            period=period,
            spot_hourly=spot_hourly,
            spot_aggregation=spot_aggregation,
            futures_hourly=futures_hourly,
            futures_aggregation=futures_aggregation,
            marks=marks,
            funding=funding,
        )

    def _run_cost_scenarios(
        self,
        *,
        request: TrendFollowingExperimentRequest,
        group: TrendFollowingMarketGroup,
        hypothesis: TrendFollowingHypothesis,
        period_data: _PeriodData,
        signal_daily: tuple[Candle, ...],
        period: str,
        evaluation_start: datetime,
        evaluation_end: datetime,
    ) -> dict[str, TrendFollowingRun]:
        return {
            scenario: self._run_one(
                request=request,
                group=group,
                hypothesis=hypothesis,
                period_data=period_data,
                signal_daily=signal_daily,
                period=period,
                evaluation_start=evaluation_start,
                evaluation_end=evaluation_end,
                costs=_cost_parameters(self._config, group, scenario),
            )
            for scenario in ("LOW", "BASE", "HIGH", "STRESS")
        }

    def _run_folds(
        self,
        *,
        request: TrendFollowingExperimentRequest,
        group: TrendFollowingMarketGroup,
        hypothesis: TrendFollowingHypothesis,
        period_data: _PeriodData,
        signal_daily: tuple[Candle, ...],
        period: str,
        windows: tuple[_FoldWindow, ...],
        costs: _CostParameters,
    ) -> tuple[tuple[_FoldWindow, TrendFollowingRun], ...]:
        return tuple(
            (
                window,
                self._run_one(
                    request=request,
                    group=group,
                    hypothesis=hypothesis,
                    period_data=period_data,
                    signal_daily=signal_daily,
                    period=period,
                    evaluation_start=window.evaluation_start,
                    evaluation_end=window.evaluation_end,
                    costs=costs,
                ),
            )
            for window in windows
        )

    def _run_one(
        self,
        *,
        request: TrendFollowingExperimentRequest,
        group: TrendFollowingMarketGroup,
        hypothesis: TrendFollowingHypothesis,
        period_data: _PeriodData,
        signal_daily: tuple[Candle, ...],
        period: str,
        evaluation_start: datetime,
        evaluation_end: datetime,
        costs: _CostParameters,
    ) -> TrendFollowingRun:
        market = group.market.lower()
        mode = group.mode.lower().replace("_", "-")
        maximum_position = (
            self._config.maximum_position_percent if market == "spot" else Decimal("25")
        )
        engine_config = TrendFollowingEngineConfig(
            market=market,
            mode=mode,
            variant_id=hypothesis.variant_id,
            period=period,
            scenario=costs.scenario,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            exit_period=hypothesis.exit_period_days,
            defensive_risk=hypothesis.defensive_risk_enabled,
            initial_capital=self._config.initial_balance,
            normal_risk_percent=hypothesis.normal_risk_percent,
            defensive_risk_percent=hypothesis.defensive_risk_percent,
            activation_losses=hypothesis.defensive_activation_losses,
            maximum_position_percent=maximum_position,
            leverage=request.leverage,
            fee_bps=costs.fee_bps,
            spread_bps=costs.spread_bps,
            slippage_bps=costs.slippage_bps,
        )
        if market == "spot":
            return self._engine.run(
                config=engine_config,
                daily_candles=signal_daily,
                hourly_candles=period_data.spot_hourly,
            )
        return self._engine.run(
            config=engine_config,
            daily_candles=signal_daily,
            hourly_candles=period_data.futures_hourly,
            marks=period_data.marks,
            funding=period_data.funding,
        )


def _cost_parameters(
    config: TradingConfig,
    group: TrendFollowingMarketGroup,
    scenario: str,
) -> _CostParameters:
    if scenario not in {"LOW", "BASE", "HIGH", "STRESS"}:
        raise ValueError("unsupported cost scenario")
    base_fee = config.taker_fee_bps if group.market == "SPOT" else Decimal("5")
    base_spread = config.spread_bps if group.market == "SPOT" else Decimal("2")
    base_slippage = config.slippage_bps if group.market == "SPOT" else Decimal("5")
    multiplier = {
        "LOW": Decimal("0.5"),
        "BASE": Decimal("1"),
        "HIGH": Decimal("2"),
        "STRESS": Decimal("4"),
    }[scenario]
    if scenario == "LOW":
        fee = max(Decimal("1"), base_fee * multiplier)
        spread = max(Decimal("1"), base_spread * multiplier)
        slippage = max(Decimal("1"), base_slippage * multiplier)
    else:
        fee = base_fee * multiplier
        spread = base_spread * multiplier
        slippage = base_slippage * multiplier
    return _CostParameters(scenario, fee, spread, slippage)


def _locked_cost_parameters(config: TradingConfig) -> dict[str, object]:
    return {
        "spot_base_fee_bps": config.taker_fee_bps,
        "spot_base_spread_bps": config.spread_bps,
        "spot_base_slippage_bps": config.slippage_bps,
        "futures_base_fee_bps": Decimal("5"),
        "futures_base_spread_bps": Decimal("2"),
        "futures_base_slippage_bps": Decimal("5"),
        "low_multiplier": Decimal("0.5"),
        "base_multiplier": Decimal("1"),
        "high_multiplier": Decimal("2"),
        "stress_multiplier": Decimal("4"),
        "funding_source": "HISTORICAL_UNCHANGED",
    }


def _development_windows(
    periods: TrendFollowingPeriods,
) -> tuple[_FoldWindow, ...]:
    cursor = periods.development_start + timedelta(days=365)
    final_exclusive = periods.development_end + HOUR
    windows: list[_FoldWindow] = []
    fold = 1
    while cursor + timedelta(days=90) <= final_exclusive:
        windows.append(
            _FoldWindow(
                fold=fold,
                train_start=cursor - timedelta(days=365),
                train_end=cursor - HOUR,
                evaluation_start=cursor,
                evaluation_end=cursor + timedelta(days=90) - HOUR,
            )
        )
        cursor += timedelta(days=90)
        fold += 1
    if not windows:
        raise ValueError("no complete development walk-forward folds")
    return tuple(windows)


def _validation_windows(
    periods: TrendFollowingPeriods,
) -> tuple[_FoldWindow, ...]:
    starts = (
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 4, 1, tzinfo=UTC),
        datetime(2024, 7, 1, tzinfo=UTC),
        datetime(2024, 10, 1, tzinfo=UTC),
    )
    ends = (
        datetime(2024, 3, 31, 23, tzinfo=UTC),
        datetime(2024, 6, 30, 23, tzinfo=UTC),
        datetime(2024, 9, 30, 23, tzinfo=UTC),
        datetime(2024, 12, 31, 23, tzinfo=UTC),
    )
    windows = tuple(
        _FoldWindow(
            fold=index,
            train_start=periods.development_start,
            train_end=periods.development_end,
            evaluation_start=start,
            evaluation_end=end,
        )
        for index, (start, end) in enumerate(
            zip(starts, ends, strict=True),
            start=1,
        )
    )
    if (
        windows[0].evaluation_start != periods.validation_start
        or windows[-1].evaluation_end != periods.validation_end
    ):
        raise ValueError("validation quarters differ from pre-registration")
    return windows


def _summarize_folds(
    folds: tuple[tuple[_FoldWindow, TrendFollowingRun], ...],
) -> _FoldSummary:
    if not folds:
        raise ValueError("walk-forward summary requires folds")
    returns = tuple(run.net_return_percent for _, run in folds)
    folds_with_trades = sum(bool(run.trades) for _, run in folds)
    count = len(folds)
    return _FoldSummary(
        fold_count=count,
        folds_with_trades=folds_with_trades,
        zero_trade_fold_percent=(Decimal(count - folds_with_trades) / Decimal(count) * HUNDRED),
        median_return_percent=_median(returns),
        mean_return_percent=sum(returns, ZERO) / Decimal(count),
        positive_fold_percent=(
            Decimal(sum(value > ZERO for value in returns)) / Decimal(count) * HUNDRED
        ),
        worst_return_percent=min(returns),
        maximum_drawdown_percent=max(run.maximum_drawdown_percent for _, run in folds),
        trades=sum(len(run.trades) for _, run in folds),
    )


def _walk_forward_rows(
    group: TrendFollowingMarketGroup,
    hypothesis: TrendFollowingHypothesis,
    folds: tuple[tuple[_FoldWindow, TrendFollowingRun], ...],
    summary: _FoldSummary,
) -> tuple[dict[str, object], ...]:
    rows = [
        {
            "market": group.market,
            "mode": group.mode,
            "variant_id": hypothesis.variant_id,
            "period": run.period,
            "scenario": run.scenario,
            "fold": window.fold,
            "train_start": window.train_start,
            "train_end": window.train_end,
            "validation_start": window.evaluation_start,
            "validation_end": window.evaluation_end,
            "effective_evaluation_start": run.effective_evaluation_start,
            "net_return_percent": run.net_return_percent,
            "maximum_drawdown_percent": run.maximum_drawdown_percent,
            "trade_count": len(run.trades),
            "warnings": run.warnings,
        }
        for window, run in folds
    ]
    rows.append(
        {
            "market": group.market,
            "mode": group.mode,
            "variant_id": hypothesis.variant_id,
            "period": folds[0][1].period,
            "scenario": folds[0][1].scenario,
            "fold": "CONSOLIDATED",
            "train_start": None,
            "train_end": None,
            "validation_start": None,
            "validation_end": None,
            "effective_evaluation_start": None,
            "net_return_percent": summary.mean_return_percent,
            "median_walk_forward_net_return": summary.median_return_percent,
            "positive_fold_percent": summary.positive_fold_percent,
            "zero_trade_fold_percent": summary.zero_trade_fold_percent,
            "maximum_drawdown_percent": summary.maximum_drawdown_percent,
            "trade_count": summary.trades,
            "warnings": (),
        }
    )
    return tuple(rows)


def _run_row(
    run: TrendFollowingRun,
    summary: _FoldSummary,
) -> dict[str, object]:
    trade_years: dict[str, int] = {}
    for trade in run.trades:
        key = str(trade.exit_time.year)
        trade_years[key] = trade_years.get(key, 0) + 1
    return {
        "market": run.market.upper(),
        "mode": run.mode.upper().replace("-", "_"),
        "variant_id": run.variant_id,
        "benchmark": False,
        "selectable": run.period == "DEVELOPMENT",
        "period": run.period,
        "scenario": run.scenario,
        "evaluation_start": run.evaluation_start,
        "evaluation_end": run.evaluation_end,
        "effective_evaluation_start": run.effective_evaluation_start,
        "initial_capital": run.initial_capital,
        "final_capital": run.final_capital,
        "trades": len(run.trades),
        "trades_by_year": trade_years,
        "long_trades": run.long_trades,
        "short_trades": run.short_trades,
        "net_return_percent": run.net_return_percent,
        "gross_return_percent": run.gross_return_percent,
        "win_rate_percent": run.win_rate_percent,
        "profit_factor": run.profit_factor,
        "expectancy": run.expectancy,
        "median_trade": run.median_trade_pnl,
        "maximum_drawdown_percent": run.maximum_drawdown_percent,
        "return_to_drawdown": run.return_to_drawdown,
        "exposure_percent": run.exposure_percent,
        "fees": run.fees,
        "execution_costs": run.execution_costs,
        "funding_paid": run.funding_paid,
        "funding_received": run.funding_received,
        "net_funding": run.net_funding,
        "liquidation_count": run.liquidation_count,
        "evaluated_daily_candles": run.evaluated_daily_candles,
        "entry_signals": run.entry_signals,
        "risk_approvals": run.risk_approvals,
        "entry_executions": run.executions,
        "defensive_mode_activations": run.defensive_mode_activations,
        "candles_in_defensive_mode": run.candles_in_defensive_mode,
        "trades_in_defensive_mode": run.trades_in_defensive_mode,
        "risk_reduction_duration_days": run.risk_reduction_duration_days,
        "result_without_best_trade": run.net_pnl_without_best_trade,
        "result_without_top_three": run.net_pnl_without_top_three,
        "best_trade_concentration_percent": (run.best_trade_concentration_percent),
        "top_three_concentration_percent": (run.top_three_concentration_percent),
        "zero_trade_fold_percent": summary.zero_trade_fold_percent,
        "median_walk_forward_net_return": summary.median_return_percent,
        "positive_fold_percent": summary.positive_fold_percent,
        "warnings": run.warnings,
    }


def _cost_rows(
    runs: dict[str, TrendFollowingRun],
    dataset_hashes: dict[str, str],
) -> tuple[dict[str, object], ...]:
    low = runs["LOW"]
    base = runs["BASE"]
    stress = runs["STRESS"]
    warnings: list[str] = []
    if low.net_return_percent > ZERO and base.net_return_percent <= ZERO:
        warnings.append("LOW_COST_ONLY_EDGE")
    if base.net_return_percent > ZERO and stress.net_return_percent < ZERO:
        warnings.append("STRESS_COLLAPSE")
    if base.fees + base.execution_costs > abs(base.gross_pnl):
        warnings.append("COST_DOMINATED")
    if abs(base.net_funding) > abs(base.net_pnl) and base.net_funding != ZERO:
        warnings.append("FUNDING_DOMINATED_RESULT")
    return tuple(
        {
            "market": run.market.upper(),
            "mode": run.mode.upper().replace("-", "_"),
            "variant_id": run.variant_id,
            "period": run.period,
            "scenario": scenario,
            "net_return_percent": run.net_return_percent,
            "gross_return_percent": run.gross_return_percent,
            "maximum_drawdown_percent": run.maximum_drawdown_percent,
            "trade_count": len(run.trades),
            "fees": run.fees,
            "execution_costs": run.execution_costs,
            "net_funding": run.net_funding,
            "funding_source_unchanged": (True if run.market == "futures" else None),
            "funding_dataset_hash": (
                dataset_hashes.get("futures_funding") if run.market == "futures" else None
            ),
            "warnings": tuple(warnings),
        }
        for scenario, run in runs.items()
    )


def _funnel_rows(
    run: TrendFollowingRun,
    *,
    daily_count: int,
) -> tuple[dict[str, object], ...]:
    macro_pass = sum(
        (
            trace.macro_side == "ABOVE"
            if run.mode in {"long", "long-short"}
            else trace.macro_side == "BELOW"
        )
        for trace in run.traces
    )
    closed = len(run.trades)
    structural_exits = sum(
        trade.exit_reason in {"MACRO_FILTER_EXIT", "DONCHIAN_EXIT_10", "DONCHIAN_EXIT_20"}
        for trade in run.trades
    )
    stages = (
        ("daily_candles", daily_count),
        ("warmup_complete", run.evaluated_daily_candles),
        ("sma_macro_filter", min(run.evaluated_daily_candles, macro_pass)),
        ("donchian_breakout", run.entry_signals),
        ("signal", run.entry_signals),
        ("risk_sizing", run.entry_signals),
        ("risk_approved", run.risk_approvals),
        ("execution", run.executions),
        ("position", run.executions),
        ("exit_condition", min(run.executions, structural_exits)),
        ("closed_trade", min(run.executions, closed)),
    )
    return tuple(
        {
            "market": run.market.upper(),
            "mode": run.mode.upper().replace("-", "_"),
            "variant_id": run.variant_id,
            "period": run.period,
            "scenario": run.scenario,
            "stage_order": index,
            "stage": stage,
            "count": count,
        }
        for index, (stage, count) in enumerate(stages, start=1)
    )


def _benchmark_rows(
    *,
    group: TrendFollowingMarketGroup,
    data: _PeriodData,
    daily: tuple[Candle, ...],
    period: str,
    start: datetime,
    end: datetime,
    costs: _CostParameters,
) -> tuple[dict[str, object], ...]:
    in_period = tuple(
        candle for candle in daily if start.date() <= candle.open_time.date() <= end.date()
    )
    if not in_period:
        raise ValueError("benchmark requires daily candles inside period")
    first = in_period[0].open
    last = in_period[-1].close
    rows = [_benchmark_row(group, period, "CASH", ZERO, ZERO, 0)]
    round_trip_cost = (
        costs.fee_bps * Decimal("2") + (costs.spread_bps + costs.slippage_bps) * Decimal("2")
    ) / Decimal("100")
    if group.market == "SPOT":
        gross = (last - first) / first * HUNDRED
        rows.append(
            _benchmark_row(
                group,
                period,
                "SPOT_BUY_AND_HOLD",
                gross,
                gross - round_trip_cost,
                0,
            )
        )
        return tuple(rows)
    funding_percent = (
        sum(
            (item.funding_rate for item in data.funding if start <= item.funding_time <= end),
            ZERO,
        )
        * HUNDRED
    )
    for side, name in (
        (PositionSide.LONG, "FUTURES_LONG_1X"),
        (PositionSide.SHORT, "FUTURES_SHORT_1X"),
    ):
        gross = (
            (last - first) / first * HUNDRED
            if side is PositionSide.LONG
            else (first - last) / first * HUNDRED
        )
        funding_effect = -funding_percent if side is PositionSide.LONG else funding_percent
        liquidation_price = approximate_liquidation_price(
            side,
            first,
            Decimal("1"),
            Decimal("0.005"),
        )
        liquidated = any(
            (
                mark.low <= liquidation_price
                if side is PositionSide.LONG
                else mark.high >= liquidation_price
            )
            for mark in data.marks
            if start <= mark.open_time <= end
        )
        net = Decimal("-100") if liquidated else gross + funding_effect - round_trip_cost
        rows.append(
            _benchmark_row(
                group,
                period,
                name,
                gross,
                net,
                int(liquidated),
                funding=funding_effect,
            )
        )
    return tuple(rows)


def _benchmark_row(
    group: TrendFollowingMarketGroup,
    period: str,
    name: str,
    gross: Decimal,
    net: Decimal,
    liquidations: int,
    *,
    funding: Decimal = ZERO,
) -> dict[str, object]:
    return {
        "market": group.market,
        "mode": group.mode,
        "variant_id": name,
        "benchmark": True,
        "selectable": False,
        "period": period,
        "scenario": CostScenario.BASE,
        "trades": 0 if name == "CASH" else 1,
        "long_trades": int("LONG" in name or "BUY_AND_HOLD" in name),
        "short_trades": int("SHORT" in name),
        "net_return_percent": net,
        "gross_return_percent": gross,
        "maximum_drawdown_percent": ZERO,
        "exposure_percent": ZERO if name == "CASH" else HUNDRED,
        "net_funding_percent": funding,
        "liquidation_count": liquidations,
    }


def _defensive_comparisons(
    groups: tuple[TrendFollowingMarketGroup, ...],
    catalog: TrendFollowingCatalog,
    runs: dict[tuple[str, str, str], TrendFollowingRun],
) -> tuple[DefensiveRiskComparison, ...]:
    pairs = (
        ("TF_DONCHIAN_20_FIXED_RISK", "TF_DONCHIAN_20_DEFENSIVE_RISK"),
        ("TF_DONCHIAN_10_FIXED_RISK", "TF_DONCHIAN_10_DEFENSIVE_RISK"),
    )
    rows: list[DefensiveRiskComparison] = []
    for group in groups:
        for fixed_id, defensive_id in pairs:
            fixed = runs[(group.key, fixed_id, "BASE")]
            defensive = runs[(group.key, defensive_id, "BASE")]
            rows.append(
                compare_defensive_risk(
                    _risk_profile(
                        fixed,
                        catalog.by_id(fixed_id).risk_model,
                        catalog.by_id(fixed_id).exit_period_days,
                    ),
                    _risk_profile(
                        defensive,
                        catalog.by_id(defensive_id).risk_model,
                        catalog.by_id(defensive_id).exit_period_days,
                    ),
                )
            )
    return tuple(rows)


def _risk_profile(
    run: TrendFollowingRun,
    risk_model: TrendFollowingRiskModel,
    exit_period: int,
) -> RiskProfileMetrics:
    returns = tuple(
        trade.net_pnl / trade.equity_before * HUNDRED if trade.equity_before > ZERO else ZERO
        for trade in run.trades
    )
    volatility = Decimal(str(pstdev([float(value) for value in returns]))) if returns else ZERO
    maximum_loss = min(
        HUNDRED,
        max((-value for value in returns if value < ZERO), default=ZERO),
    )
    return RiskProfileMetrics(
        market=run.market.upper(),
        mode=run.mode.upper().replace("-", "_"),
        period=run.period,
        variant_id=run.variant_id,
        exit_period_days=exit_period,
        risk_model=risk_model,
        trade_count=len(run.trades),
        net_return_percent=run.net_return_percent,
        maximum_drawdown_percent=min(HUNDRED, run.maximum_drawdown_percent),
        volatility_percent=min(HUNDRED, volatility),
        maximum_loss_percent=maximum_loss,
        recovery_duration_days=Decimal(run.risk_reduction_duration_days),
        defensive_activations=run.defensive_mode_activations,
        defensive_period_percent=(
            Decimal(run.candles_in_defensive_mode) / Decimal(run.evaluated_daily_candles) * HUNDRED
            if run.evaluated_daily_candles
            else ZERO
        ),
        half_risk_trade_count=run.trades_in_defensive_mode,
    )


def _bootstrap_rows(
    runs: tuple[TrendFollowingRun, ...],
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for run in runs:
        result = bootstrap_trade_pnls(tuple(trade.net_pnl for trade in run.trades))
        rows.append(
            {
                "market": run.market.upper(),
                "mode": run.mode.upper().replace("-", "_"),
                "variant_id": run.variant_id,
                "period": run.period,
                **asdict(result),
            }
        )
    return tuple(rows)


def _concentration_row(run: TrendFollowingRun) -> dict[str, object]:
    return {
        "market": run.market.upper(),
        "mode": run.mode.upper().replace("-", "_"),
        "variant_id": run.variant_id,
        "period": run.period,
        "best_trade_concentration_percent": (run.best_trade_concentration_percent),
        "top_three_concentration_percent": (run.top_three_concentration_percent),
        "net_pnl_without_best_trade": run.net_pnl_without_best_trade,
        "net_pnl_without_top_three": run.net_pnl_without_top_three,
    }


def _side_rows(run: TrendFollowingRun) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for side in ("LONG", "SHORT"):
        trades = tuple(trade for trade in run.trades if trade.side == side)
        if side == "SHORT" and run.market == "spot":
            continue
        rows.append(
            {
                "market": run.market.upper(),
                "mode": run.mode.upper().replace("-", "_"),
                "variant_id": run.variant_id,
                "period": run.period,
                "side": side,
                "trade_count": len(trades),
                "gross_pnl": sum(
                    (trade.gross_pnl for trade in trades),
                    ZERO,
                ),
                "net_pnl": sum((trade.net_pnl for trade in trades), ZERO),
                "win_rate_percent": (
                    Decimal(sum(trade.net_pnl > ZERO for trade in trades))
                    / Decimal(len(trades))
                    * HUNDRED
                    if trades
                    else ZERO
                ),
                "stable_contribution_claimed": False,
            }
        )
    return tuple(rows)


def _funding_row(
    run: TrendFollowingRun,
    dataset_hashes: dict[str, str],
) -> dict[str, object]:
    without_funding = (run.net_pnl - run.net_funding) / run.initial_capital * HUNDRED
    return {
        "market": "FUTURES",
        "mode": run.mode.upper().replace("-", "_"),
        "variant_id": run.variant_id,
        "period": run.period,
        "scenario": run.scenario,
        "funding_dataset_hash": dataset_hashes.get("futures_funding"),
        "funding_paid": run.funding_paid,
        "funding_received": run.funding_received,
        "net_funding": run.net_funding,
        "net_return_with_funding_percent": run.net_return_percent,
        "net_return_without_funding_percent": without_funding,
        "funding_return_impact_percent": (run.net_return_percent - without_funding),
        "funding_dominated_result": (
            abs(run.net_funding) > abs(run.net_pnl) and run.net_funding != ZERO
        ),
    }


def _assessments(
    *,
    groups: tuple[TrendFollowingMarketGroup, ...],
    catalog: TrendFollowingCatalog,
    selections: tuple[TrendFollowingDevelopmentSelection, ...],
    viability_by_key: dict[tuple[str, str], TrendFollowingOperationalAssessment],
    development_runs: dict[tuple[str, str, str], TrendFollowingRun],
    development_summaries: dict[tuple[str, str], _FoldSummary],
    validation_runs: dict[tuple[str, str, str], TrendFollowingRun],
    validation_summaries: dict[tuple[str, str], _FoldSummary],
    bootstrap_rows: tuple[dict[str, object], ...],
    lock_preserved: bool,
) -> tuple[dict[str, object], ...]:
    selection_by_group = {(item.market, item.mode): item for item in selections}
    bootstrap_by_key = {
        (str(row["market"]), str(row["mode"]), str(row["variant_id"]), str(row["period"])): row
        for row in bootstrap_rows
    }
    rows: list[dict[str, object]] = []
    for group in groups:
        selection = selection_by_group[(group.market, group.mode)]
        for hypothesis in catalog.applicable_to(group):
            key = (group.key, hypothesis.variant_id)
            operational = viability_by_key[key]
            development = development_runs[(group.key, hypothesis.variant_id, "BASE")]
            dev_summary = development_summaries[key]
            selected = selection.selected_variant_id == hypothesis.variant_id
            criteria: list[tuple[str, bool]] = [
                ("operational_viability", operational.viable),
                ("development_trades_at_least_8", len(development.trades) >= 8),
                (
                    "development_median_wf_non_negative",
                    dev_summary.median_return_percent >= ZERO,
                ),
                (
                    "development_positive_folds_at_least_50_percent",
                    dev_summary.positive_fold_percent >= Decimal("50"),
                ),
                ("selected_from_development_only", selected),
            ]
            if not selected:
                if operational.status is TrendFollowingOperationalStatus.TOO_RESTRICTIVE:
                    classification = "TOO_RESTRICTIVE"
                elif operational.status is TrendFollowingOperationalStatus.INSUFFICIENT_SAMPLE:
                    classification = "INSUFFICIENT_SAMPLE"
                elif operational.viable and (
                    dev_summary.median_return_percent >= ZERO
                    and dev_summary.positive_fold_percent >= Decimal("50")
                ):
                    classification = "OPERATIONALLY_VIABLE_BUT_UNPROVEN"
                else:
                    classification = "NOT_PROMISING"
            else:
                validation = validation_runs[(group.key, hypothesis.variant_id, "BASE")]
                stress = validation_runs[(group.key, hypothesis.variant_id, "STRESS")]
                val_summary = validation_summaries[key]
                bootstrap = bootstrap_by_key[
                    (
                        group.market,
                        group.mode,
                        hypothesis.variant_id,
                        "VALIDATION",
                    )
                ]
                criteria.extend(
                    (
                        ("validation_trades_at_least_4", len(validation.trades) >= 4),
                        (
                            "validation_median_wf_non_negative",
                            val_summary.median_return_percent >= ZERO,
                        ),
                        (
                            "validation_positive_folds_at_least_50_percent",
                            val_summary.positive_fold_percent >= Decimal("50"),
                        ),
                        (
                            "validation_net_return_non_negative",
                            validation.net_return_percent >= ZERO,
                        ),
                        (
                            "maximum_drawdown_at_most_15_percent",
                            validation.maximum_drawdown_percent <= Decimal("15"),
                        ),
                        (
                            "zero_trade_folds_at_most_50_percent",
                            val_summary.zero_trade_fold_percent <= Decimal("50"),
                        ),
                        (
                            "stress_not_completely_destructive",
                            stress.final_capital > ZERO
                            and stress.net_return_percent > Decimal("-50"),
                        ),
                        (
                            "best_trade_concentration_at_most_50_percent",
                            validation.best_trade_concentration_percent <= Decimal("50"),
                        ),
                        (
                            "without_top_three_not_strongly_negative",
                            validation.net_pnl_without_top_three
                            >= -validation.initial_capital * Decimal("0.05"),
                        ),
                        (
                            "bootstrap_not_strongly_negative",
                            bootstrap["status"] != BootstrapStatus.NEGATIVE_UNCERTAIN,
                        ),
                        (
                            "no_futures_liquidation_at_1x",
                            validation.liquidation_count == 0,
                        ),
                        ("consumed_period_not_used", True),
                        ("validation_lock_preserved", lock_preserved),
                    )
                )
                if all(value for _, value in criteria):
                    classification = "PROMISING_FOR_CONFIRMATION"
                elif len(validation.trades) < 4:
                    classification = "OPERATIONALLY_VIABLE_BUT_UNPROVEN"
                else:
                    classification = "NOT_PROMISING"
            rows.append(
                {
                    "market": group.market,
                    "mode": group.mode,
                    "variant_id": hypothesis.variant_id,
                    "classification": classification,
                    "selected_for_validation": selected,
                    "criteria": tuple(criteria),
                    "failures": tuple(name for name, passed in criteria if not passed),
                    "candidate_frozen": False,
                    "production_enabled": False,
                    "profitability_claimed": False,
                }
            )
        if selection.selected_variant_id is None:
            rows.append(
                {
                    "market": group.market,
                    "mode": group.mode,
                    "variant_id": None,
                    "classification": "NO_DEVELOPMENT_HYPOTHESIS",
                    "selected_for_validation": False,
                    "criteria": (),
                    "failures": ("NO_DEVELOPMENT_HYPOTHESIS",),
                    "candidate_frozen": False,
                    "production_enabled": False,
                    "profitability_claimed": False,
                }
            )
    return tuple(rows)


def _confirmation_plan(
    assessments: tuple[dict[str, object], ...],
) -> dict[str, object]:
    promising = tuple(
        {
            "market": item["market"],
            "mode": item["mode"],
            "variant_id": item["variant_id"],
        }
        for item in assessments
        if item["classification"] == "PROMISING_FOR_CONFIRMATION"
    )
    if not promising:
        return {
            "status": "NO_CONFIRMATION_PLAN",
            "configurations": (),
            "executed": False,
            "paper_trading_enabled": False,
        }
    return {
        "status": "PLAN_ONLY_NOT_EXECUTED",
        "configurations": promising,
        "earliest_start": "after 2026-07-01T00:00:00Z",
        "minimum_days": 180,
        "minimum_closed_trades": 10,
        "configuration_immutable": True,
        "adjustments_during_confirmation": False,
        "parameter_change_requires_new_version": True,
        "executed": False,
        "paper_trading_enabled": False,
    }


def _aggregation_rows(data: _PeriodData) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for market, result in (
        ("SPOT", data.spot_aggregation),
        ("FUTURES", data.futures_aggregation),
    ):
        if result is None:
            continue
        rows.append(
            {
                "period": data.period,
                "market": market,
                "policy": IncompleteDayPolicy.WARN_AND_EXCLUDE,
                **asdict(result.integrity),
                "audits": tuple(asdict(item) for item in result.audits),
            }
        )
    return tuple(rows)


def _daily_hash_rows(data: _PeriodData) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for market, result in (
        ("SPOT", data.spot_aggregation),
        ("FUTURES", data.futures_aggregation),
    ):
        if result is None:
            continue
        rows.append(
            {
                "period": data.period,
                "market": market,
                "source_hourly_hash": result.source_hourly_hash,
                "aggregation_config_hash": result.aggregation_config_hash,
                "daily_rows_hash": result.daily_rows_hash,
                "daily_candle_hash": result.daily_candle_hash,
                "hourly_candle_count": result.integrity.source_candle_count,
                "daily_candle_count": result.integrity.output_candle_count,
            }
        )
    return tuple(rows)


def _lock_dataset_hashes(data: _PeriodData) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if data.spot_aggregation is not None:
        hashes["spot_hourly"] = data.spot_aggregation.source_hourly_hash
        hashes["spot_daily"] = data.spot_aggregation.daily_candle_hash
    if data.futures_aggregation is not None:
        hashes["futures_hourly"] = data.futures_aggregation.source_hourly_hash
        hashes["futures_daily"] = data.futures_aggregation.daily_candle_hash
        hashes["futures_mark"] = mark_price_content_hash(data.marks)
        hashes["futures_funding"] = funding_content_hash(data.funding)
    if not hashes:
        raise ValueError("no trend-following datasets were loaded")
    return hashes


def _combine_daily(
    development: tuple[Candle, ...],
    validation: tuple[Candle, ...],
) -> tuple[Candle, ...]:
    combined = (*development, *validation)
    if any(
        left.open_time >= right.open_time
        for left, right in zip(combined, combined[1:], strict=False)
    ):
        raise ValueError("development and validation daily data overlap")
    return combined


def _assert_loaded_boundaries(
    timestamps: tuple[datetime, ...],
    start: datetime,
    end: datetime,
    label: str,
) -> None:
    if not timestamps:
        raise ValueError(f"{label} dataset is empty")
    if timestamps[0] < start or timestamps[-1] > end:
        raise ValueError(f"{label} query escaped the requested range")
    if any(timestamp.year >= 2025 for timestamp in timestamps):
        raise ValueError(f"{label} loaded forbidden 2025 or 2026 data")


def _daily_count_inside(
    candles: tuple[Candle, ...],
    start: datetime,
    end: datetime,
) -> int:
    return sum(start.date() <= item.open_time.date() <= end.date() for item in candles)


def _median(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        return ZERO
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            _jsonable(value),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    raise TypeError(f"unsupported experiment value: {type(value).__name__}")
