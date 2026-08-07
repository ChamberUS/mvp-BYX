"""Offline orchestration for Sprint 3B.2 pullback frequency calibration."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import Candle
from adaptive_trader.futures.datasets import FuturesDataset
from adaptive_trader.futures.models import FuturesBacktestConfig
from adaptive_trader.research.pullback_analysis import (
    BootstrapResult,
    PullbackFold,
    PullbackRun,
    bootstrap_trades,
    concentration_metrics,
    summarize_folds,
)
from adaptive_trader.research.pullback_calibration import (
    OperationalFrequency,
    OperationalStatus,
    logic_audit_rows,
    operational_frequency,
    rejected_resumption_rows,
    select_by_frequency,
    sequential_funnel_rows,
    single_rule_ablation_rows,
)
from adaptive_trader.research.pullback_calibration_catalog import (
    CALIBRATION_CATALOG_FILE,
    PullbackCalibrationCatalog,
    load_pullback_calibration_catalog,
)
from adaptive_trader.research.pullback_catalog import (
    PullbackHypothesis,
    PullbackValidationLock,
    load_pullback_catalog,
)
from adaptive_trader.research.pullback_experiment import (
    PullbackExperimentRequest,
    PullbackExperimentService,
    PullbackFoldWindow,
    _futures_cost_configs,
    _futures_segment,
    _futures_variant_config,
    _market_groups,
    _period_range,
    _PriceBar,
    _result_row,
    _run_futures,
    _run_spot,
    _spot_cost_configs,
    _spot_segment,
)
from adaptive_trader.storage.sqlite import DatabaseRepository


@dataclass(frozen=True, slots=True)
class PullbackCalibrationBundle:
    experiment_id: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: Decimal
    request: PullbackExperimentRequest
    catalog: PullbackCalibrationCatalog
    dataset_manifest: dict[str, object]
    logic_audit: tuple[dict[str, object], ...]
    sequential_funnel: tuple[dict[str, object], ...]
    rejected_resumptions: tuple[dict[str, object], ...]
    single_rule_ablation: tuple[dict[str, object], ...]
    operational_frequency_results: tuple[OperationalFrequency, ...]
    selection_decisions: tuple[dict[str, object], ...]
    development_financial_results: tuple[dict[str, object], ...]
    validation_locks: tuple[dict[str, object], ...]
    validation_results: tuple[dict[str, object], ...]
    post_event_analysis: tuple[dict[str, object], ...]
    bootstraps: tuple[BootstrapResult, ...]
    assessments: tuple[dict[str, object], ...]
    manifest: dict[str, object]


class PullbackCalibrationService:
    def __init__(
        self,
        repository: DatabaseRepository,
        config: TradingConfig,
        *,
        catalog_path: Path = CALIBRATION_CATALOG_FILE,
    ) -> None:
        self._repository = repository
        self._config = config
        self._catalog_path = catalog_path
        self._legacy = PullbackExperimentService(repository, config)

    def run(
        self,
        request: PullbackExperimentRequest,
    ) -> PullbackCalibrationBundle:
        request.validate()
        started_clock = time.monotonic()
        started_at = datetime.now(tz=UTC)
        original_catalog_bytes = self._catalog_path.read_bytes()
        catalog = load_pullback_calibration_catalog(self._catalog_path)
        spot, futures, dataset_manifest = self._legacy._load_datasets(request)
        baseline = load_pullback_catalog().by_id("ORIGINAL_BASELINE")
        groups = _market_groups(request)

        audit: list[dict[str, object]] = []
        funnel: list[dict[str, object]] = []
        rejected: list[dict[str, object]] = []
        ablations: list[dict[str, object]] = []
        frequencies: list[OperationalFrequency] = []
        selections: list[dict[str, object]] = []
        development_financial: list[dict[str, object]] = []
        validation_results: list[dict[str, object]] = []
        locks: list[dict[str, object]] = []
        post_event: list[dict[str, object]] = []
        bootstraps: list[BootstrapResult] = []
        assessment_by_key: dict[
            tuple[str, str, str], dict[str, object]
        ] = {}
        development_runs: dict[tuple[str, str, str], PullbackRun] = {}
        development_folds: dict[
            tuple[str, str, str], tuple[PullbackFold, ...]
        ] = {}
        selected_by_group: dict[tuple[str, str], tuple[str, ...]] = {}

        for market, mode in groups:
            baseline_run, _ = self._base_run_and_folds(
                request, market, mode, baseline, "DEVELOPMENT", spot, futures
            )
            baseline_directional_trades = len(baseline_run.trades)
            group_frequencies: list[OperationalFrequency] = []
            for variant in catalog.variants:
                run, folds = self._base_run_and_folds(
                    request,
                    market,
                    mode,
                    variant,
                    "DEVELOPMENT",
                    spot,
                    futures,
                )
                key = (market, mode, variant.variant_id)
                development_runs[key] = run
                development_folds[key] = folds
                exposure = (
                    Decimal(sum(trade.holding_candles for trade in run.trades))
                    / Decimal(run.evaluated_candles)
                    * Decimal("100")
                    if run.evaluated_candles
                    else Decimal("0")
                )
                frequency = operational_frequency(
                    run,
                    folds,
                    baseline_directional_trades=baseline_directional_trades,
                    exposure_percent=exposure,
                )
                frequencies.append(frequency)
                group_frequencies.append(frequency)
                base_labels = [frequency.status.value]
                if frequency.trades < 10:
                    base_labels.append("INSUFFICIENT_SAMPLE")
                assessment_by_key[key] = {
                    "market": market,
                    "mode": mode,
                    "variant_id": variant.variant_id,
                    "classifications": base_labels,
                    "advanced_to_financial_analysis": False,
                    "advanced_to_validation": False,
                    "candidate_frozen": False,
                    "profitability_claimed": False,
                }
                funnel.extend(sequential_funnel_rows(run))
                rejected.extend(rejected_resumption_rows(run))
                ablations.extend(single_rule_ablation_rows(run))
                post_event.extend(
                    _post_event_rows(
                        run,
                        _bars_for(
                            market,
                            request,
                            "DEVELOPMENT",
                            spot,
                            futures,
                        ),
                    )
                )
            selected = select_by_frequency(
                tuple(group_frequencies),
                catalog_order=tuple(
                    variant.variant_id for variant in catalog.variants
                ),
                complexity={
                    variant.variant_id: variant.complexity_rank
                    for variant in catalog.variants
                },
            )
            selected_by_group[(market, mode)] = selected
            selections.append(
                {
                    "market": market,
                    "mode": mode,
                    "selected_variant_ids": selected,
                    "selection_source": "DEVELOPMENT_2022_2023_FREQUENCY_ONLY",
                    "return_metrics_used": False,
                    "validation_2024_used": False,
                    "criteria_order": (
                        "folds_with_trades",
                        "zero_trade_fold_percent",
                        "target_range_distance",
                        "conceptual_modification",
                        "catalog_order",
                    ),
                }
            )

        base_traces = tuple(
            trace
            for run in development_runs.values()
            if run.variant_id == "CALIBRATION_BASE"
            for trace in run.pullback_traces
        )
        audit.extend(logic_audit_rows(base_traces))

        for market, mode in groups:
            selected = selected_by_group[(market, mode)]
            lock = PullbackValidationLock.create(
                market=market,
                mode=mode,
                variant_ids=selected,
                catalog_hash=catalog.canonical_hash,
            )
            lock_payload = {
                **asdict(lock),
                "parameters": {
                    variant_id: _variant_parameters(catalog.by_id(variant_id))
                    for variant_id in selected
                },
                "dataset_hash": _development_dataset_hash(
                    dataset_manifest, market
                ),
                "selection_criteria": "OPERATIONAL_FREQUENCY_ONLY",
                "frequency_metrics": [
                    asdict(item)
                    for item in frequencies
                    if item.market == market and item.mode == mode
                ],
                "locked_before_validation": True,
            }
            locks.append(lock_payload)
            for variant_id in selected:
                run = development_runs[(market, mode, variant_id)]
                folds = development_folds[(market, mode, variant_id)]
                development_financial.append(
                    _financial_row(run, folds)
                )
                bootstraps.append(
                    bootstrap_trades(
                        market=market,
                        mode=mode,
                        variant_id=variant_id,
                        period="DEVELOPMENT",
                        trades=run.trades,
                    )
                )

            validation_ids = ("ORIGINAL_BASELINE", *selected)
            for variant_id in validation_ids:
                variant = (
                    baseline
                    if variant_id == "ORIGINAL_BASELINE"
                    else catalog.by_id(variant_id)
                )
                run, folds = self._base_run_and_folds(
                    request,
                    market,
                    mode,
                    variant,
                    "VALIDATION",
                    spot,
                    futures,
                )
                validation_results.append(_financial_row(run, folds))
                if variant_id != "ORIGINAL_BASELINE":
                    post_event.extend(
                        _post_event_rows(
                            run,
                            _bars_for(
                                market,
                                request,
                                "VALIDATION",
                                spot,
                                futures,
                            ),
                        )
                    )
                    bootstrap = bootstrap_trades(
                        market=market,
                        mode=mode,
                        variant_id=variant_id,
                        period="VALIDATION",
                        trades=run.trades,
                    )
                    bootstraps.append(bootstrap)
                    assessment_by_key[(market, mode, variant_id)] = (
                        _assessment(
                            frequencies,
                            market,
                            mode,
                            variant_id,
                            development_runs[(market, mode, variant_id)],
                            run,
                            development_folds[(market, mode, variant_id)],
                            folds,
                            bootstrap,
                        )
                    )
            lock.assert_unchanged(
                market=market,
                mode=mode,
                variant_ids=selected,
                catalog_hash=catalog.canonical_hash,
            )

        if self._catalog_path.read_bytes() != original_catalog_bytes:
            raise RuntimeError("calibration catalog changed during execution")
        completed_at = datetime.now(tz=UTC)
        experiment_id = (
            "pullback-calibration-"
            f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{catalog.canonical_hash[:8]}"
        )
        manifest = {
            "experiment_id": experiment_id,
            "sprint": "3B.2",
            "research_only": True,
            "catalog_hash": catalog.canonical_hash,
            "catalog_file_sha256": catalog.file_sha256,
            "dataset": dataset_manifest,
            "development_selection_source": "2022_2023_FREQUENCY_ONLY",
            "validation_selection_source": None,
            "post_event_declaration": "POST_EVENT_ONLY_NO_STRATEGY_ACCESS",
            "consumed_2025_loaded": False,
            "consumed_2026_loaded": False,
            "network_used": False,
            "download_used": False,
            "authentication_used": False,
            "external_orders_sent": False,
            "leverage": "1",
            "candidate_frozen": False,
        }
        return PullbackCalibrationBundle(
            experiment_id=experiment_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=Decimal(str(time.monotonic() - started_clock)),
            request=request,
            catalog=catalog,
            dataset_manifest=dataset_manifest,
            logic_audit=tuple(audit),
            sequential_funnel=tuple(funnel),
            rejected_resumptions=tuple(rejected),
            single_rule_ablation=tuple(ablations),
            operational_frequency_results=tuple(frequencies),
            selection_decisions=tuple(selections),
            development_financial_results=tuple(development_financial),
            validation_locks=tuple(locks),
            validation_results=tuple(validation_results),
            post_event_analysis=tuple(post_event),
            bootstraps=tuple(bootstraps),
            assessments=tuple(assessment_by_key.values()),
            manifest=manifest,
        )

    def _base_run_and_folds(
        self,
        request: PullbackExperimentRequest,
        market: str,
        mode: str,
        hypothesis: PullbackHypothesis,
        period: str,
        spot: tuple[Candle, ...],
        futures: FuturesDataset | None,
    ) -> tuple[PullbackRun, tuple[PullbackFold, ...]]:
        start, end = _period_range(request.periods, period)
        if market == "SPOT":
            spot_segment = _spot_segment(
                spot, evaluation_start=start, evaluation_end=end
            )
            configs = _spot_cost_configs(
                replace(
                    self._config,
                    symbol=request.symbol,
                    interval=request.interval,
                )
            )
            run = _run_spot(
                segment=spot_segment,
                config=configs["BASE"],
                hypothesis=hypothesis,
                scenario="BASE",
                period=period,
            )
        else:
            if futures is None:
                raise ValueError("Futures dataset was not loaded")
            futures_segment = _futures_segment(
                futures, evaluation_start=start, evaluation_end=end
            )
            config = _futures_variant_config(
                self._futures_base(), mode=mode, hypothesis=hypothesis
            )
            run = _run_futures(
                segment=futures_segment,
                config=_futures_cost_configs(config)["BASE"],
                hypothesis=hypothesis,
                scenario="BASE",
                period=period,
                mode=mode,
            )
        windows = self._windows(request, period)
        folds = tuple(
            self._legacy._run_fold(
                request=request,
                market=market,
                mode=mode,
                hypothesis=hypothesis,
                period=period,
                scenario="BASE",
                window=window,
                spot_candles=spot,
                futures_dataset=futures,
            )
            for window in windows
        )
        return run, folds

    def _futures_base(self) -> FuturesBacktestConfig:
        from adaptive_trader.futures.real_validation import base_futures_config

        return base_futures_config(self._config)

    @staticmethod
    def _windows(
        request: PullbackExperimentRequest,
        period: str,
    ) -> tuple[PullbackFoldWindow, ...]:
        from adaptive_trader.research.pullback_experiment import (
            build_pullback_walk_forward_windows,
        )

        return build_pullback_walk_forward_windows(
            period=period, periods=request.periods
        )


def _variant_parameters(variant: PullbackHypothesis) -> dict[str, object]:
    return {
        key: value
        for key, value in asdict(variant).items()
        if key not in {"catalog_key", "analyzer"}
    }


def _development_dataset_hash(
    manifest: dict[str, object],
    market: str,
) -> object:
    section = manifest[market.lower()]
    if not isinstance(section, dict):
        raise ValueError("invalid dataset manifest")
    return section.get("development_hash", section.get("combined_dataset_hash"))


def _financial_row(
    run: PullbackRun,
    folds: tuple[PullbackFold, ...],
) -> dict[str, object]:
    row = _result_row(run)
    summary = summarize_folds(folds)
    concentration = concentration_metrics(run.trades)
    row.update(
        {
            "positive_folds": summary.positive_fold_count,
            "positive_fold_percent": summary.positive_fold_percent,
            "median_walk_forward_return_percent": summary.median_return_percent,
            "zero_trade_fold_percent": summary.zero_trade_fold_percent,
            **concentration,
        }
    )
    return row


def _bars_for(
    market: str,
    request: PullbackExperimentRequest,
    period: str,
    spot: tuple[Candle, ...],
    futures: FuturesDataset | None,
) -> tuple[_PriceBar, ...]:
    start, end = _period_range(request.periods, period)
    if market == "SPOT":
        return _spot_segment(
            spot, evaluation_start=start, evaluation_end=end
        ).bars
    if futures is None:
        return ()
    return _futures_segment(
        futures, evaluation_start=start, evaluation_end=end
    ).bars


def _post_event_rows(
    run: PullbackRun,
    bars: tuple[_PriceBar, ...],
) -> tuple[dict[str, object], ...]:
    index_by_close = {
        bar.close_time: index for index, bar in enumerate(bars)
    }
    rows: list[dict[str, object]] = []
    for trace in run.pullback_traces:
        if not trace.resumption_cross:
            continue
        index = index_by_close.get(trace.timestamp)
        if index is None:
            continue
        side = "LONG" if trace.short_ema > trace.long_ema else "SHORT"
        direction = Decimal("1") if side == "LONG" else Decimal("-1")
        row: dict[str, object] = {
            "timestamp": trace.timestamp,
            "market": run.market,
            "mode": run.mode,
            "variant_id": run.variant_id,
            "period": run.period,
            "side": side,
            "signal_created": trace.signal_created,
            "first_failure_code": (
                trace.all_failure_codes[0]
                if trace.all_failure_codes
                else None
            ),
            "declaration": "POST_EVENT_ONLY_NO_STRATEGY_ACCESS",
        }
        for horizon in (1, 3, 6, 12, 24):
            future_index = index + horizon
            row[f"return_after_{horizon}_candles_percent"] = (
                (
                    bars[future_index].close / trace.close_price
                    - Decimal("1")
                )
                * Decimal("100")
                * direction
                if future_index < len(bars)
                else None
            )
        future = bars[index + 1 : index + 25]
        if future:
            if side == "LONG":
                mfe = max(bar.high for bar in future)
                mae = min(bar.low for bar in future)
                row["mfe_percent"] = (
                    (mfe / trace.close_price - Decimal("1")) * Decimal("100")
                )
                row["mae_percent"] = (
                    (mae / trace.close_price - Decimal("1")) * Decimal("100")
                )
            else:
                mfe = min(bar.low for bar in future)
                mae = max(bar.high for bar in future)
                row["mfe_percent"] = (
                    (Decimal("1") - mfe / trace.close_price) * Decimal("100")
                )
                row["mae_percent"] = (
                    (Decimal("1") - mae / trace.close_price) * Decimal("100")
                )
        rows.append(row)
    return tuple(rows)


def _assessment(
    frequencies: list[OperationalFrequency],
    market: str,
    mode: str,
    variant_id: str,
    development: PullbackRun,
    validation: PullbackRun,
    development_folds: tuple[PullbackFold, ...],
    validation_folds: tuple[PullbackFold, ...],
    bootstrap: BootstrapResult,
) -> dict[str, object]:
    frequency = next(
        item
        for item in frequencies
        if item.market == market
        and item.mode == mode
        and item.variant_id == variant_id
    )
    development_summary = summarize_folds(development_folds)
    validation_summary = summarize_folds(validation_folds)
    concentration = concentration_metrics(validation.trades)
    financially_promising = all(
        (
            frequency.status is OperationalStatus.OPERATIONALLY_VIABLE,
            len(development.trades) >= 10,
            len(validation.trades) >= 5,
            development_summary.median_return_percent >= 0,
            validation_summary.median_return_percent >= 0,
            development_summary.positive_fold_percent >= 50,
            validation_summary.positive_fold_percent >= 50,
            validation.net_return_percent >= 0,
            validation.maximum_drawdown_percent <= 10,
            concentration["top_1_percent"] <= 50,
            bootstrap.status.value != "NEGATIVE_UNCERTAIN",
        )
    )
    frequency_stable = (
        validation.resumptions > 0
        and validation.long_signals + validation.short_signals > 0
    )
    classifications = [
        "FREQUENCY_STABLE" if frequency_stable else "FREQUENCY_NON_STATIONARY",
        "FINANCIALLY_PROMISING" if financially_promising else "FINANCIALLY_WEAK",
    ]
    return {
        "market": market,
        "mode": mode,
        "variant_id": variant_id,
        "classifications": classifications,
        "candidate_frozen": False,
        "profitability_claimed": False,
    }
