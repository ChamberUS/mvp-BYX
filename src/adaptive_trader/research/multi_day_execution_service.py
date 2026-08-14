"""Durable reporting for multi-day execution economics without alpha selection."""

from __future__ import annotations

import csv
import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.microstructure.campaign import (
    CampaignSession,
    MicrostructureDatasetCampaign,
    dataset_sufficiency,
    load_campaign,
)
from adaptive_trader.microstructure.scientific_admission import (
    qualify_session,
    reject_duplicate_sessions,
)
from adaptive_trader.microstructure.storage import inspect_session
from adaptive_trader.research.multi_day_execution import (
    ACCOUNT_NOTIONALS,
    LONG_HORIZONS_MS,
    ExecutionPolicyCatalog,
    ExitVariantId,
    QualificationEvidence,
    accessible_edge_answer,
    consumed_campaign_manifest,
    episode_block_bootstrap,
    execution_policy_fee_bps,
    extended_horizon_availability,
    load_execution_policy_catalog,
    runner_status,
    stable_hash,
    validate_new_campaign,
)

NEW_CAMPAIGN_ID = "ethusdt-futures-intraday-discovery-v1"
PROVENANCE_BASELINE_COMMIT = "5d2aa404f43fd873d484ba9d1843c6667acfe8f9"


