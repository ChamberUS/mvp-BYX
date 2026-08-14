from __future__ import annotations

import csv
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_trader.cli.main import _parser
from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.microstructure.campaign import (
    CampaignSession,
    DatasetSufficiency,
    MicrostructureCampaignBuilder,
    MicrostructureDatasetCampaign,
)
from adaptive_trader.research.multi_day_execution import (
    ACCOUNT_NOTIONALS,
    EXTENDED_HORIZONS_MS,
    LONG_HORIZONS_MS,
    AccessibleEdgeAnswer,
    EpisodeKey,
    ExecutionPolicyId,
    ExitVariantId,
    NonOverlappingExecutionEpisodeSampler,
    QualificationEvidence,
    accessible_edge_answer,
    consumed_campaign_manifest,
    episode_block_bootstrap,
    execution_policy_fee_bps,
    exit_increment_is_useful,
    extended_horizon_availability,
    load_execution_policy_catalog,
    runner_status,
    small_account_feasibility,
    validate_new_campaign,
)
from adaptive_trader.research.multi_day_execution_service import (
    MultiDayExecutionEconomicsService,
)
from tests.microstructure.helpers import at, write_session


def campaign(
    campaign_id: str,
    campaign_hash: str,
    event_hash: str,
    *,
    status: DatasetSufficiency = DatasetSufficiency.ENGINEERING_ONLY,
) -> MicrostructureDatasetCampaign:
    session = CampaignSession(
        session_id=f"{campaign_id}-session",
        path="fixture",
        market=MarketType.USD_M_FUTURES.value,
        symbol="ETHUSDT",
        start=at(0).isoformat(),
        end=at(900_000).isoformat(),
        duration_seconds=900,
        event_count=1,
        event_hashes=(event_hash,),
        quality="CLEAN",
        warnings=(),
        software_commit="fixture",
    )
    return MicrostructureDatasetCampaign(
        campaign_id=campaign_id,
        market=MarketType.USD_M_FUTURES.value,
        symbol="ETHUSDT",
        sessions=(session,),
        total_duration_seconds=900,
        utc_dates_covered=("2026-08-06",),
        event_counts={},
        capture_breaks=(),
        warnings=(),
        software_commits=("fixture",),
        status=status,
        campaign_hash=campaign_hash,
    )


def test_policy_catalog_is_exact_frozen_and_fee_correct() -> None:
    catalog = load_execution_policy_catalog(Path("intraday-execution-policies-v1.toml"))
    assert tuple(item.policy_id for item in catalog.policies) == tuple(ExecutionPolicyId)
    assert catalog.maker_wait_ms == 250
    assert catalog.latency_profile == "NORMAL"
    assert catalog.leverage == Decimal("1")
    assert len(catalog.catalog_hash) == 64
    assert [execution_policy_fee_bps(item) for item in catalog.policies] == [
        Decimal("10"),
        Decimal("7"),
        Decimal("7"),
        Decimal("4"),
    ]
    assert ACCOUNT_NOTIONALS == (Decimal("100"), Decimal("500"), Decimal("1000"))


@pytest.mark.parametrize("horizon", LONG_HORIZONS_MS)
def test_extended_horizons_and_capture_boundary(horizon: int) -> None:
    complete = extended_horizon_availability(
        session_id="session",
        side=PositionSide.LONG,
        anchor_time=at(0),
        horizon_ms=horizon,
        session_end=at(horizon),
    )
    incomplete = extended_horizon_availability(
        session_id="session",
        side=PositionSide.SHORT,
        anchor_time=at(1),
        horizon_ms=horizon,
        session_end=at(horizon),
    )
    assert complete.status.value == "COMPLETE"
    assert incomplete.status.value == "LABEL_INCOMPLETE"
    assert incomplete.reason == "CAPTURE_BOUNDARY"
    assert EXTENDED_HORIZONS_MS[-4:] == LONG_HORIZONS_MS


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("feed_integrity_valid", "FEED_INVALID"),
        ("book_valid", "BOOK_INVALID"),
        ("executable_future_state", "NO_EXECUTABLE_FUTURE_STATE"),
    ],
)
def test_extended_label_rejects_invalid_future(field: str, reason: str) -> None:
    values = {
        "feed_integrity_valid": True,
        "book_valid": True,
        "executable_future_state": True,
    }
    values[field] = False
    result = extended_horizon_availability(
        session_id="session",
        side=PositionSide.LONG,
        anchor_time=at(0),
        horizon_ms=120_000,
        session_end=at(900_000),
        **values,
    )
    assert result.status.value == "LABEL_INCOMPLETE"
    assert result.reason == reason


