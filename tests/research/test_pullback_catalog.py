from pathlib import Path

import pytest

from adaptive_trader.research.pullback_catalog import (
    EXACT_VARIANT_IDS,
    load_pullback_catalog,
)


def test_catalog_contains_only_the_six_pre_registered_variants() -> None:
    catalog = load_pullback_catalog()

    assert tuple(item.variant_id for item in catalog.hypotheses) == EXACT_VARIANT_IDS
    assert catalog.content_hash == (
        "49301c90ded03eb83da6245e47b824931f11a7afdf6e427f88ca809362d42d8b"
    )


def test_catalog_rejects_any_parameter_mutation(tmp_path: Path) -> None:
    source = Path("pullback-hypotheses-v1.toml").read_text(encoding="utf-8")
    changed = tmp_path / "changed.toml"
    changed.write_text(
        source.replace(
            'maximum_entry_extension_atr = "1.0"',
            'maximum_entry_extension_atr = "1.1"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="differs from pre-registration"):
        load_pullback_catalog(changed)
