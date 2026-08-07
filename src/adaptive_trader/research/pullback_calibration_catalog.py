"""Immutable frequency-calibration catalog for Sprint 3B.2."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from adaptive_trader.research.datasets import canonical_hash
from adaptive_trader.research.pullback_catalog import PullbackHypothesis

CALIBRATION_CATALOG_FILE = Path("pullback-calibration-v1.toml")
CALIBRATION_VARIANT_IDS = (
    "CALIBRATION_BASE",
    "EXTENSION_1_5",
    "EXTENSION_2_0",
    "NO_MINIMUM_DEPTH",
    "VOLUME_RELAXED",
    "VOLATILITY_RELAXED",
    "PERSISTENCE_2",
    "DIRECTIONAL_CLOSE_RELAXED",
)

_BASE = PullbackHypothesis(
    variant_id="CALIBRATION_BASE",
    catalog_key="calibration_base",
    analyzer="PULLBACK",
    trend_persistence_candles=3,
    pullback_min_candles=1,
    pullback_max_candles=6,
    minimum_pullback_depth_atr=Decimal("0.10"),
    maximum_pullback_depth_atr=Decimal("1.00"),
    maximum_entry_extension_atr=Decimal("1.00"),
    time_exit_candles=None,
    regime_loss_exit=False,
    complexity_rank=0,
    minimum_volume_ratio=Decimal("1.00"),
    maximum_atr_relative=Decimal("0.05"),
    directional_close_confirmation=True,
)

_CHANGES: dict[str, dict[str, object]] = {
    "EXTENSION_1_5": {"maximum_entry_extension_atr": Decimal("1.50")},
    "EXTENSION_2_0": {"maximum_entry_extension_atr": Decimal("2.00")},
    "NO_MINIMUM_DEPTH": {"minimum_pullback_depth_atr": Decimal("0")},
    "VOLUME_RELAXED": {"minimum_volume_ratio": Decimal("0.8")},
    "VOLATILITY_RELAXED": {"maximum_atr_relative": Decimal("0.07")},
    "PERSISTENCE_2": {"trend_persistence_candles": 2},
    "DIRECTIONAL_CLOSE_RELAXED": {
        "directional_close_confirmation": False
    },
}


@dataclass(frozen=True, slots=True)
class PullbackCalibrationCatalog:
    path: Path
    variants: tuple[PullbackHypothesis, ...]
    canonical_hash: str
    file_sha256: str

    def by_id(self, variant_id: str) -> PullbackHypothesis:
        for variant in self.variants:
            if variant.variant_id == variant_id:
                return variant
        raise ValueError(f"unknown calibration variant: {variant_id}")


def _serializable(variant: PullbackHypothesis) -> dict[str, object]:
    result = asdict(variant)
    return {
        key: str(value) if isinstance(value, Decimal) else value
        for key, value in result.items()
    }


def changed_dimensions(
    base: PullbackHypothesis,
    variant: PullbackHypothesis,
) -> tuple[str, ...]:
    ignored = {"variant_id", "catalog_key", "complexity_rank"}
    base_values = asdict(base)
    variant_values = asdict(variant)
    return tuple(
        key
        for key in base_values
        if key not in ignored and base_values[key] != variant_values[key]
    )


def load_pullback_calibration_catalog(
    path: Path = CALIBRATION_CATALOG_FILE,
) -> PullbackCalibrationCatalog:
    content = path.read_bytes()
    raw = tomllib.loads(content.decode("utf-8"))
    metadata = raw.get("metadata")
    sections = raw.get("variants")
    if not isinstance(metadata, dict) or not isinstance(sections, dict):
        raise ValueError("calibration catalog requires metadata and variants")
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
        "selection_basis": "OPERATIONAL_FREQUENCY_ONLY",
    }
    if metadata != expected_metadata:
        raise ValueError("calibration metadata differs from pre-registration")
    expected_keys = (
        "calibration_base",
        "extension_1_5",
        "extension_2_0",
        "no_minimum_depth",
        "volume_relaxed",
        "volatility_relaxed",
        "persistence_2",
        "directional_close_relaxed",
    )
    if tuple(sections) != expected_keys:
        raise ValueError("calibration catalog variants or order changed")
    base_raw = sections["calibration_base"]
    expected_base = {
        "variant_id": "CALIBRATION_BASE",
        "trend_persistence_candles": 3,
        "pullback_min_candles": 1,
        "pullback_max_candles": 6,
        "minimum_pullback_depth_atr": "0.10",
        "maximum_pullback_depth_atr": "1.00",
        "maximum_entry_extension_atr": "1.00",
        "minimum_volume_ratio": "1.00",
        "maximum_atr_relative": "0.05",
        "directional_close_confirmation": True,
        "complexity_rank": 0,
    }
    if base_raw != expected_base:
        raise ValueError("CALIBRATION_BASE differs from pre-registration")
    variants = [_BASE]
    for key, variant_id in zip(expected_keys[1:], CALIBRATION_VARIANT_IDS[1:], strict=True):
        section = sections[key]
        change = _CHANGES[variant_id]
        expected_section = {
            "variant_id": variant_id,
            **{
                name: str(value) if isinstance(value, Decimal) else value
                for name, value in change.items()
            },
        }
        if section != expected_section:
            raise ValueError(f"{variant_id} differs from pre-registration")
        variant = _variant_with_change(
            variant_id, key, len(variants)
        )
        if len(changed_dimensions(_BASE, variant)) != 1:
            raise ValueError(f"{variant_id} must alter exactly one dimension")
        variants.append(variant)
    normalized = {
        "metadata": expected_metadata,
        "variants": [_serializable(item) for item in variants],
    }
    return PullbackCalibrationCatalog(
        path=path,
        variants=tuple(variants),
        canonical_hash=canonical_hash(normalized),
        file_sha256=hashlib.sha256(content).hexdigest(),
    )


def _variant_with_change(
    variant_id: str,
    key: str,
    complexity_rank: int,
) -> PullbackHypothesis:
    common = replace(
        _BASE,
        variant_id=variant_id,
        catalog_key=key,
        complexity_rank=complexity_rank,
    )
    if variant_id == "EXTENSION_1_5":
        return replace(
            common, maximum_entry_extension_atr=Decimal("1.50")
        )
    if variant_id == "EXTENSION_2_0":
        return replace(
            common, maximum_entry_extension_atr=Decimal("2.00")
        )
    if variant_id == "NO_MINIMUM_DEPTH":
        return replace(
            common, minimum_pullback_depth_atr=Decimal("0")
        )
    if variant_id == "VOLUME_RELAXED":
        return replace(
            common, minimum_volume_ratio=Decimal("0.8")
        )
    if variant_id == "VOLATILITY_RELAXED":
        return replace(
            common, maximum_atr_relative=Decimal("0.07")
        )
    if variant_id == "PERSISTENCE_2":
        return replace(common, trend_persistence_candles=2)
    if variant_id == "DIRECTIONAL_CLOSE_RELAXED":
        return replace(
            common, directional_close_confirmation=False
        )
    raise ValueError(f"unsupported calibration variant: {variant_id}")
