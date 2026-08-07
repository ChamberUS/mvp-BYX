from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from adaptive_trader.research.trend_following_catalog import (
    EXACT_VARIANT_IDS,
    TrendFollowingRiskModel,
    load_trend_following_catalog,
)


def test_catalog_contains_only_the_six_pre_registered_variants_in_order() -> None:
    catalog = load_trend_following_catalog()

    assert tuple(item.variant_id for item in catalog.hypotheses) == EXACT_VARIANT_IDS
    assert tuple(item.catalog_order for item in catalog.hypotheses) == tuple(range(6))
    assert {item.sma_period_days for item in catalog.hypotheses} == {200}
    assert {item.entry_period_days for item in catalog.hypotheses} == {20}
    assert {item.exit_period_days for item in catalog.hypotheses} == {10, 20}
    assert (
        sum(item.risk_model is TrendFollowingRiskModel.DEFENSIVE for item in catalog.hypotheses)
        == 2
    )
    assert len(catalog.canonical_hash) == len(catalog.file_sha256) == 64


def test_catalog_hash_is_stable_and_content_tampering_is_rejected(tmp_path: Path) -> None:
    first = load_trend_following_catalog()
    second = load_trend_following_catalog()
    assert first.canonical_hash == second.canonical_hash
    assert first.file_sha256 == second.file_sha256

    content = first.path.read_text(encoding="utf-8")
    changed = tmp_path / "changed.toml"
    changed.write_text(
        content.replace("sma_period_days = 200", "sma_period_days = 199", 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="differs from pre-registration"):
        load_trend_following_catalog(changed)


def test_catalog_models_are_frozen() -> None:
    hypothesis = load_trend_following_catalog().hypotheses[0]
    field_name = "exit_period_days"

    with pytest.raises(FrozenInstanceError):
        setattr(hypothesis, field_name, 10)
