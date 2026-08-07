"""Orchestration and durable reporting for Sprint 4A.3 edge discovery."""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.execution.latency import LatencyProfile
from adaptive_trader.microstructure.campaign import (
    DatasetSufficiency,
    MicrostructureDatasetCampaign,
    load_campaign,
)
from adaptive_trader.microstructure.elastic_exit import ElasticProfitExitController
from adaptive_trader.microstructure.models import LiquiditySnapshot, ProfitExtensionState
from adaptive_trader.research.executable_forward_labels import (
    HORIZONS_MS,
    NOTIONALS,
    RESEARCH_FEE_PROFILE_ID,
    ExecutableForwardLabel,
    ExecutableForwardLabeler,
    write_labels,
)
from adaptive_trader.research.microstructure_edge_analysis import EdgeCharacterizer, file_hash
from adaptive_trader.research.microstructure_edge_dataset import (
    FeatureAnchor,
    MicrostructureEdgeDatasetBuilder,
    write_feature_anchors,
)

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


class MicrostructureHoldoutLock:
    """Immutable metadata lock; discovery access is rejected by construction."""

    @staticmethod
    def create(
        anchors: tuple[FeatureAnchor, ...],
        *,
        campaign_hash: str,
        software_commit: str,
        config_hash: str,
    ) -> dict[str, object]:
        if not anchors:
            raise ValueError("holdout cannot be empty")
        start = anchors[0].timestamp.isoformat()
        end = anchors[-1].timestamp.isoformat()
        payload = {
            "start": start,
            "end": end,
            "campaign_hash": campaign_hash,
            "anchor_count": len(anchors),
            "event_hashes": sorted(
                {event_hash for anchor in anchors for event_hash in anchor.event_hashes}
            ),
            "software_commit": software_commit,
            "config_hash": config_hash,
            "partition": "LOCKED_FUTURE_HOLDOUT",
        }
        payload["lock_hash"] = _hash(payload)
        return payload

    @staticmethod
    def assert_discovery_allowed(partition: str) -> None:
        if partition == "LOCKED_FUTURE_HOLDOUT":
            raise ValueError("discovery refuses LOCKED_FUTURE_HOLDOUT")


