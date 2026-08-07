"""Pre-registered multi-day execution economics primitives."""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.execution.fees import FeeConfig, FeeModel
from adaptive_trader.execution.models import LiquidityRole
from adaptive_trader.microstructure.campaign import (
    DatasetSufficiency,
    MicrostructureDatasetCampaign,
)

EXTENDED_HORIZONS_MS = (
    250,
    500,
    1_000,
    3_000,
    5_000,
    15_000,
    30_000,
    60_000,
    120_000,
    300_000,
    600_000,
    900_000,
)
LONG_HORIZONS_MS = (120_000, 300_000, 600_000, 900_000)
ACCOUNT_NOTIONALS = (Decimal("100"), Decimal("500"), Decimal("1000"))


class ExecutionPolicyId(StrEnum):
    TAKER_TAKER = "TAKER_TAKER_V1"
    MAKER_TAKER = "MAKER_TAKER_V1"
    TAKER_MAKER = "TAKER_MAKER_V1"
    MAKER_MAKER = "MAKER_MAKER_V1"


class ExitVariantId(StrEnum):
    IMMEDIATE = "IMMEDIATE_PROFIT_EXIT"
    ELASTIC = "ELASTIC_300_150_V0"
    RUNNER_10M = "MULTI_MINUTE_RUNNER_10M_V0"
    RUNNER_15M = "MULTI_MINUTE_RUNNER_15M_V0"


class ExtendedLabelStatus(StrEnum):
    COMPLETE = "COMPLETE"
    LABEL_INCOMPLETE = "LABEL_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class ExecutionPolicyDefinition:
    policy_id: ExecutionPolicyId
    entry: str
    exit: str

    @property
    def entry_role(self) -> LiquidityRole:
        return LiquidityRole.MAKER if self.entry == "MAKER_FIRST_V0" else LiquidityRole.TAKER

    @property
    def exit_role(self) -> LiquidityRole:
        return LiquidityRole.MAKER if self.exit == "MAKER_FIRST_V0" else LiquidityRole.TAKER


@dataclass(frozen=True, slots=True)
class ExecutionPolicyCatalog:
    catalog_id: str
    policies: tuple[ExecutionPolicyDefinition, ...]
    maker_wait_ms: int
    queue_model: str
    fallback_behavior: str
    maximum_slippage_bps: Decimal
    latency_profile: str
    fee_profile: str
    cancel_outbound_latency_ms: int
    cancel_processing_latency_ms: int
    leverage: Decimal
    catalog_hash: str

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "policies": [
                {"id": item.policy_id.value, "entry": item.entry, "exit": item.exit}
                for item in self.policies
            ],
            "maximum_slippage_bps": str(self.maximum_slippage_bps),
            "leverage": str(self.leverage),
        }


def load_execution_policy_catalog(path: Path) -> ExecutionPolicyCatalog:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    policy_rows = raw.get("policies")
    if not isinstance(policy_rows, list):
        raise ValueError("execution policy catalog has no policies")
    policies = tuple(
        ExecutionPolicyDefinition(
            policy_id=ExecutionPolicyId(str(item["id"])),
            entry=str(item["entry"]),
            exit=str(item["exit"]),
        )
        for item in policy_rows
        if isinstance(item, dict)
    )
    if tuple(item.policy_id for item in policies) != tuple(ExecutionPolicyId):
        raise ValueError("execution policy catalog must contain exactly the four V1 policies")
    if int(raw["maker_wait_ms"]) != 250 or str(raw["latency_profile"]) != "NORMAL":
        raise ValueError("MakerFirst V0 parameters cannot be changed")
    if Decimal(str(raw["leverage"])) != Decimal("1"):
        raise ValueError("execution economics is locked to leverage 1x")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ExecutionPolicyCatalog(
        catalog_id=str(raw["catalog_id"]),
        policies=policies,
        maker_wait_ms=int(raw["maker_wait_ms"]),
        queue_model=str(raw["queue_model"]),
        fallback_behavior=str(raw["fallback_behavior"]),
        maximum_slippage_bps=Decimal(str(raw["maximum_slippage_bps"])),
        latency_profile=str(raw["latency_profile"]),
        fee_profile=str(raw["fee_profile"]),
        cancel_outbound_latency_ms=int(raw["cancel_outbound_latency_ms"]),
        cancel_processing_latency_ms=int(raw["cancel_processing_latency_ms"]),
        leverage=Decimal(str(raw["leverage"])),
        catalog_hash=digest,
    )


