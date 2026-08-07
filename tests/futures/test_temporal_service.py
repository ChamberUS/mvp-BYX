from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.futures.integrity import FuturesGapPolicy, inspect_public_dataset
from adaptive_trader.futures.models import FundingRate
from adaptive_trader.futures.temporal_robustness import (
    FuturesTemporalRobustnessService,
    TemporalRobustnessRequest,
)
from adaptive_trader.storage.sqlite import DatabaseRepository
from tests.futures.conftest import make_candles, make_marks


def test_temporal_service_runs_six_fixed_variants_offline(tmp_path) -> None:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    closes = tuple(
        str(1000 + (index % 120 if (index // 120) % 2 == 0 else 120 - index % 120))
        for index in range(500)
    )
    candles = make_candles(start, closes)
    marks = make_marks(candles)
    funding = tuple(
        FundingRate(
            symbol="ETHUSDT",
            funding_time=start + timedelta(hours=index),
            funding_rate=Decimal("0.0001"),
            mark_price=marks[index].close,
        )
        for index in range(0, len(candles), 8)
    )
    database_path = tmp_path / "temporal.sqlite3"
    repository = DatabaseRepository(database_path)
    try:
        repository.upsert_futures_candles(candles)
        repository.upsert_mark_prices(marks)
        repository.upsert_funding_rates(funding)
        end = datetime(2025, 12, 31, 23, tzinfo=UTC)
        integrity = inspect_public_dataset(
            candles,
            marks,
            funding,
            requested_start=start,
            requested_end=end,
            gap_policy=FuturesGapPolicy.WARN,
        )
        bundle = FuturesTemporalRobustnessService(
            repository,
            TradingConfig(database_path=database_path, interval="1h"),
        ).run(
            TemporalRobustnessRequest(
                symbol="ETHUSDT",
                interval="1h",
                start=start,
                end=end,
                dataset_hash=integrity.combined_dataset_hash,
                leverage=Decimal("1"),
                bootstrap_iterations=20,
                bootstrap_seed=42,
            )
        )
    finally:
        repository.close()
    assert len(bundle.variants) == 6
    assert len(bundle.yearly_rows) == 24
    assert len(bundle.classifications) == 6
    assert all(item["candidate_frozen"] is False for item in bundle.classifications)
    assert bundle.request.end.year == 2025