def test_non_overlapping_episodes_are_separate_by_full_stream_key() -> None:
    sampler = NonOverlappingExecutionEpisodeSampler()
    base = EpisodeKey(
        PositionSide.LONG,
        Decimal("100"),
        ExecutionPolicyId.TAKER_TAKER,
        ExitVariantId.RUNNER_10M,
    )
    assert sampler.consider(key=base, anchor_time=at(0), exit_time=at(600_000)) is not None
    assert sampler.consider(key=base, anchor_time=at(250), exit_time=at(600_250)) is None
    assert sampler.skipped(base) == 1
    assert sampler.consider(key=base, anchor_time=at(600_000), exit_time=at(600_250))

    for changed in (
        replace(base, side=PositionSide.SHORT),
        replace(base, notional=Decimal("500")),
        replace(base, execution_policy=ExecutionPolicyId.MAKER_TAKER),
        replace(base, exit_variant=ExitVariantId.RUNNER_15M),
    ):
        assert sampler.consider(key=changed, anchor_time=at(250), exit_time=at(500))
    assert sampler.summary(base)["effective_independent_episodes"] == 2


def test_consumed_campaign_cannot_enter_new_discovery() -> None:
    old = campaign("ethusdt-futures-intraday-v1", "a" * 64, "b" * 64)
    consumed = consumed_campaign_manifest(old, commit="c" * 40)
    assert consumed["status"] == "ENGINEERING_CONSUMED"
    assert consumed["eligible_for_new_discovery"] is False

    valid = campaign(
        "ethusdt-futures-intraday-discovery-v1", "d" * 64, "e" * 64
    )
    validate_new_campaign(valid, consumed)
    mixed = campaign(
        "ethusdt-futures-intraday-discovery-v1", "f" * 64, "b" * 64
    )
    with pytest.raises(ValueError, match="consumed"):
        validate_new_campaign(mixed, consumed)


def test_30_minute_episode_bootstrap_is_deterministic() -> None:
    episodes = (
        (at(0), Decimal("1")),
        (at(1_800_000), Decimal("-1")),
        (at(3_600_000), Decimal("2")),
    )
    first = episode_block_bootstrap(episodes)
    second = episode_block_bootstrap(episodes)
    assert first == second
    assert first["block_size_seconds"] == 1800
    assert first["iterations"] == 2000
    assert first["seed"] == 42
    assert first["status"] == "OK"
    assert episode_block_bootstrap(episodes[:1])["status"] == "INSUFFICIENT_SAMPLE"


def test_engineering_dataset_never_classifies_runner_helpful() -> None:
    assert runner_status(DatasetSufficiency.ENGINEERING_ONLY, 1000) == "MORE_DATA_REQUIRED"
    assert runner_status(DatasetSufficiency.DISCOVERY_READY, 2) == "INSUFFICIENT_SAMPLE"


def test_yes_no_and_more_data_qualification_are_unambiguous() -> None:
    passing = QualificationEvidence(
        dataset_status=DatasetSufficiency.DISCOVERY_READY,
        utc_dates=2,
        quality_sufficient=True,
        independent_episodes_sufficient=True,
        maker_observations_sufficient=True,
        all_structures_evaluable=True,
        accessible_candidate_count=1,
        normal_latency_positive=True,
        confirmation_same_direction=True,
        temporally_distributed=True,
        bootstrap_supportive=True,
    )
    assert accessible_edge_answer(passing) is AccessibleEdgeAnswer.YES
    assert (
        accessible_edge_answer(replace(passing, accessible_candidate_count=0))
        is AccessibleEdgeAnswer.NO
    )
    assert (
        accessible_edge_answer(
            replace(passing, dataset_status=DatasetSufficiency.EXPLORATORY, utc_dates=1)
        )
        is AccessibleEdgeAnswer.MORE_DATA_REQUIRED
    )
    assert (
        accessible_edge_answer(replace(passing, normal_latency_positive=False))
        is AccessibleEdgeAnswer.NO
    )
    assert (
        accessible_edge_answer(replace(passing, temporally_distributed=False))
        is AccessibleEdgeAnswer.NO
    )