def execution_policy_fee_bps(
    policy: ExecutionPolicyDefinition, fee_config: FeeConfig | None = None
) -> Decimal:
    fees = FeeModel(fee_config)
    from adaptive_trader.domain.market import MarketType

    return Decimal("10000") * (
        fees.rate(MarketType.USD_M_FUTURES, policy.entry_role)
        + fees.rate(MarketType.USD_M_FUTURES, policy.exit_role)
    )


@dataclass(frozen=True, slots=True)
class ExtendedHorizonLabelAvailability:
    session_id: str
    side: PositionSide
    anchor_time: datetime
    horizon_ms: int
    session_end: datetime
    status: ExtendedLabelStatus
    reason: str


def extended_horizon_availability(
    *,
    session_id: str,
    side: PositionSide,
    anchor_time: datetime,
    horizon_ms: int,
    session_end: datetime,
    feed_integrity_valid: bool = True,
    book_valid: bool = True,
    executable_future_state: bool = True,
) -> ExtendedHorizonLabelAvailability:
    if horizon_ms not in EXTENDED_HORIZONS_MS:
        raise ValueError("horizon is not in the pre-registered extended catalog")
    reason = "COMPLETE"
    if anchor_time.timestamp() + horizon_ms / 1000 > session_end.timestamp():
        reason = "CAPTURE_BOUNDARY"
    elif not feed_integrity_valid:
        reason = "FEED_INVALID"
    elif not book_valid:
        reason = "BOOK_INVALID"
    elif not executable_future_state:
        reason = "NO_EXECUTABLE_FUTURE_STATE"
    return ExtendedHorizonLabelAvailability(
        session_id=session_id,
        side=side,
        anchor_time=anchor_time,
        horizon_ms=horizon_ms,
        session_end=session_end,
        status=(
            ExtendedLabelStatus.COMPLETE
            if reason == "COMPLETE"
            else ExtendedLabelStatus.LABEL_INCOMPLETE
        ),
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class EpisodeKey:
    side: PositionSide
    notional: Decimal
    execution_policy: ExecutionPolicyId
    exit_variant: ExitVariantId


@dataclass(frozen=True, slots=True)
class ExecutionEpisode:
    episode_id: str
    key: EpisodeKey
    opened_at: datetime
    closed_at: datetime

    @property
    def holding_seconds(self) -> float:
        return (self.closed_at - self.opened_at).total_seconds()


class NonOverlappingExecutionEpisodeSampler:
    """Allow one hypothetical position at a time for each independent stream key."""

    def __init__(self) -> None:
        self._open_until: dict[EpisodeKey, datetime] = {}
        self._episodes: list[ExecutionEpisode] = []
        self._skipped: dict[EpisodeKey, int] = {}

    def consider(
        self, *, key: EpisodeKey, anchor_time: datetime, exit_time: datetime
    ) -> ExecutionEpisode | None:
        if exit_time <= anchor_time:
            raise ValueError("episode exit must follow entry")
        if anchor_time.tzinfo is None or exit_time.tzinfo is None:
            raise ValueError("episode timestamps must be timezone-aware")
        if anchor_time < self._open_until.get(key, anchor_time):
            self._skipped[key] = self._skipped.get(key, 0) + 1
            return None
        identifier = hashlib.sha256(
            f"{key}|{anchor_time.isoformat()}|{exit_time.isoformat()}".encode()
        ).hexdigest()
        episode = ExecutionEpisode(identifier, key, anchor_time, exit_time)
        self._open_until[key] = exit_time
        self._episodes.append(episode)
        return episode

    @property
    def episodes(self) -> tuple[ExecutionEpisode, ...]:
        return tuple(self._episodes)

    def skipped(self, key: EpisodeKey) -> int:
        return self._skipped.get(key, 0)

    def summary(self, key: EpisodeKey) -> dict[str, object]:
        episodes = [item for item in self._episodes if item.key == key]
        return {
            "side": key.side.value,
            "notional": str(key.notional),
            "execution_policy": key.execution_policy.value,
            "exit_variant": key.exit_variant.value,
            "episode_count": len(episodes),
            "effective_independent_episodes": len(episodes),
            "anchors_skipped_due_to_open_episode": self.skipped(key),
            "time_in_market_seconds": sum(item.holding_seconds for item in episodes),
        }


def consumed_campaign_manifest(
    campaign: MicrostructureDatasetCampaign, *, commit: str
) -> dict[str, object]:
    return {
        "status": "ENGINEERING_CONSUMED",
        "campaign_id": campaign.campaign_id,
        "campaign_hash": campaign.campaign_hash,
        "session_event_hashes": {
            session.session_id: list(session.event_hashes) for session in campaign.sessions
        },
        "period": {
            "start": campaign.sessions[0].start,
            "end": campaign.sessions[-1].end,
            "duration_seconds": campaign.total_duration_seconds,
            "utc_dates": list(campaign.utc_dates_covered),
        },
        "consumed_at_commit": commit,
        "reason": "Used for Sprint 4A.3 engineering and hypothesis formation",
        "allowed_uses": [
            "REGRESSION_TEST",
            "DOCUMENTATION",
            "HISTORICAL_COMPARISON",
            "MECHANICAL_VERIFICATION",
        ],
        "prohibited_uses": [
            "RUNNER_SELECTION",
            "EXECUTION_POLICY_SELECTION",
            "HORIZON_SELECTION",
            "FEATURE_SELECTION",
            "ALPHA_V1",
        ],
        "eligible_for_new_discovery": False,
    }


def validate_new_campaign(
    campaign: MicrostructureDatasetCampaign,
    consumed: dict[str, object],
    *,
    required_campaign_id: str = "ethusdt-futures-intraday-discovery-v1",
    minimum_session_start: datetime | None = None,
) -> None:
    if campaign.campaign_id != required_campaign_id:
        raise ValueError("multi-day economics requires the new discovery campaign")
    if campaign.campaign_hash == consumed["campaign_hash"]:
        raise ValueError("consumed engineering campaign cannot enter new discovery")
    if campaign.market != "USD_M_FUTURES" or campaign.symbol != "ETHUSDT":
        raise ValueError("new discovery campaign must be ETHUSDT USD-M Futures")
    if minimum_session_start is not None and any(
        datetime.fromisoformat(session.start) <= minimum_session_start
        for session in campaign.sessions
    ):
        raise ValueError("new discovery sessions must be recorded after the baseline commit")
    raw_hashes = consumed["session_event_hashes"]
    if not isinstance(raw_hashes, dict):
        raise ValueError("consumed campaign session hashes are invalid")
    old_hashes = {
        str(value)
        for values in raw_hashes.values()
        if isinstance(values, list)
        for value in values
    }
    if any(value in old_hashes for session in campaign.sessions for value in session.event_hashes):
        raise ValueError("new campaign contains a consumed engineering session")


def episode_block_bootstrap(
    episodes: tuple[tuple[datetime, Decimal], ...], *, iterations: int = 2000, seed: int = 42
) -> dict[str, object]:
    blocks: dict[int, list[float]] = {}
    for timestamp, value in episodes:
        block = int(timestamp.timestamp()) // 1800
        blocks.setdefault(block, []).append(float(value))
    values = tuple(blocks.values())
    if len(values) < 2:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "block_size_seconds": 1800,
            "iterations": iterations,
            "seed": seed,
            "block_count": len(values),
            "mean_incremental_ci95": None,
            "median_incremental_ci95": None,
            "positive_episode_fraction_ci95": None,
        }
    rng = random.Random(seed)
    means: list[float] = []
    medians: list[float] = []
    positives: list[float] = []
    for _ in range(iterations):
        sample = [item for _ in values for item in values[rng.randrange(len(values))]]
        means.append(statistics.fmean(sample))
        medians.append(statistics.median(sample))
        positives.append(sum(item > 0 for item in sample) / len(sample))
    return {
        "status": "OK",
        "block_size_seconds": 1800,
        "iterations": iterations,
        "seed": seed,
        "block_count": len(values),
        "mean_incremental_ci95": [_quantile(means, 0.025), _quantile(means, 0.975)],
        "median_incremental_ci95": [_quantile(medians, 0.025), _quantile(medians, 0.975)],
        "positive_episode_fraction_ci95": [
            _quantile(positives, 0.025),
            _quantile(positives, 0.975),
        ],
    }


def runner_status(dataset_status: DatasetSufficiency, episode_count: int) -> str:
    if dataset_status not in {
        DatasetSufficiency.DISCOVERY_READY,
        DatasetSufficiency.CONFIRMATION_READY,
    }:
        return "MORE_DATA_REQUIRED"
    return "INSUFFICIENT_SAMPLE" if episode_count < 30 else "MIXED"


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def stable_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