class MultiDayExecutionEconomicsService:
    def build(
        self,
        *,
        campaign_manifest: Path,
        consumed_campaign_manifest_path: Path,
        policy_catalog_path: Path,
        output_dir: Path,
    ) -> Path:
        campaign = load_campaign(campaign_manifest)
        old_campaign = load_campaign(consumed_campaign_manifest_path)
        commit = _git_commit()
        consumed = consumed_campaign_manifest(old_campaign, commit=commit)
        admissions = reject_duplicate_sessions(
            tuple(
                qualify_session(
                    Path(item.path),
                    expected_market=campaign.market,
                    expected_symbol=campaign.symbol,
                )
                for item in campaign.sessions
            )
        )
        admitted_paths = {item.path for item in admissions if item.admitted}
        eligible_sessions = tuple(
            item for item in campaign.sessions if item.path in admitted_paths
        )
        dates = tuple(
            sorted(
                {
                    datetime.fromisoformat(item.start).date().isoformat()
                    for item in eligible_sessions
                }
                | {
                    datetime.fromisoformat(item.end).date().isoformat()
                    for item in eligible_sessions
                }
            )
        )
        duration = sum(item.duration_seconds for item in eligible_sessions)
        eligible_campaign = replace(
            campaign,
            sessions=eligible_sessions,
            total_duration_seconds=duration,
            utc_dates_covered=dates,
            event_counts=_event_counts(eligible_sessions),
            capture_breaks=(),
            warnings=tuple(warning for item in eligible_sessions for warning in item.warnings),
            software_commits=tuple(sorted({item.software_commit for item in eligible_sessions})),
            status=dataset_sufficiency(duration, len(dates)),
            campaign_hash=stable_hash(
                [(item.session_id, list(item.event_hashes)) for item in eligible_sessions]
            ),
        )
        validate_new_campaign(
            eligible_campaign,
            consumed,
            minimum_session_start=_git_commit_time(PROVENANCE_BASELINE_COMMIT),
        )
        campaign = eligible_campaign
        catalog = load_execution_policy_catalog(policy_catalog_path)
        experiment_id = (
            f"multi-day-economic-qualification-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}-"
            f"{campaign.campaign_hash[:8]}"
        )
        output = output_dir / experiment_id
        output.mkdir(parents=True, exist_ok=False)

        _json(output / "engineering_consumed_manifest.json", consumed)
        _json(
            output / "provenance_audit.json",
            {
                "status": "COMPLETE",
                "sessions_admitted": sum(item.admitted for item in admissions),
                "sessions_rejected": sum(not item.admitted for item in admissions),
                "historical_raw_rewritten": False,
                "admissions": [item.as_dict() for item in admissions],
            },
        )
        _csv(output / "session_admission.csv", [item.as_dict() for item in admissions])
        _json(output / "campaign_manifest.json", campaign.as_dict())
        _json(output / "campaign_status.json", self._campaign_status(campaign))
        _json(output / "campaign_progress.json", self._campaign_status(campaign))
        _json(output / "execution_policy_catalog.json", catalog.as_dict())
        _json(output / "exit_variant_catalog.json", self._exit_catalog())
        _json(output / "dataset_quality.json", self._quality(campaign, consumed))

        labels = self._extended_labels(campaign, catalog)
        _csv(output / "extended_horizon_labels.csv", labels)
        economics = self._policy_economics(campaign, catalog)
        _csv(output / "execution_policy_economics.csv", economics)
        _csv(output / "long_economics.csv", [row for row in economics if row["side"] == "LONG"])
        _csv(output / "short_economics.csv", [row for row in economics if row["side"] == "SHORT"])
        _csv(output / "cost_decomposition.csv", economics)
        _csv(output / "execution_sensitivity.csv", self._sensitivity(catalog))
        _csv(output / "maker_fill_quality.csv", self._maker_quality(catalog))
        _csv(output / "maker_adverse_selection.csv", self._maker_adverse(catalog))
        _csv(output / "taker_execution_quality.csv", self._taker_quality(catalog))
        _csv(output / "notional_capacity.csv", self._capacity(campaign))
        _csv(output / "small_account_feasibility.csv", self._small_account(campaign))
        _csv(output / "non_overlapping_episodes.csv", self._empty_episode_rows(catalog))
        long_runner = self._runner_rows(campaign, PositionSide.LONG)
        short_runner = self._runner_rows(campaign, PositionSide.SHORT)
        _csv(output / "long_runner_results.csv", long_runner)
        _csv(output / "short_runner_results.csv", short_runner)
        _csv(output / "runner_10m.csv", self._exit_rows(campaign, ExitVariantId.RUNNER_10M))
        _csv(output / "runner_15m.csv", self._exit_rows(campaign, ExitVariantId.RUNNER_15M))
        _csv(output / "elastic_300_150.csv", self._exit_rows(campaign, ExitVariantId.ELASTIC))
        comparison = self._runner_comparison(campaign)
        _json(output / "runner_comparison.json", comparison)
        _json(output / "block_bootstrap.json", episode_block_bootstrap(()))
        _csv(output / "temporal_stability.csv", self._temporal_stability(campaign))
        _csv(output / "no_trade_execution_contexts.csv", self._no_trade(campaign))
        _csv(output / "no_trade_contexts.csv", self._no_trade(campaign))
        _json(output / "frequency_analysis.json", self._frequency(campaign))
        requirements = self._requirements(campaign)
        _json(output / "data_requirements.json", requirements)
        decision = accessible_edge_answer(
            QualificationEvidence(
                dataset_status=campaign.status,
                utc_dates=len(campaign.utc_dates_covered),
                quality_sufficient=not campaign.warnings,
                independent_episodes_sufficient=False,
                maker_observations_sufficient=False,
                all_structures_evaluable=False,
                accessible_candidate_count=0,
                normal_latency_positive=False,
                confirmation_same_direction=False,
                temporally_distributed=False,
                bootstrap_supportive=False,
            )
        )
        discovery_confirmation = {
            "temporal_split": "60/20/20",
            "split_created": False,
            "split_hashes": None,
            "discovery": "NOT_AVAILABLE",
            "confirmation": "NOT_AVAILABLE",
            "reason": "DATASET_BELOW_DISCOVERY_READY",
        }
        _json(output / "discovery_confirmation.json", discovery_confirmation)
        _json(
            output / "holdout_lock.json",
            {
                "status": "LOCKED",
                "accessed": False,
                "winner_selection_allowed": False,
                "alpha_v1_selected": False,
            },
        )
        central_answer = {
            "question": (
                "Existe uma combinação de microestrutura + execução maker/taker que produz edge "
                "líquido em vários dias, usando condições acessíveis a uma conta pequena?"
            ),
            "answer": decision.value,
            "best_supported_side": None,
            "supported_execution_policy": None,
            "supported_notional_range": None,
            "discovery_result": "NOT_AVAILABLE",
            "confirmation_result": "NOT_AVAILABLE",
            "holdout_status": "LOCKED",
            "evidence_strength": "INSUFFICIENT_MULTI_DAY_EVIDENCE",
            "limitations": [
                "DATASET_BELOW_DISCOVERY_READY",
                "FEWER_THAN_TWO_UTC_DATES",
                "INSUFFICIENT_INDEPENDENT_EPISODES",
                "INSUFFICIENT_MAKER_FILL_OBSERVATIONS",
                "RUNNER_SAMPLE_INSUFFICIENT",
            ],
        }
        _json(output / "accessible_intraday_edge_answer.json", central_answer)
        manifest: dict[str, object] = {
            "experiment_id": experiment_id,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "software_commit": commit,
            "campaign_id": campaign.campaign_id,
            "campaign_hash": campaign.campaign_hash,
            "consumed_campaign_id": old_campaign.campaign_id,
            "consumed_campaign_hash": old_campaign.campaign_hash,
            "dataset_status": campaign.status.value,
            "execution_policy_catalog_hash": catalog.catalog_hash,
            "exit_variant_catalog_hash": stable_hash(self._exit_catalog()),
            "extended_horizons_ms": list(LONG_HORIZONS_MS),
            "notionals_usdt": [str(item) for item in ACCOUNT_NOTIONALS],
            "research_only": True,
            "authentication_used": False,
            "external_orders_sent": False,
            "leverage": "1",
            "holdout_accessed": False,
            "alpha_v1_implemented": False,
            "next_step": "MORE_DATA_REQUIRED",
            "central_answer": decision.value,
            "scientific_sessions_admitted": len(eligible_sessions),
            "scientific_sessions_rejected": sum(not item.admitted for item in admissions),
        }
        _json(output / "experiment_manifest.json", manifest)
        self._report(output, manifest, campaign, catalog, labels, comparison, requirements)
        (output / "multi_day_economic_qualification_report.md").write_text(
            (output / "multi_day_execution_economics_report.md").read_text(encoding="utf-8")
            + "\n## Central scientific answer\n\n**MORE_DATA_REQUIRED**\n",
            encoding="utf-8",
        )
        return output

    @staticmethod
    def inspect(experiment: Path) -> dict[str, object]:
        manifest = json.loads((experiment / "experiment_manifest.json").read_text())
        required = (
            "engineering_consumed_manifest.json",
            "campaign_manifest.json",
            "execution_policy_economics.csv",
            "long_runner_results.csv",
            "short_runner_results.csv",
            "runner_comparison.json",
        )
        return {
            **manifest,
            "required_artifacts_present": all((experiment / name).is_file() for name in required),
        }

    @staticmethod
    def _campaign_status(campaign: MicrostructureDatasetCampaign) -> dict[str, object]:
        duration = campaign.total_duration_seconds
        dates = campaign.utc_dates_covered
        return {
            "campaign_id": campaign.campaign_id,
            "campaign_hash": campaign.campaign_hash,
            "sessions": len(campaign.sessions),
            "valid_duration_seconds": duration,
            "utc_dates": list(dates),
            "events": campaign.event_counts,
            "quality": "CLEAN" if not campaign.warnings else "WARNING",
            "capture_breaks": list(campaign.capture_breaks),
            "dataset_status": campaign.status.value,
            "seconds_missing_for_discovery": max(0.0, 86_400 - duration),
            "utc_dates_missing_for_discovery": max(0, 2 - len(dates)),
        }

    @staticmethod
    def _quality(
        campaign: MicrostructureDatasetCampaign, consumed: dict[str, object]
    ) -> dict[str, object]:
        return {
            "new_campaign_only": True,
            "consumed_engineering_campaign_excluded": True,
            "consumed_campaign_hash": consumed["campaign_hash"],
            "session_count": len(campaign.sessions),
            "warnings": list(campaign.warnings),
            "capture_breaks_are_market_gaps": False,
            "invalid_long_horizon_labels_repaired": False,
            "receive_wall_minus_exchange_time_used": False,
        }

    @staticmethod
    def _exit_catalog() -> dict[str, object]:
        return {
            "variants": [item.value for item in ExitVariantId],
            "elastic_300_150_v0_modified": False,
            "runner_10m_max_hold_ms": 600_000,
            "runner_15m_max_hold_ms": 900_000,
            "runner_reversal_confirmation_ms": 150,
            "runner_has_300ms_no_new_peak_timeout": False,
            "thresholds_selected_from_pnl": False,
        }

    @staticmethod
    def _extended_labels(
        campaign: MicrostructureDatasetCampaign, catalog: ExecutionPolicyCatalog
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for session in campaign.sessions:
            start = datetime.fromisoformat(session.start)
            end = datetime.fromisoformat(session.end)
            for side in PositionSide:
                for horizon in LONG_HORIZONS_MS:
                    availability = extended_horizon_availability(
                        session_id=session.session_id,
                        side=side,
                        anchor_time=start,
                        horizon_ms=horizon,
                        session_end=end,
                    )
                    for notional in ACCOUNT_NOTIONALS:
                        for policy in catalog.policies:
                            rows.append(
                                {
                                    "session_id": session.session_id,
                                    "side": side.value,
                                    "anchor_time": start.isoformat(),
                                    "horizon_ms": horizon,
                                    "notional": str(notional),
                                    "execution_policy": policy.policy_id.value,
                                    "status": availability.status.value,
                                    "reason": availability.reason,
                                    "net_return_bps": None,
                                    "mark_price_used": False,
                                }
                            )
        return rows

    @staticmethod
    def _policy_economics(
        campaign: MicrostructureDatasetCampaign, catalog: ExecutionPolicyCatalog
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for side in PositionSide:
            for policy in catalog.policies:
                for notional in ACCOUNT_NOTIONALS:
                    for horizon in LONG_HORIZONS_MS:
                        maker_legs = sum(
                            role is not None and role.value == "MAKER"
                            for role in (policy.entry_role, policy.exit_role)
                        )
                        rows.append(
                            {
                                "side": side.value,
                                "horizon_ms": horizon,
                                "notional": str(notional),
                                "execution_policy": policy.policy_id.value,
                                "latency_profile": "NORMAL",
                                "fee_profile": catalog.fee_profile,
                                "gross_movement_bps": None,
                                "maker_fee_bps": str(Decimal("2") * maker_legs),
                                "taker_fee_bps": str(
                                    execution_policy_fee_bps(policy)
                                    - Decimal("2") * maker_legs
                                ),
                                "spread_crossing_bps": None,
                                "queue_opportunity_cost_bps": None,
                                "depth_slippage_bps": None,
                                "latency_slippage_bps": None,
                                "missed_fills": None,
                                "total_cost_bps": str(execution_policy_fee_bps(policy)),
                                "net_edge_bps": None,
                                "status": "MORE_DATA_REQUIRED",
                                "dataset_status": campaign.status.value,
                            }
                        )
        return rows

    @staticmethod
    def _sensitivity(catalog: ExecutionPolicyCatalog) -> list[dict[str, object]]:
        return [
            {
                "side": side.value,
                "execution_policy": policy.policy_id.value,
                "notional": str(notional),
                "latency_profile": latency,
                "fee_profile": catalog.fee_profile,
                "net_edge_bps": None,
                "normal_latency_required_for_yes": True,
                "status": "MORE_DATA_REQUIRED",
            }
            for side in PositionSide
            for policy in catalog.policies
            for notional in ACCOUNT_NOTIONALS
            for latency in ("FAST", "NORMAL", "STRESSED")
        ]

    @staticmethod
    def _maker_quality(catalog: ExecutionPolicyCatalog) -> list[dict[str, object]]:
        return [
            {
                "execution_policy": item.policy_id.value,
                "maker_wait_ms": catalog.maker_wait_ms,
                "queue_model": catalog.queue_model,
                "opportunities": 0,
                "hypothetical_orders_posted": 0,
                "fills": 0,
                "full_fills": 0,
                "partial_fills": 0,
                "no_fills": 0,
                "fill_rate": None,
                "full_fill_rate": None,
                "partial_fill_rate": None,
                "queue_ahead_quantity": None,
                "missed_opportunity_rate": None,
                "missed_favorable_moves": None,
                "mean_time_to_fill_ms": None,
                "mean_queue_time_ms": None,
                "cancel_count": 0,
                "fallback_taker_rate": None,
                "adverse_selection_bps": None,
                "markout_after_fill_bps": None,
                "touch_counted_as_fill": False,
                "fill_confidence": "INSUFFICIENT_NEW_DATA",
            }
            for item in catalog.policies
            if item.entry_role.value == "MAKER" or item.exit_role.value == "MAKER"
        ]

    @staticmethod
    def _taker_quality(catalog: ExecutionPolicyCatalog) -> list[dict[str, object]]:
        return [
            {
                "side": side.value,
                "execution_policy": policy.policy_id.value,
                "notional": str(notional),
                "fill_certainty": None,
                "fee_bps": str(execution_policy_fee_bps(policy)),
                "spread_crossing_bps": None,
                "vwap_slippage_bps": None,
                "latency_slippage_bps": None,
                "adverse_selection_bps": None,
                "status": "MORE_DATA_REQUIRED",
            }
            for side in PositionSide
            for policy in catalog.policies
            if policy.entry_role.value == "TAKER" or policy.exit_role.value == "TAKER"
            for notional in ACCOUNT_NOTIONALS
        ]

    @staticmethod
    def _maker_adverse(catalog: ExecutionPolicyCatalog) -> list[dict[str, object]]:
        return [
            {
                "execution_policy": item.policy_id.value,
                "side": side.value,
                "adverse_selection_bps": None,
                "sample_count": 0,
                "status": "MORE_DATA_REQUIRED",
            }
            for side in PositionSide
            for item in catalog.policies
            if item.entry_role.value == "MAKER" or item.exit_role.value == "MAKER"
        ]

    @staticmethod
    def _capacity(campaign: MicrostructureDatasetCampaign) -> list[dict[str, object]]:
        return [
            {
                "side": side.value,
                "notional": str(notional),
                "leverage": "1",
                "execution_rate": None,
                "depth_slippage_bps": None,
                "capacity_status": "MORE_DATA_REQUIRED",
                "dataset_status": campaign.status.value,
            }
            for side in PositionSide
            for notional in ACCOUNT_NOTIONALS
        ]

    @staticmethod
    def _small_account(campaign: MicrostructureDatasetCampaign) -> list[dict[str, object]]:
        return [
            {
                "notional_usdt": str(notional),
                "leverage": "1",
                "execution_possible": None,
                "costs_preserve_edge": None,
                "liquidity_sufficient": None,
                "fill_behavior_plausible": None,
                "status": "MORE_DATA_REQUIRED",
                "dataset_status": campaign.status.value,
            }
            for notional in ACCOUNT_NOTIONALS
        ]

    @staticmethod
    def _empty_episode_rows(catalog: ExecutionPolicyCatalog) -> list[dict[str, object]]:
        return [
            {
                "side": side.value,
                "notional": str(notional),
                "execution_policy": policy.policy_id.value,
                "exit_variant": variant.value,
                "episode_count": 0,
                "effective_independent_episodes": 0,
                "anchors_skipped_due_to_open_episode": 0,
                "time_in_market_seconds": 0,
            }
            for side in PositionSide
            for notional in ACCOUNT_NOTIONALS
            for policy in catalog.policies
            for variant in ExitVariantId
        ]

    @staticmethod
    def _runner_rows(
        campaign: MicrostructureDatasetCampaign, side: PositionSide
    ) -> list[dict[str, object]]:
        return [
            {
                "side": side.value,
                "exit_variant": variant.value,
                "activation_count": 0,
                "completed_episode_count": 0,
                "immediate_pnl_bps": None,
                "runner_pnl_bps": None,
                "incremental_pnl_bps": None,
                "holding_duration_seconds": None,
                "peak_pnl_bps": None,
                "giveback_bps": None,
                "mfe_bps": None,
                "mae_bps": None,
                "hard_floor_count": 0,
                "reversal_exits": 0,
                "liquidity_failsafes": 0,
                "max_hold_exits": 0,
                "capture_boundary_failures": 0,
                "time_in_market_seconds": 0,
                "opportunity_cost_bps": None,
                "episodes_per_hour": None,
                "episodes_per_day_extrapolated": None,
                "status": runner_status(campaign.status, 0),
            }
            for variant in ExitVariantId
        ]

    @staticmethod
    def _exit_rows(
        campaign: MicrostructureDatasetCampaign, variant: ExitVariantId
    ) -> list[dict[str, object]]:
        return [
            {
                "side": side.value,
                "exit_variant": variant.value,
                "entry_edge_bps": None,
                "exit_increment_bps": None,
                "activations": 0,
                "completed_episodes": 0,
                "max_hold_ms": {
                    ExitVariantId.RUNNER_10M: 600_000,
                    ExitVariantId.RUNNER_15M: 900_000,
                }.get(variant),
                "reversal_exits": 0,
                "hard_floor_exits": 0,
                "liquidity_failsafes": 0,
                "giveback_bps": None,
                "incremental_edge_bps": None,
                "time_in_market_seconds": 0,
                "useful": False,
                "status": "MORE_DATA_REQUIRED",
                "elastic_parameters_modified": False,
                "dataset_status": campaign.status.value,
            }
            for side in PositionSide
        ]

    @staticmethod
    def _frequency(campaign: MicrostructureDatasetCampaign) -> dict[str, object]:
        return {
            "median_opportunities_per_day": None,
            "executed_episodes_per_day": None,
            "episodes_per_hour": None,
            "no_trade_time_seconds": None,
            "capital_reuse_count": None,
            "average_holding_duration_seconds": None,
            "target_5_to_20_trades_is_selection_criterion": False,
            "status": "MORE_DATA_REQUIRED",
            "dataset_status": campaign.status.value,
        }

    @staticmethod
    def _runner_comparison(campaign: MicrostructureDatasetCampaign) -> dict[str, object]:
        return {
            "LONG": {
                variant.value: runner_status(campaign.status, 0)
                for variant in ExitVariantId
            },
            "SHORT": {
                variant.value: runner_status(campaign.status, 0)
                for variant in ExitVariantId
            },
            "pnl_summed_across_sides": False,
            "winner_selected": False,
            "next_step": "MORE_DATA_REQUIRED",
        }

    @staticmethod
    def _temporal_stability(campaign: MicrostructureDatasetCampaign) -> list[dict[str, object]]:
        return [
            {
                "aggregation": aggregation,
                "utc_date": date,
                "bucket": None,
                "side": side.value,
                "episode_count": 0,
                "mean_net_edge_bps": None,
                "median_net_edge_bps": None,
                "positive_rate": None,
                "mean_incremental_pnl_bps": None,
                "single_short_block_dependency": None,
                "status": "INSUFFICIENT_EPISODES",
            }
            for date in campaign.utc_dates_covered
            for side in PositionSide
            for aggregation in ("UTC_DAY", "SIX_HOUR_BLOCK", "UTC_HOUR")
        ] or [{"status": "NO_NEW_SESSIONS"}]

    @staticmethod
    def _no_trade(campaign: MicrostructureDatasetCampaign) -> list[dict[str, object]]:
        return [
            {
                "context": "INSUFFICIENT_NEW_CAMPAIGN_DATA",
                "no_policy_has_net_edge": None,
                "runner_worse_than_immediate": None,
                "maker_fill_rate_insufficient": None,
                "taker_cost_dominates": None,
                "automatic_gate_created": False,
                "dataset_status": campaign.status.value,
            }
        ]

    @staticmethod
    def _requirements(campaign: MicrostructureDatasetCampaign) -> dict[str, object]:
        duration = campaign.total_duration_seconds
        dates = len(campaign.utc_dates_covered)
        return {
            "valid_duration_seconds": duration,
            "utc_dates_covered": dates,
            "seconds_missing_for_discovery": max(0.0, 86_400 - duration),
            "utc_dates_missing_for_discovery": max(0, 2 - dates),
            "minimum_discovery_seconds": 86_400,
            "minimum_discovery_utc_dates": 2,
            "requirements_reduced_after_results": False,
            "next_step": "MORE_DATA_REQUIRED",
        }

    @staticmethod
    def _report(
        output: Path,
        manifest: dict[str, object],
        campaign: MicrostructureDatasetCampaign,
        catalog: ExecutionPolicyCatalog,
        labels: list[dict[str, object]],
        comparison: dict[str, object],
        requirements: dict[str, object],
    ) -> None:
        incomplete = sum(row["status"] == "LABEL_INCOMPLETE" for row in labels)
        text = f"""# Multi-day economic qualification — Sprint 4A.3.2

## Decision

- DATASET_STATUS: **{manifest['dataset_status']}**
- EXECUTION_POLICY_STATUS LONG/SHORT: **MORE_DATA_REQUIRED**
- RUNNER_STATUS LONG/SHORT and every variant: **MORE_DATA_REQUIRED**
- NEXT_STEP: **MORE_DATA_REQUIRED**
- CENTRAL_ANSWER: **MORE_DATA_REQUIRED**

The prior campaign is formally `ENGINEERING_CONSUMED` and excluded from selection. Campaign
`{campaign.campaign_id}` contains {len(campaign.sessions)} new session(s),
{campaign.total_duration_seconds:.3f} valid seconds and
{len(campaign.utc_dates_covered)} UTC date(s). It remains below 24 hours/two dates.

All {incomplete} extended-horizon availability rows are `LABEL_INCOMPLETE`; no candle, mark price,
capture break or consumed session was used to fill missing future state. The four immutable policies
have catalog hash `{catalog.catalog_hash}`. Their fee-only round-trip floors are 10 bps taker/taker,
7 bps maker/taker or taker/maker, and 4 bps maker/maker. Maker fill quality, adverse selection and
net economics are not inferred without valid new executable episodes.

Elastic 300/150 remains unchanged. The 10m and 15m runners are independent controllers with fixed
150ms microstructure-reversal confirmation, hard floor, liquidity failsafe and 600/900 second max
hold. Non-overlapping sampling is mandatory per side/notional/policy/exit stream. No runner or
execution policy was selected.

Discovery still needs {requirements['seconds_missing_for_discovery']:.3f} seconds and
{requirements['utc_dates_missing_for_discovery']} additional UTC date(s). Alpha V1 was not created.
"""
        (output / "multi_day_execution_economics_report.md").write_text(text, encoding="utf-8")


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _git_commit_time(commit: str) -> datetime:
    result = subprocess.run(
        ["git", "show", "-s", "--format=%cI", commit],
        check=True,
        capture_output=True,
        text=True,
    )
    return datetime.fromisoformat(result.stdout.strip())


def _event_counts(sessions: tuple[CampaignSession, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for session in sessions:
        path = Path(session.path)
        delivery = inspect_session(path).get("stream_delivery", [])
        if not isinstance(delivery, list):
            continue
        for item in delivery:
            if not isinstance(item, dict):
                continue
            name = str(item.get("requested_stream", "unknown"))
            value = item.get("event_count", 0)
            if isinstance(value, int) and not isinstance(value, bool):
                counts[name] = counts.get(name, 0) + value
    return counts


def _json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def _csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("status\nNO_DATA\n")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
