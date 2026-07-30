import sqlite3
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_trader.futures.datasets import validate_futures_dataset
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FundingRate,
    FuturesPriceSource,
)
from adaptive_trader.storage.sqlite import DatabaseRepository, database_status, initialize_database


def test_spot_and_futures_storage_do_not_collide(
    tmp_path: Path,
    candle,
    futures_candles,
    mark_prices,
    start_time,
) -> None:
    repository = DatabaseRepository(tmp_path / "markets.sqlite3")
    funding = FundingRate(
        symbol="ETHUSDT",
        funding_time=start_time + timedelta(hours=4),
        funding_rate=Decimal("0.0001"),
        mark_price=Decimal("110"),
    )
    try:
        repository.save_candle(replace(candle, interval="1h"))
        assert repository.upsert_futures_candles(futures_candles) == len(futures_candles)
        assert repository.upsert_futures_candles(futures_candles) == len(futures_candles)
        assert repository.upsert_mark_prices(mark_prices) == len(mark_prices)
        assert repository.upsert_mark_prices(mark_prices) == len(mark_prices)
        assert repository.upsert_funding_rates((funding, funding)) == 2
        assert repository.count_candles("ETHUSDT", "1h") == 1
        assert repository.count_futures_candles("ETHUSDT", "1h") == len(futures_candles)
        assert repository.count_mark_prices("ETHUSDT", "1h") == len(mark_prices)
        assert repository.count_funding_rates("ETHUSDT") == 1
        assert repository.latest_futures_candle("ETHUSDT", "1h") == futures_candles[-1]
        assert repository.get_mark_prices("ETHUSDT", "1h") == mark_prices
        assert repository.get_funding_rates("ETHUSDT") == (funding,)
    finally:
        repository.close()


def test_v3_database_migrates_to_v4_without_losing_spot(tmp_path: Path) -> None:
    path = tmp_path / "v3.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        INSERT INTO schema_migrations VALUES (3, '2026-01-01T00:00:00+00:00');
        CREATE TABLE candles(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
            open_time TEXT NOT NULL, close_time TEXT, open TEXT NOT NULL,
            high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
            volume TEXT NOT NULL, quote_volume TEXT, trades_count INTEGER,
            taker_buy_base_volume TEXT, taker_buy_quote_volume TEXT,
            is_closed INTEGER NOT NULL, collected_at TEXT,
            UNIQUE(exchange, symbol, interval, open_time)
        );
        INSERT INTO candles(
            exchange, symbol, interval, open_time, close_time, open, high, low, close,
            volume, is_closed
        ) VALUES (
            'BINANCE', 'ETHUSDT', '1h', '2025-01-01T00:00:00+00:00',
            '2025-01-01T00:59:59+00:00', '100', '101', '99', '100', '1', 1
        );
        """
    )
    connection.commit()
    connection.close()

    initialize_database(path)
    status = database_status(path)
    repository = DatabaseRepository(path)
    try:
        assert status["schema_version"] == 4
        assert repository.count_candles("ETHUSDT", "1h") == 1
        assert set(status["tables"]) >= {
            "candles",
            "futures_candles",
            "futures_mark_prices",
            "futures_funding_rates",
        }
    finally:
        repository.close()


def test_dataset_hash_changes_with_funding(futures_candles, mark_prices, start_time) -> None:
    first = FundingRate(
        symbol="ETHUSDT",
        funding_time=start_time + timedelta(hours=4),
        funding_rate=Decimal("0.0001"),
    )
    second = replace(first, funding_rate=Decimal("0.0002"))
    left = validate_futures_dataset(
        futures_candles,
        mark_prices,
        (first,),
        source="fixture",
    )
    right = validate_futures_dataset(
        futures_candles,
        mark_prices,
        (second,),
        source="fixture",
    )
    assert left.candle_hash == right.candle_hash
    assert left.mark_price_hash == right.mark_price_hash
    assert left.funding_hash != right.funding_hash
    assert left.combined_dataset_hash != right.combined_dataset_hash
    assert left.valid_for_research


def test_dataset_missing_data_policies(futures_candles, mark_prices) -> None:
    with pytest.raises(ValueError, match="FUNDING_DATA_MISSING"):
        validate_futures_dataset(futures_candles, mark_prices, (), source="fixture")
    warned = validate_futures_dataset(
        futures_candles,
        mark_prices,
        (),
        source="fixture",
        funding_missing_policy=FundingMissingPolicy.WARN_AND_SKIP,
    )
    assert "FUNDING_DATA_MISSING" in warned.warnings
    with pytest.raises(ValueError, match="MARK_PRICE_MISSING"):
        validate_futures_dataset(
            futures_candles,
            mark_prices[:-1],
            (),
            source="fixture",
            funding_enabled=False,
            funding_missing_policy=FundingMissingPolicy.DISABLE_EXPLICITLY,
        )


def test_dataset_detects_gap(futures_candles, mark_prices) -> None:
    candles = futures_candles[:2] + futures_candles[3:]
    times = {item.open_time for item in candles}
    marks = tuple(item for item in mark_prices if item.open_time in times)
    dataset = validate_futures_dataset(
        candles,
        marks,
        (),
        source="fixture",
        funding_enabled=False,
        funding_missing_policy=FundingMissingPolicy.DISABLE_EXPLICITLY,
    )
    assert dataset.gap_count == 1
    assert "FUTURES_CANDLE_GAPS" in dataset.warnings


def test_spot_proxy_is_fixture_only_and_invalidates_dataset(
    futures_candles,
    mark_prices,
) -> None:
    dataset = validate_futures_dataset(
        futures_candles,
        mark_prices[:-1],
        (),
        source="unit-test-only",
        funding_enabled=False,
        funding_missing_policy=FundingMissingPolicy.DISABLE_EXPLICITLY,
        price_source=FuturesPriceSource.SPOT_PROXY_FOR_TESTS_ONLY,
    )
    assert not dataset.valid_for_research
    assert "REPORT_INVALID_PRICE_PROXY" in dataset.warnings
