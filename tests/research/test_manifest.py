from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.research.datasets import canonical_hash, validate_dataset
from adaptive_trader.research.manifest import config_hash, reproducibility_hash


def test_config_and_reproducibility_hashes_are_stable(daily_candles) -> None:
    config = TradingConfig(database_path=Path("/tmp/one.sqlite"))
    other_path = replace(config, database_path=Path("/tmp/two.sqlite"))
    dataset = validate_dataset(daily_candles, created_at=datetime(2026, 1, 1, tzinfo=UTC))

    assert config_hash(config) == config_hash(other_path)
    first = reproducibility_hash(
        dataset_hash=dataset.content_hash,
        configuration=config.as_dict(),
        segment_hashes={"test": "segment"},
        strategy_name="strategy",
        strategy_version="v1",
    )
    second = reproducibility_hash(
        dataset_hash=dataset.content_hash,
        configuration=other_path.as_dict(),
        segment_hashes={"test": "segment"},
        strategy_name="strategy",
        strategy_version="v1",
    )
    changed = replace(config, target_r_multiple=Decimal("3"))
    assert first == second
    assert first != reproducibility_hash(
        dataset_hash=dataset.content_hash,
        configuration=changed.as_dict(),
        segment_hashes={"test": "segment"},
        strategy_name="strategy",
        strategy_version="v1",
    )


def test_canonical_hash_is_order_independent() -> None:
    assert canonical_hash({"b": Decimal("2"), "a": "1"}) == canonical_hash(
        {"a": "1", "b": Decimal("2")}
    )