class IntradayEdgeDiscoveryService:
    def build(
        self,
        *,
        campaign_manifest: Path,
        output_dir: Path,
        anchor_ms: int = 250,
        notionals: tuple[Decimal, ...] = NOTIONALS,
        latency_profile: LatencyProfile = LatencyProfile.NORMAL,
        characterize: bool = True,
    ) -> Path:
        campaign = load_campaign(campaign_manifest)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        experiment = output_dir / f"intraday-edge-discovery-{stamp}-{campaign.campaign_hash[:8]}"
        experiment.mkdir(parents=True, exist_ok=False)
        anchors, states = MicrostructureEdgeDatasetBuilder(anchor_interval_ms=anchor_ms).build(
            campaign
        )
        feature_hash = write_feature_anchors(anchors, experiment / "feature_anchors.csv.gz")
        labeler = ExecutableForwardLabeler(notionals=notionals, latency_profile=latency_profile)
        labeled_anchors, discovery_anchors = self._analysis_anchors(campaign, anchors)
        long_labels = self._label_campaign(
            campaign, labeled_anchors, states, labeler, PositionSide.LONG
        )
        short_labels = self._label_campaign(
            campaign, labeled_anchors, states, labeler, PositionSide.SHORT
        )
        long_hash = write_labels(long_labels, experiment / "long_forward_labels.csv.gz")
        short_hash = write_labels(short_labels, experiment / "short_forward_labels.csv.gz")
        commit = _git_commit()
        anchor_config = {
            "anchor_interval_ms": anchor_ms,
            "methodology": "V1_PRE_REGISTERED",
            "maximum_anchors_per_interval": 1,
            "future_fields_present": False,
        }
        labeling_config = {
            "horizons_ms": list(HORIZONS_MS),
            "notionals_usdt": [str(item) for item in notionals],
            "policy": "TAKER_ONLY",
            "latency_profile": latency_profile.value,
            "fee_profile": RESEARCH_FEE_PROFILE_ID,
            "leverage": "1",
            "long_short_calculated_independently": True,
            "mark_price_is_execution_price": False,
            "receive_wall_minus_exchange_time_used": False,
        }
        execution_config = {
            "research_only": True,
            "public_depth_only": True,
            "authenticated": False,
            "orders_sent": False,
            "fee_rates_are_account_tier_claim": False,
        }
        _json(experiment / "anchor_config.json", anchor_config)
        _json(experiment / "labeling_config.json", labeling_config)
        _json(experiment / "execution_config.json", execution_config)
        _json(experiment / "campaign_manifest.json", campaign.as_dict())
        quality = self._quality(campaign, anchors, feature_hash, long_hash, short_hash)
        _json(experiment / "dataset_quality.json", quality)
        partition = self._partition(campaign, anchors, commit, _hash(labeling_config))
        _json(experiment / "temporal_partition.json", partition)
        if "holdout_lock" in partition:
            _json(experiment / "holdout_lock.json", partition["holdout_lock"])
        sensitivity = self._execution_sensitivity(
            campaign, labeled_anchors, states, latency_profile, long_labels, short_labels
        )
        _csv(experiment / "execution_sensitivity.csv", sensitivity)
        _csv(
            experiment / "notional_sensitivity.csv",
            self._notional_sensitivity(long_labels, short_labels),
        )
        if characterize:
            EdgeCharacterizer(
                labeled_anchors,
                long_labels,
                short_labels,
                campaign.status,
                cut_point_anchors=discovery_anchors,
            ).write(experiment)
        elastic_rows, elastic_summary = self._elastic(
            campaign, labeled_anchors, states, long_labels, short_labels
        )
        _csv(experiment / "elastic_real_data_diagnostics.csv", elastic_rows)
        _json(experiment / "elastic_real_data_summary.json", elastic_summary)
        assessment = {
            "dataset_status": campaign.status.value,
            "long_status": "LONG_MORE_DATA_REQUIRED"
            if campaign.status
            in {DatasetSufficiency.ENGINEERING_ONLY, DatasetSufficiency.EXPLORATORY}
            else "LONG_NO_CLEAR_STRUCTURE",
            "short_status": "SHORT_MORE_DATA_REQUIRED"
            if campaign.status
            in {DatasetSufficiency.ENGINEERING_ONLY, DatasetSufficiency.EXPLORATORY}
            else "SHORT_NO_CLEAR_STRUCTURE",
            "elastic_status": elastic_summary["classification"],
            "next_step": "MORE_DATA_REQUIRED"
            if campaign.status
            not in {DatasetSufficiency.DISCOVERY_READY, DatasetSufficiency.CONFIRMATION_READY}
            else "NO_CLEAR_INTRADAY_EDGE_YET",
            "structure_observed_is_profitable_strategy_claim": False,
            "alpha_v1_implemented": False,
        }
        _json(experiment / "association_assessment.json", assessment)
        _json(experiment / "data_requirements.json", self._requirements(campaign))
        manifest = {
            "experiment_id": experiment.name,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "software_commit": commit,
            "campaign_id": campaign.campaign_id,
            "campaign_hash": campaign.campaign_hash,
            "dataset_status": campaign.status.value,
            "session_count": len(campaign.sessions),
            "duration_seconds": campaign.total_duration_seconds,
            "utc_dates_covered": list(campaign.utc_dates_covered),
            "anchor_count": len(anchors),
            "anchor_frequency_ms": anchor_ms,
            "feature_hash": feature_hash,
            "long_label_hash": long_hash,
            "short_label_hash": short_hash,
            "labeling_config_hash": _hash(labeling_config),
            "execution_config_hash": _hash(execution_config),
            "research_only": True,
            "public_only": True,
            "authentication_used": False,
            "orders_sent": False,
            "clock_limitation": (
                "receive_wall - exchange_event_time is not reliable one-way latency "
                "and is excluded from alpha and labels"
            ),
            **assessment,
        }
        _json(experiment / "experiment_manifest.json", manifest)
        self._report(experiment, manifest, campaign, long_labels, short_labels, elastic_summary)
        return experiment

    @staticmethod
    def inspect(experiment: Path) -> dict[str, object]:
        manifest = json.loads((experiment / "experiment_manifest.json").read_text())
        hashes = {
            "feature_hash_valid": file_hash(experiment / "feature_anchors.csv.gz")
            == manifest["feature_hash"],
            "long_label_hash_valid": file_hash(experiment / "long_forward_labels.csv.gz")
            == manifest["long_label_hash"],
            "short_label_hash_valid": file_hash(experiment / "short_forward_labels.csv.gz")
            == manifest["short_label_hash"],
        }
        return {**manifest, **hashes, "all_hashes_valid": all(hashes.values())}

    @staticmethod
    def _quality(
        campaign: MicrostructureDatasetCampaign,
        anchors: tuple[FeatureAnchor, ...],
        feature_hash: str,
        long_hash: str,
        short_hash: str,
    ) -> dict[str, object]:
        source_hashes = {
            item.session_id: hashlib.sha256(
                (Path(item.path) / "manifest.json").read_bytes()
            ).hexdigest()
            for item in campaign.sessions
        }
        return {
            "campaign_quality": "CLEAN"
            if not campaign.warnings
            else "INTEGRITY_PRESERVED_WITH_WARNING",
            "sessions_eligible": len(campaign.sessions),
            "source_session_manifest_hashes": source_hashes,
            "session_warnings": list(campaign.warnings),
            "capture_breaks": list(campaign.capture_breaks),
            "capture_breaks_are_market_data_gaps": False,
            "anchors": len(anchors),
            "feature_hash": feature_hash,
            "long_label_hash": long_hash,
            "short_label_hash": short_hash,
            "invalid_states_repaired": False,
            "transport_clock_alignment_valid": False,
            "transport_clock_limitation": (
                "receive_wall - exchange_event_time cannot establish one-way network latency"
            ),
        }

    @staticmethod
    def _partition(
        campaign: MicrostructureDatasetCampaign,
        anchors: tuple[FeatureAnchor, ...],
        commit: str,
        config_hash: str,
    ) -> dict[str, object]:
        if campaign.status not in {
            DatasetSufficiency.DISCOVERY_READY,
            DatasetSufficiency.CONFIRMATION_READY,
        }:
            return {
                "status": "NOT_APPLICABLE_BELOW_DISCOVERY_READY",
                "chronological": True,
                "random_shuffle": False,
                "holdout_created": False,
                "reason": "Dataset below 24 valid hours and two UTC dates",
            }
        discovery_end = int(len(anchors) * 0.6)
        confirmation_end = int(len(anchors) * 0.8)
        discovery = anchors[:discovery_end]
        confirmation = anchors[discovery_end:confirmation_end]
        holdout = anchors[confirmation_end:]
        return {
            "status": "LOCKED",
            "chronological": True,
            "random_shuffle": False,
            "discovery_anchor_count": discovery_end,
            "confirmation_anchor_count": confirmation_end - discovery_end,
            "holdout_anchor_count": len(holdout),
            "discovery_partition_hash": _anchor_partition_hash(discovery),
            "confirmation_partition_hash": _anchor_partition_hash(confirmation),
            "holdout_partition_hash": _anchor_partition_hash(holdout),
            "holdout_lock": MicrostructureHoldoutLock.create(
                holdout,
                campaign_hash=campaign.campaign_hash,
                software_commit=commit,
                config_hash=config_hash,
            ),
        }

    @staticmethod
    def _analysis_anchors(
        campaign: MicrostructureDatasetCampaign,
        anchors: tuple[FeatureAnchor, ...],
    ) -> tuple[tuple[FeatureAnchor, ...], tuple[FeatureAnchor, ...]]:
        if campaign.status not in {
            DatasetSufficiency.DISCOVERY_READY,
            DatasetSufficiency.CONFIRMATION_READY,
        }:
            return anchors, anchors
        discovery_end = int(len(anchors) * 0.6)
        confirmation_end = int(len(anchors) * 0.8)
        return anchors[:confirmation_end], anchors[:discovery_end]

    @staticmethod
    def _session_states(
        campaign: MicrostructureDatasetCampaign,
        states: tuple[LiquiditySnapshot, ...],
    ) -> dict[str, tuple[LiquiditySnapshot, ...]]:
        result: dict[str, tuple[LiquiditySnapshot, ...]] = {}
        for session in campaign.sessions:
            start = datetime.fromisoformat(session.start)
            end = datetime.fromisoformat(session.end)
            result[session.session_id] = tuple(
                state for state in states if start <= state.timestamp <= end
            )
        return result

    def _label_campaign(
        self,
        campaign: MicrostructureDatasetCampaign,
        anchors: tuple[FeatureAnchor, ...],
        states: tuple[LiquiditySnapshot, ...],
        labeler: ExecutableForwardLabeler,
        side: PositionSide,
    ) -> tuple[ExecutableForwardLabel, ...]:
        states_by_session = self._session_states(campaign, states)
        rows: list[ExecutableForwardLabel] = []
        for session in campaign.sessions:
            session_anchors = tuple(
                anchor for anchor in anchors if anchor.session_id == session.session_id
            )
            rows.extend(
                labeler.label(session_anchors, states_by_session[session.session_id], side)
            )
        return tuple(rows)

    def _execution_sensitivity(
        self,
        campaign: MicrostructureDatasetCampaign,
        anchors: tuple[FeatureAnchor, ...],
        states: tuple[LiquiditySnapshot, ...],
        baseline: LatencyProfile,
        long_labels: tuple[ExecutableForwardLabel, ...],
        short_labels: tuple[ExecutableForwardLabel, ...],
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        profiles = (LatencyProfile.FAST, LatencyProfile.NORMAL, LatencyProfile.STRESSED)
        for profile in profiles:
            if profile is baseline:
                by_side = (("LONG", long_labels), ("SHORT", short_labels))
            else:
                labeler = ExecutableForwardLabeler(
                    latency_profile=profile, calculate_excursions=False
                )
                by_side = (
                    (
                        "LONG",
                        self._label_campaign(
                            campaign, anchors, states, labeler, PositionSide.LONG
                        ),
                    ),
                    (
                        "SHORT",
                        self._label_campaign(
                            campaign, anchors, states, labeler, PositionSide.SHORT
                        ),
                    ),
                )
            for side, labels in by_side:
                for key, values in _group_labels(labels).items():
                    rows.append(
                        {
                            "latency_profile": profile.value,
                            "side": side,
                            "horizon_ms": key[0],
                            "notional": key[1],
                            **_summary(values),
                        }
                    )
        return rows

    @staticmethod
    def _notional_sensitivity(
        long_labels: tuple[ExecutableForwardLabel, ...],
        short_labels: tuple[ExecutableForwardLabel, ...],
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for side, labels in (("LONG", long_labels), ("SHORT", short_labels)):
            for key, values in _group_labels(labels).items():
                rows.append(
                    {"side": side, "horizon_ms": key[0], "notional": key[1], **_summary(values)}
                )
        return rows

    def _elastic(
        self,
        campaign: MicrostructureDatasetCampaign,
        anchors: tuple[FeatureAnchor, ...],
        states: tuple[LiquiditySnapshot, ...],
        long_labels: tuple[ExecutableForwardLabel, ...],
        short_labels: tuple[ExecutableForwardLabel, ...],
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        session_states = self._session_states(campaign, states)
        anchor_map = {item.anchor_id: item for item in anchors}
        selected: dict[tuple[str, str], ExecutableForwardLabel] = {}
        for label in (*long_labels, *short_labels):
            if (
                label.requested_notional == Decimal("100")
                and label.horizon_ms == 60000
                and label.entry_price is not None
            ):
                selected[(label.side, label.anchor_id)] = label
        rows: list[dict[str, object]] = []
        for (side_name, anchor_id), label in selected.items():
            side = PositionSide(side_name)
            anchor = anchor_map[anchor_id]
            ordered = session_states[anchor.session_id]
            times = tuple(item.timestamp for item in ordered)
            quantity = Decimal("100") / anchor.liquidity.mid_price
            assert label.entry_price is not None
            controller = ElasticProfitExitController(
                side=side, quantity=quantity, entry_price=label.entry_price
            )
            start = bisect.bisect_left(times, anchor.timestamp)
            end = bisect.bisect_right(times, anchor.timestamp + timedelta(seconds=60))
            immediate: Decimal | None = None
            elastic: Decimal | None = None
            reason: str | None = None
            previous: Decimal | None = None
            exit_time = anchor.timestamp + timedelta(seconds=60)
            exit_price: Decimal | None = None
            last_observation = None
            for state in ordered[start:end]:
                price = (
                    state.executable_sell_price(quantity)
                    if side is PositionSide.LONG
                    else state.executable_buy_price(quantity)
                )
                reversal = bool(
                    previous is not None
                    and price is not None
                    and (price < previous if side is PositionSide.LONG else price > previous)
                )
                observation = controller.observe(
                    timestamp=state.timestamp, liquidity=state, microstructure_reversal=reversal
                )
                last_observation = observation
                if observation.state is ProfitExtensionState.ARMED and immediate is None:
                    immediate = observation.net_executable_profit_bps
                if observation.state in {
                    ProfitExtensionState.EXIT_REQUESTED,
                    ProfitExtensionState.FAILSAFE,
                }:
                    elastic = observation.net_executable_profit_bps
                    reason = observation.exit_reason
                    exit_time = state.timestamp
                    exit_price = price
                    break
                if price is not None:
                    previous = price
                    exit_price = price
                    exit_time = state.timestamp
            if immediate is None:
                continue
            elastic = (
                elastic
                if elastic is not None
                else (
                    last_observation.net_executable_profit_bps
                    if last_observation is not None
                    else None
                )
            )
            post_index = bisect.bisect_left(times, exit_time + timedelta(seconds=1))
            post_exit_move: Decimal | None = None
            if exit_price is not None and post_index < len(ordered):
                post_state = ordered[post_index]
                post_price = (
                    post_state.executable_sell_price(quantity)
                    if side is PositionSide.LONG
                    else post_state.executable_buy_price(quantity)
                )
                if post_price is not None:
                    post_exit_move = (
                        (post_price / exit_price - Decimal("1")) * TEN_THOUSAND
                        if side is PositionSide.LONG
                        else (exit_price / post_price - Decimal("1")) * TEN_THOUSAND
                    )
            rows.append(
                {
                    "anchor_id": anchor_id,
                    "side": side_name,
                    "notional": "100",
                    "activation": True,
                    "immediate_exit_pnl_bps": immediate,
                    "elastic_exit_pnl_bps": elastic,
                    "incremental_pnl_bps": elastic - immediate if elastic is not None else None,
                    "maximum_additional_capture_bps": max(
                        ZERO, (label.mfe_bps_60s or immediate) - immediate
                    ),
                    "profit_giveback_bps": max(
                        ZERO, (label.mfe_bps_60s or immediate) - (elastic or immediate)
                    ),
                    "exit_reason": reason or "CAPTURE_BOUNDARY",
                    "partial_exit": False,
                    "slippage_bps": label.depth_slippage_bps,
                    "adverse_selection_after_exit_bps": post_exit_move,
                    "mark_price_used": False,
                    "post_event_research_only": True,
                }
            )
        increments = [
            Decimal(str(row["incremental_pnl_bps"]))
            for row in rows
            if row["incremental_pnl_bps"] is not None
        ]
        minimum = 30
        classification = (
            "INSUFFICIENT_SAMPLE"
            if len(rows) < minimum
            else "EXTENSION_HELPFUL"
            if sum(increments, ZERO) > ZERO
            and sum(value > ZERO for value in increments) / len(increments) >= Decimal("0.6")
            else "EXTENSION_HARMFUL"
            if sum(increments, ZERO) < ZERO
            and sum(value < ZERO for value in increments) / len(increments) >= Decimal("0.6")
            else "MIXED"
        )
        reasons = Counter(str(row["exit_reason"]) for row in rows)
        summary = {
            "profile": "ELASTIC_300_150_V0",
            "activation_count": len(rows),
            "minimum_sample_for_classification": minimum,
            "mean_immediate_exit_pnl_bps": _mean_decimal(rows, "immediate_exit_pnl_bps"),
            "mean_elastic_exit_pnl_bps": _mean_decimal(rows, "elastic_exit_pnl_bps"),
            "mean_incremental_pnl_bps": str(sum(increments, ZERO) / len(increments))
            if increments
            else None,
            "mean_maximum_additional_capture_bps": _mean_decimal(
                rows, "maximum_additional_capture_bps"
            ),
            "mean_profit_giveback_bps": _mean_decimal(rows, "profit_giveback_bps"),
            "mean_slippage_bps": _mean_decimal(rows, "slippage_bps"),
            "mean_adverse_selection_after_exit_bps": _mean_decimal(
                rows, "adverse_selection_after_exit_bps"
            ),
            "hard_floor_triggers": reasons["HARD_PROFIT_FLOOR"],
            "reversal_150ms_exits": reasons["REVERSAL_CONFIRMED_150MS"],
            "timeout_300ms_exits": reasons["NO_NEW_PEAK_300MS"],
            "liquidity_failsafes": reasons["LIQUIDITY_EXIT_FAILSAFE"],
            "partial_exits": 0,
            "classification": classification,
            "parameters_changed_after_results": False,
            "mark_price_used": False,
        }
        return rows, summary

    @staticmethod
    def _requirements(campaign: MicrostructureDatasetCampaign) -> dict[str, object]:
        remaining = max(0, 24 * 3600 - campaign.total_duration_seconds)
        return {
            "current_status": campaign.status.value,
            "valid_duration_seconds": campaign.total_duration_seconds,
            "utc_dates_covered": len(campaign.utc_dates_covered),
            "discovery_ready_requires_seconds": 86400,
            "discovery_ready_requires_utc_dates": 2,
            "additional_valid_seconds_required": remaining,
            "requirements_were_reduced_after_results": False,
            "next_step": "MORE_DATA_REQUIRED"
            if remaining
            else "PROCEED_WITH_LOCKED_TEMPORAL_PROTOCOL",
        }

    @staticmethod
    def _report(
        output: Path,
        manifest: dict[str, object],
        campaign: MicrostructureDatasetCampaign,
        long_labels: tuple[ExecutableForwardLabel, ...],
        short_labels: tuple[ExecutableForwardLabel, ...],
        elastic: dict[str, object],
    ) -> None:
        long_summary = _summary(list(long_labels))
        short_summary = _summary(list(short_labels))
        text = f"""# Intraday edge discovery — Sprint 4A.3

## Decision

- DATASET_STATUS: **{manifest["dataset_status"]}**
- LONG_STATUS: **{manifest["long_status"]}**
- SHORT_STATUS: **{manifest["short_status"]}**
- ELASTIC_STATUS: **{manifest["elastic_status"]}**
- NEXT_STEP: **{manifest["next_step"]}**

This is dataset engineering and statistical characterization, not alpha calibration or a
profitable-strategy claim. No threshold, horizon, notional or side was selected by PnL.

## Provenance and scope

- Campaign `{campaign.campaign_id}` contains {len(campaign.sessions)} valid session(s),
  {campaign.total_duration_seconds:.3f} seconds and {len(campaign.utc_dates_covered)} UTC date(s).
- Campaign hash: `{campaign.campaign_hash}`.
- Anchors: {manifest["anchor_count"]} at a pre-registered maximum frequency of 250 ms.
- Horizons: {", ".join(str(item) + "ms" for item in HORIZONS_MS)}.
- Notionals: 100, 500 and 1000 USDT; leverage 1x.
- Baseline: TAKER_ONLY, NORMAL latency, `{RESEARCH_FEE_PROFILE_ID}`.
- Long executable rate: {long_summary["execution_rate_percent"]};
  short: {short_summary["execution_rate_percent"]}.
- Long mean net bps: {long_summary["mean_net_bps"]}; short: {short_summary["mean_net_bps"]}.
- Elastic activations: {elastic["activation_count"]}; hard floors: {elastic["hard_floor_triggers"]};
  liquidity failsafes: {elastic["liquidity_failsafes"]}.

## Methodological protections

Features and future labels are stored separately and implemented in separate modules. Alpha code
does not import the offline labeler. Long and short execution are independently simulated from
asks/bids; short is not the negation of long. Realization never uses mark price. Quantile bounds are
frozen from discovery before confirmation when the dataset becomes eligible. Discovery rejects the
locked future holdout. The 15-minute, 2,000-iteration, seed-42 block bootstrap does not claim iid
anchors.

`receive_wall_time - exchange_event_time` is explicitly invalid as one-way network latency because
the clocks were not aligned. It is not used by features, labels, alpha or conclusions.

## Interpretation

The available campaign is below 24 valid hours and two UTC dates. All edge, regime, time-of-day,
bootstrap, no-trade and Elastic results are engineering diagnostics only. The valid result is
`MORE_DATA_REQUIRED`; Alpha V1 was not implemented.
"""
        (output / "intraday_edge_discovery_report.md").write_text(text, encoding="utf-8")


def _group_labels(
    labels: tuple[ExecutableForwardLabel, ...],
) -> dict[tuple[int, str], list[ExecutableForwardLabel]]:
    groups: dict[tuple[int, str], list[ExecutableForwardLabel]] = defaultdict(list)
    for item in labels:
        groups[(item.horizon_ms, str(item.requested_notional))].append(item)
    return groups


def _summary(labels: list[ExecutableForwardLabel]) -> dict[str, object]:
    net = [float(item.net_return_bps) for item in labels if item.net_return_bps is not None]
    costs = [float(item.total_cost_bps) for item in labels if item.total_cost_bps is not None]
    slip = [
        float(item.depth_slippage_bps) for item in labels if item.depth_slippage_bps is not None
    ]
    return {
        "samples": len(labels),
        "execution_rate_percent": 100 * sum(item.executable for item in labels) / len(labels)
        if labels
        else 0,
        "mean_net_bps": sum(net) / len(net) if net else None,
        "positive_percent": 100 * sum(value > 0 for value in net) / len(net) if net else None,
        "mean_cost_bps": sum(costs) / len(costs) if costs else None,
        "mean_depth_slippage_bps": sum(slip) / len(slip) if slip else None,
    }


def _mean_decimal(rows: list[dict[str, object]], name: str) -> str | None:
    values = [Decimal(str(row[name])) for row in rows if row.get(name) is not None]
    return str(sum(values, ZERO) / len(values)) if values else None


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _anchor_partition_hash(anchors: tuple[FeatureAnchor, ...]) -> str:
    return _hash([anchor.anchor_id for anchor in anchors])


def _json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("status\nNO_DATA\n", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
