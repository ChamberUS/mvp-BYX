from adaptive_trader.research.pullback_calibration_catalog import (
    CALIBRATION_VARIANT_IDS,
    changed_dimensions,
    load_pullback_calibration_catalog,
)


def test_catalog_is_fixed_and_each_variant_changes_one_dimension() -> None:
    catalog = load_pullback_calibration_catalog()
    assert tuple(item.variant_id for item in catalog.variants) == (
        CALIBRATION_VARIANT_IDS
    )
    base = catalog.variants[0]
    assert all(
        len(changed_dimensions(base, variant)) == 1
        for variant in catalog.variants[1:]
    )
    assert len(catalog.canonical_hash) == len(catalog.file_sha256) == 64
