"""Immutable pre-registration for the pullback-continuation research family."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from adaptive_trader.research.datasets import canonical_hash

CATALOG_FILE = Path("pullback-hypotheses-v1.toml")

DEVELOPMENT_START = datetime(2022, 1, 1, tzinfo=UTC)
DEVELOPMENT_END = datetime(2023, 12, 31, 23, tzinfo=UTC)
VALIDATION_START = datetime(2024, 1, 1, tzinfo=UTC)
VALIDATION_END = datetime(2024, 12, 31, 23, tzinfo=UTC)
CONSUMED_START = datetime(2025, 1, 1, tzinfo=UTC)
CONSUMED_END = datetime(2026, 7, 1, tzinfo=UTC)
FUTURE_HOLDOUT_AFTER = CONSUMED_END

EXACT_CATALOG_KEYS = (
    "original_baseline",
    "pullback_base",
    "pullback_persistence_6",
    "pullback_time_exit_24",
    "pullback_regime_loss_exit",
    "pullback_persistence_6_regime_loss_exit",
)
EXACT_VARIANT_IDS = (
    "ORIGINAL_BASELINE",
    "PULLBACK_BASE",
    "PULLBACK_PERSISTENCE_6",
    "PULLBACK_TIME_EXIT_24",
    "PULLBACK_REGIME_LOSS_EXIT",
    "PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT",
)
_EXPECTED_VALUES = (
    ("ORIGINAL", 0, 0, 0, "0", "0", "0", 0, False, 0),
    ("PULLBACK", 3, 1, 6, "0.10", "1.0", "1.0", 0, False, 1),
    ("PULLBACK", 6, 1, 6, "0.10", "1.0", "1.0", 0, False, 2),
    ("PULLBACK", 3, 1, 6, "0.10", "1.0", "1.0", 24, False, 2),
    ("PULLBACK", 3, 1, 6, "0.10", "1.0", "1.0", 0, True, 3),
    ("PULLBACK", 6, 1, 6, "0.10", "1.0", "1.0", 0, True, 4),
)


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class PullbackHypothesis:
    variant_id: str
    catalog_key: str
    analyzer: str
    trend_persistence_candles: int
    pullback_min_candles: int
    pullback_max_candles: int
    minimum_pullback_depth_atr: Decimal
    maximum_pullback_depth_atr: Decimal
    maximum_entry_extension_atr: Decimal
    time_exit_candles: int | None
    regime_loss_exit: bool
    complexity_rank: int
    minimum_volume_ratio: Decimal | None = None
    maximum_atr_relative: Decimal | None = None
    directional_close_confirmation: bool = True

    @property
    def is_baseline(self) -> bool:
        return self.analyzer == "ORIGINAL"


@dataclass(frozen=True, slots=True)
class PullbackHypothesisCatalog:
    version: int
    path: Path
    hypotheses: tuple[PullbackHypothesis, ...]
    content_hash: str

    def by_id(self, variant_id: str) -> PullbackHypothesis:
        found = next(
            (item for item in self.hypotheses if item.variant_id == variant_id),
            None,
        )
        if found is None:
            raise ValueError(f"unknown pullback hypothesis: {variant_id}")
        return found


@dataclass(frozen=True, slots=True)
class PullbackExperimentPeriods:
    development_start: datetime
    development_end: datetime
    validation_start: datetime
    validation_end: datetime
    consumed_start: datetime
    consumed_end: datetime

    def __post_init__(self) -> None:
        for name in (
            "development_start",
            "development_end",
            "validation_start",
            "validation_end",
            "consumed_start",
            "consumed_end",
        ):
            _aware(getattr(self, name), name)
        if not (
            self.development_start
            <= self.development_end
            < self.validation_start
            <= self.validation_end
            < self.consumed_start
            <= self.consumed_end
        ):
            raise ValueError("pullback periods must be chronological and non-overlapping")

    @classmethod
    def pre_registered(cls) -> PullbackExperimentPeriods:
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
            raise ValueError("pullback periods differ from Sprint 3B.1 pre-registration")

    def assert_research_range(
        self,
        start: datetime,
        end: datetime,
        operation: str,
    ) -> None:
        _aware(start, "start")
        _aware(end, "end")
        if start > end:
            raise ValueError(f"{operation} start must not exceed end")
        if start >= self.consumed_start or end >= self.consumed_start:
            raise ValueError(f"{operation} overlaps consumed 2025-2026 period")
        if start.year >= 2026 or end.year >= 2026:
            raise ValueError(f"{operation} cannot use 2026")


@dataclass(frozen=True, slots=True)
class PullbackValidationLock:
    market: str
    mode: str
    variant_ids: tuple[str, ...]
    catalog_hash: str
    development_fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        market: str,
        mode: str,
        variant_ids: tuple[str, ...],
        catalog_hash: str,
    ) -> PullbackValidationLock:
        fingerprint = canonical_hash(
            {
                "market": market,
                "mode": mode,
                "variant_ids": variant_ids,
                "catalog_hash": catalog_hash,
                "selection_source": "DEVELOPMENT_2022_2023_ONLY",
            }
        )
        return cls(
            market=market,
            mode=mode,
            variant_ids=variant_ids,
            catalog_hash=catalog_hash,
            development_fingerprint=fingerprint,
        )

    def assert_unchanged(
        self,
        *,
        market: str,
        mode: str,
        variant_ids: tuple[str, ...],
        catalog_hash: str,
    ) -> None:
        candidate = self.create(
            market=market,
            mode=mode,
            variant_ids=variant_ids,
            catalog_hash=catalog_hash,
        )
        if candidate.development_fingerprint != self.development_fingerprint:
            raise ValueError("validation configuration differs from development lock")


def load_pullback_catalog(
    path: Path = CATALOG_FILE,
) -> PullbackHypothesisCatalog:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    metadata = raw.get("metadata")
    hypotheses_raw = raw.get("hypotheses")
    if not isinstance(metadata, dict) or not isinstance(hypotheses_raw, dict):
        raise ValueError("pullback catalog requires metadata and hypotheses")
    expected_metadata: dict[str, Any] = {
        "version": 1,
        "symbol": "ETHUSDT",
        "interval": "1h",
        "development_start": "2022-01-01T00:00:00Z",
        "development_end": "2023-12-31T23:00:00Z",
        "validation_start": "2024-01-01T00:00:00Z",
        "validation_end": "2024-12-31T23:00:00Z",
        "consumed_start": "2025-01-01T00:00:00Z",
        "consumed_end": "2026-07-01T00:00:00Z",
    }
    if metadata != expected_metadata:
        raise ValueError("pullback catalog metadata differs from pre-registration")
    if tuple(hypotheses_raw) != EXACT_CATALOG_KEYS:
        raise ValueError("pullback catalog must contain the six exact ordered variants")
    normalized: dict[str, Any] = {
        "metadata": expected_metadata,
        "hypotheses": {},
    }
    hypotheses: list[PullbackHypothesis] = []
    field_names = (
        "analyzer",
        "trend_persistence_candles",
        "pullback_min_candles",
        "pullback_max_candles",
        "minimum_pullback_depth_atr",
        "maximum_pullback_depth_atr",
        "maximum_entry_extension_atr",
        "time_exit_candles",
        "regime_loss_exit",
        "complexity_rank",
    )
    for index, key in enumerate(EXACT_CATALOG_KEYS):
        values = hypotheses_raw.get(key)
        if not isinstance(values, dict):
            raise ValueError(f"invalid pullback hypothesis section: {key}")
        variant_id = values.get("variant_id")
        observed = tuple(values.get(name) for name in field_names)
        if variant_id != EXACT_VARIANT_IDS[index] or observed != _EXPECTED_VALUES[index]:
            raise ValueError(f"pullback hypothesis {key} differs from pre-registration")
        hypothesis = PullbackHypothesis(
            variant_id=str(variant_id),
            catalog_key=key,
            analyzer=str(values["analyzer"]),
            trend_persistence_candles=int(values["trend_persistence_candles"]),
            pullback_min_candles=int(values["pullback_min_candles"]),
            pullback_max_candles=int(values["pullback_max_candles"]),
            minimum_pullback_depth_atr=Decimal(
                str(values["minimum_pullback_depth_atr"])
            ),
            maximum_pullback_depth_atr=Decimal(
                str(values["maximum_pullback_depth_atr"])
            ),
            maximum_entry_extension_atr=Decimal(
                str(values["maximum_entry_extension_atr"])
            ),
            time_exit_candles=(
                int(values["time_exit_candles"])
                if int(values["time_exit_candles"]) > 0
                else None
            ),
            regime_loss_exit=bool(values["regime_loss_exit"]),
            complexity_rank=int(values["complexity_rank"]),
        )
        hypotheses.append(hypothesis)
        normalized["hypotheses"][key] = {
            "variant_id": hypothesis.variant_id,
            **{
                name: values[name]
                for name in field_names
            },
        }
    return PullbackHypothesisCatalog(
        version=1,
        path=path,
        hypotheses=tuple(hypotheses),
        content_hash=canonical_hash(normalized),
    )
