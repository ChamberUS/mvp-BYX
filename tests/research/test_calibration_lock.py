import pytest

from adaptive_trader.research.pullback_catalog import PullbackValidationLock


def test_validation_lock_is_immutable() -> None:
    lock = PullbackValidationLock.create(
        market="SPOT",
        mode="LONG",
        variant_ids=("CALIBRATION_BASE",),
        catalog_hash="hash",
    )
    with pytest.raises(ValueError, match="differs"):
        lock.assert_unchanged(
            market="SPOT",
            mode="LONG",
            variant_ids=("EXTENSION_1_5",),
            catalog_hash="hash",
        )
