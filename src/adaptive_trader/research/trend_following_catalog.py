"""Immutable pre-registration for the Sprint 3C.1 trend-following family."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from adaptive_trader.research.datasets import canonical_hash
from adaptive_trader.research.periods import ConsumedTestError

TREND_FOLLOWING_CATALOG_FILE = Path("trend-following-hypotheses-v1.toml")

DEVELOPMENT_START = datetime(2022, 1, 1, tzinfo=UTC)
DEVELOPMENT_END = datetime(2023, 12, 31, 23, tzinfo=UTC)
VALIDATION_START = datetime(2024, 1, 1, tzinfo=UTC)
VALIDATION_END = datetime(2024, 12, 31, 23, tzinfo=UTC)
CONSUMED_START = datetime(2025, 1, 1, tzinfo=UTC)
CONSUMED_END = datetime(2026, 7, 1, tzinfo=UTC)

EXACT_CATALOG_KEYS = (
    "tf_donchian_20_fixed_risk",
    "tf_donchian_10_fixed_risk",
    "tf_donchian_20_defensive_risk",
    "tf_donchian_10_defensive_risk",
    "tf_long_only_donchian_20",
    "tf_short_only_donchian_20",
)
EXACT_VARIANT_IDS = (
    "TF_DONCHIAN_20_FIXED_RISK",
    "TF_DONCHIAN_10_FIXED_RISK",
    "TF_DONCHIAN_20_DEFENSIVE_RISK",
    "TF_DONCHIAN_10_DEFENSIVE_RISK",
    "TF_LONG_ONLY_DONCHIAN_20",
    "TF_SHORT_ONLY_DONCHIAN_20",
)


class TrendFollowingDirectionScope(StrEnum):
    MARKET_DEFAULT = "MARKET_DEFAULT"
    LONG_ONLY = "LONG_ONLY"
    SHORT_ONLY = "SHORT_ONLY"


class TrendFollowingRiskModel(StrEnum):
    FIXED = "FIXED"
    DEFENSIVE = "DEFENSIVE"


@dataclass(frozen=True, slots=True)
class TrendFollowingMarketGroup:
    market: str
    mode: str

    def __post_init__(self) -> None:
        if (self.market, self.mode) not in {
            ("SPOT", "LONG"),
            ("FUTURES", "LONG"),
            ("FUTURES", "SHORT"),
            ("FUTURES", "LONG_SHORT"),
        }:
            raise ValueError(f"unsupported trend-following market group: {self.market}/{self.mode}")

    @property
    def key(self) -> str:
        return f"{self.market}/{self.mode}"


SPOT_LONG = TrendFollowingMarketGroup("SPOT", "LONG")
FUTURES_LONG = TrendFollowingMarketGroup("FUTURES", "LONG")
FUTURES_SHORT = TrendFollowingMarketGroup("FUTURES", "SHORT")
FUTURES_LONG_SHORT = TrendFollowingMarketGroup("FUTURES", "LONG_SHORT")
EXACT_MARKET_GROUPS = (
    SPOT_LONG,
    FUTURES_LONG,
    FUTURES_SHORT,
    FUTURES_LONG_SHORT,
)


@dataclass(frozen=True, slots=True)
class TrendFollowingHypothesis:
    variant_id: str
    catalog_key: str
    sma_period_days: int
    entry_period_days: int
    exit_period_days: int
    direction_scope: TrendFollowingDirectionScope
    risk_model: TrendFollowingRiskModel
    normal_risk_percent: Decimal
    defensive_risk_percent: Decimal
    defensive_activation_losses: int
    defensive_recovery_rule: str
    complexity_rank: int
    catalog_order: int

    @property
    def defensive_risk_enabled(self) -> bool:
        return self.risk_model is TrendFollowingRiskModel.DEFENSIVE

    def is_applicable_to(self, group: TrendFollowingMarketGroup) -> bool:
        if self.direction_scope is TrendFollowingDirectionScope.MARKET_DEFAULT:
            return True
        if self.direction_scope is TrendFollowingDirectionScope.LONG_ONLY:
            return group in {SPOT_LONG, FUTURES_LONG}
        return group == FUTURES_SHORT


@dataclass(frozen=True, slots=True)
class TrendFollowingCatalog:
    version: int
    path: Path
    hypotheses: tuple[TrendFollowingHypothesis, ...]
    canonical_hash: str
    file_sha256: str

    def by_id(self, variant_id: str) -> TrendFollowingHypothesis:
        match = next(
            (hypothesis for hypothesis in self.hypotheses if hypothesis.variant_id == variant_id),
            None,
        )
        if match is None:
            raise ValueError(f"unknown trend-following hypothesis: {variant_id}")
        return match

    def applicable_to(
        self,
        group: TrendFollowingMarketGroup,
    ) -> tuple[TrendFollowingHypothesis, ...]:
        return tuple(
            hypothesis for hypothesis in self.hypotheses if hypothesis.is_applicable_to(group)
        )


@dataclass(frozen=True, slots=True)
class TrendFollowingPeriods:
    development_start: datetime
    development_end: datetime
    validation_start: datetime
    validation_end: datetime
    consumed_start: datetime
    consumed_end: datetime

    def __post_init__(self) -> None:
        values = (
            self.development_start,
            self.development_end,
            self.validation_start,
            self.validation_end,
            self.consumed_start,
            self.consumed_end,
        )
        if any(value.tzinfo is None or value.utcoffset() is None for value in values):
            raise ValueError("trend-following periods must be timezone-aware")
        if any(value.utcoffset() != UTC.utcoffset(value) for value in values):
            raise ValueError("trend-following periods must use UTC")
        if not (
            self.development_start
            <= self.development_end
            < self.validation_start
            <= self.validation_end
            < self.consumed_start
            <= self.consumed_end
        ):
            raise ValueError("trend-following periods must be chronological and non-overlapping")

    @classmethod
    def pre_registered(cls) -> TrendFollowingPeriods:
        return cls(
            development_start=DEVELOPMENT_START,
            development_end=DEVELOPMENT_END,
            validation_start=VALIDATION_START,
            validation_end=VALIDATION_END,
            consumed_start=CONSUMED_START,
            consumed_end=CONSUMED_END,
        )

    def assert_pre_registered(self) -> None:
        if self != self.pre_registered():
            raise ValueError("trend-following periods differ from Sprint 3C.1 pre-registration")

    def assert_research_range(
        self,
        start: datetime,
        end: datetime,
        operation: str,
    ) -> None:
        _assert_utc(start, "start")
        _assert_utc(end, "end")
        if start > end:
            raise ValueError(f"{operation} start must not exceed end")
        if start >= self.consumed_start or end >= self.consumed_start:
            raise ConsumedTestError(f"{operation} cannot load or use consumed 2025-2026 data")
        if start < self.development_start or end > self.validation_end:
            raise ValueError(f"{operation} must remain inside pre-registered 2022-2024 periods")

    def assert_development_range(self, start: datetime, end: datetime) -> None:
        self.assert_research_range(start, end, "development")
        if start < self.development_start or end > self.development_end:
            raise ValueError("development range must remain inside 2022-2023")

    def assert_validation_range(self, start: datetime, end: datetime) -> None:
        self.assert_research_range(start, end, "validation")
        if start < self.validation_start or end > self.validation_end:
            raise ValueError("validation range must remain inside locked 2024")

    def as_dict(self) -> dict[str, str]:
        return {
            "development_start": self.development_start.isoformat(),
            "development_end": self.development_end.isoformat(),
            "validation_start": self.validation_start.isoformat(),
            "validation_end": self.validation_end.isoformat(),
            "consumed_start": self.consumed_start.isoformat(),
            "consumed_end": self.consumed_end.isoformat(),
        }


def build_market_groups(
    *,
    markets: tuple[str, ...] = ("spot", "futures"),
    futures_modes: tuple[str, ...] = ("long", "short", "long-short"),
) -> tuple[TrendFollowingMarketGroup, ...]:
    if not markets or len(markets) != len(set(markets)):
        raise ValueError("markets must be a non-empty unique list")
    if any(market not in {"spot", "futures"} for market in markets):
        raise ValueError("markets accepts only spot,futures")
    if len(futures_modes) != len(set(futures_modes)):
        raise ValueError("futures modes must be unique")
    if any(mode not in {"long", "short", "long-short"} for mode in futures_modes):
        raise ValueError("futures modes accepts only long,short,long-short")
    if "futures" in markets and not futures_modes:
        raise ValueError("Futures trend-following research requires at least one mode")
    groups: list[TrendFollowingMarketGroup] = []
    if "spot" in markets:
        groups.append(SPOT_LONG)
    if "futures" in markets:
        by_mode = {
            "long": FUTURES_LONG,
            "short": FUTURES_SHORT,
            "long-short": FUTURES_LONG_SHORT,
        }
        groups.extend(by_mode[mode] for mode in futures_modes)
    return tuple(groups)


def load_trend_following_catalog(
    path: Path = TREND_FOLLOWING_CATALOG_FILE,
) -> TrendFollowingCatalog:
    content = path.read_bytes()
    raw = tomllib.loads(content.decode("utf-8"))
    metadata = raw.get("metadata")
    hypotheses_raw = raw.get("hypotheses")
    if not isinstance(metadata, dict) or not isinstance(hypotheses_raw, dict):
        raise ValueError("trend-following catalog requires metadata and hypotheses")
    expected_metadata: dict[str, Any] = {
        "version": 1,
        "symbol": "ETHUSDT",
        "source_interval": "1h",
        "strategy_interval": "1d",
        "development_start": "2022-01-01T00:00:00Z",
        "development_end": "2023-12-31T23:00:00Z",
        "validation_start": "2024-01-01T00:00:00Z",
        "validation_end": "2024-12-31T23:00:00Z",
        "consumed_start": "2025-01-01T00:00:00Z",
        "consumed_end": "2026-07-01T00:00:00Z",
        "selection_metric": "median_walk_forward_net_return",
        "leverage": "1",
    }
    if metadata != expected_metadata:
        raise ValueError("trend-following catalog metadata differs from pre-registration")
    if tuple(hypotheses_raw) != EXACT_CATALOG_KEYS:
        raise ValueError("trend-following catalog must contain the six exact ordered variants")

    expected_values = (
        (
            EXACT_VARIANT_IDS[0],
            200,
            20,
            20,
            "MARKET_DEFAULT",
            "FIXED",
            "1.0",
            "0.5",
            3,
            "EQUITY_RECOVERY_TARGET",
            1,
        ),
        (
            EXACT_VARIANT_IDS[1],
            200,
            20,
            10,
            "MARKET_DEFAULT",
            "FIXED",
            "1.0",
            "0.5",
            3,
            "EQUITY_RECOVERY_TARGET",
            1,
        ),
        (
            EXACT_VARIANT_IDS[2],
            200,
            20,
            20,
            "MARKET_DEFAULT",
            "DEFENSIVE",
            "1.0",
            "0.5",
            3,
            "EQUITY_RECOVERY_TARGET",
            2,
        ),
        (
            EXACT_VARIANT_IDS[3],
            200,
            20,
            10,
            "MARKET_DEFAULT",
            "DEFENSIVE",
            "1.0",
            "0.5",
            3,
            "EQUITY_RECOVERY_TARGET",
            2,
        ),
        (
            EXACT_VARIANT_IDS[4],
            200,
            20,
            20,
            "LONG_ONLY",
            "FIXED",
            "1.0",
            "0.5",
            3,
            "EQUITY_RECOVERY_TARGET",
            1,
        ),
        (
            EXACT_VARIANT_IDS[5],
            200,
            20,
            20,
            "SHORT_ONLY",
            "FIXED",
            "1.0",
            "0.5",
            3,
            "EQUITY_RECOVERY_TARGET",
            1,
        ),
    )
    field_names = (
        "variant_id",
        "sma_period_days",
        "entry_period_days",
        "exit_period_days",
        "direction_scope",
        "risk_model",
        "normal_risk_percent",
        "defensive_risk_percent",
        "defensive_activation_losses",
        "defensive_recovery_rule",
        "complexity_rank",
    )
    hypotheses: list[TrendFollowingHypothesis] = []
    for order, (key, expected) in enumerate(zip(EXACT_CATALOG_KEYS, expected_values, strict=True)):
        section = hypotheses_raw.get(key)
        if not isinstance(section, dict):
            raise ValueError(f"invalid trend-following hypothesis section: {key}")
        observed = tuple(section.get(name) for name in field_names)
        if tuple(section) != field_names or observed != expected:
            raise ValueError(f"trend-following hypothesis {key} differs from pre-registration")
        hypotheses.append(
            TrendFollowingHypothesis(
                variant_id=str(section["variant_id"]),
                catalog_key=key,
                sma_period_days=int(section["sma_period_days"]),
                entry_period_days=int(section["entry_period_days"]),
                exit_period_days=int(section["exit_period_days"]),
                direction_scope=TrendFollowingDirectionScope(str(section["direction_scope"])),
                risk_model=TrendFollowingRiskModel(str(section["risk_model"])),
                normal_risk_percent=Decimal(str(section["normal_risk_percent"])),
                defensive_risk_percent=Decimal(str(section["defensive_risk_percent"])),
                defensive_activation_losses=int(section["defensive_activation_losses"]),
                defensive_recovery_rule=str(section["defensive_recovery_rule"]),
                complexity_rank=int(section["complexity_rank"]),
                catalog_order=order,
            )
        )
    normalized = {
        "metadata": expected_metadata,
        "hypotheses": [asdict(hypothesis) for hypothesis in hypotheses],
    }
    return TrendFollowingCatalog(
        version=1,
        path=path,
        hypotheses=tuple(hypotheses),
        canonical_hash=canonical_hash(normalized),
        file_sha256=hashlib.sha256(content).hexdigest(),
    )


def _assert_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must use UTC")