def test_small_account_and_runner_exit_gates_do_not_invent_edge() -> None:
    assert (
        small_account_feasibility(
            dataset_ready=False,
            execution_possible=True,
            costs_destroy_edge=False,
            liquidity_sufficient=True,
            fills_plausible=True,
        )
        == "MORE_DATA_REQUIRED"
    )
    assert (
        small_account_feasibility(
            dataset_ready=True,
            execution_possible=True,
            costs_destroy_edge=False,
            liquidity_sufficient=True,
            fills_plausible=True,
        )
        == "SMALL_ACCOUNT_FEASIBLE"
    )
    assert not exit_increment_is_useful(
        entry_edge_bps=Decimal("-1"),
        exit_increment_bps=Decimal("5"),
        temporally_stable=True,
    )
    assert exit_increment_is_useful(
        entry_edge_bps=Decimal("1"),
        exit_increment_bps=Decimal("0.5"),
        temporally_stable=True,
    )


def test_multi_day_cli_has_only_frozen_catalog_options() -> None:
    args = _parser().parse_args(
        [
            "research",
            "execution",
            "build-multi-day-economics",
            "--campaign",
            "ethusdt-futures-intraday-discovery-v1",
            "--policies",
            "taker-taker,maker-taker,taker-maker,maker-maker",
            "--exits",
            "immediate,elastic-300-150,runner-10m,runner-15m",
            "--notionals",
            "100,500,1000",
            "--latency-profile",
            "normal",
            "--output-dir",
            "reports/research",
            "--yes",
        ]
    )
    assert args.execution_research_command == "build-multi-day-economics"
    assert args.latency_profile == "normal"
    assert not hasattr(args, "api_key")


def test_multi_day_service_writes_complete_more_data_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_session = write_session(
        tmp_path / "old", market=MarketType.USD_M_FUTURES
    )
    new_session = write_session(
        tmp_path / "new",
        market=MarketType.USD_M_FUTURES,
        rotate_event_count=1,
    )
    builder = MicrostructureCampaignBuilder()
    old = builder.build("ethusdt-futures-intraday-v1", (old_session,))
    new = builder.build(
        "ethusdt-futures-intraday-discovery-v1", (new_session,)
    )
    old_manifest = builder.write(old, tmp_path / "old-campaign.json")
    new_manifest = builder.write(new, tmp_path / "new-campaign.json")
    monkeypatch.setattr(
        "adaptive_trader.research.multi_day_execution_service._git_commit_time",
        lambda _commit: at(-1),
    )

    output = MultiDayExecutionEconomicsService().build(
        campaign_manifest=new_manifest,
        consumed_campaign_manifest_path=old_manifest,
        policy_catalog_path=Path("intraday-execution-policies-v1.toml"),
        output_dir=tmp_path / "reports",
    )
    shown = MultiDayExecutionEconomicsService.inspect(output)

    assert shown["required_artifacts_present"] is True
    assert shown["dataset_status"] == "ENGINEERING_ONLY"
    assert shown["next_step"] == "MORE_DATA_REQUIRED"
    assert (output / "multi_day_execution_economics_report.md").is_file()
    answer = json.loads((output / "accessible_intraday_edge_answer.json").read_text())
    assert answer["answer"] == "MORE_DATA_REQUIRED"
    assert answer["best_supported_side"] is None
    assert json.loads((output / "holdout_lock.json").read_text())["status"] == "LOCKED"
    assert (output / "multi_day_economic_qualification_report.md").is_file()
    required = {
        "experiment_manifest.json",
        "provenance_audit.json",
        "campaign_manifest.json",
        "session_admission.csv",
        "dataset_quality.json",
        "campaign_progress.json",
        "execution_policy_economics.csv",
        "maker_fill_quality.csv",
        "maker_adverse_selection.csv",
        "taker_execution_quality.csv",
        "long_economics.csv",
        "short_economics.csv",
        "small_account_feasibility.csv",
        "temporal_stability.csv",
        "block_bootstrap.json",
        "frequency_analysis.json",
        "no_trade_contexts.csv",
        "runner_10m.csv",
        "runner_15m.csv",
        "elastic_300_150.csv",
        "discovery_confirmation.json",
        "holdout_lock.json",
        "accessible_intraday_edge_answer.json",
        "multi_day_economic_qualification_report.md",
    }
    assert required <= {item.name for item in output.iterdir()}
    with (output / "maker_fill_quality.csv").open(newline="") as handle:
        maker_rows = list(csv.DictReader(handle))
    assert maker_rows and all(row["touch_counted_as_fill"] == "False" for row in maker_rows)
