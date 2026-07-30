import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from adaptive_trader.storage.sqlite import DatabaseRepository, database_status, initialize_database


def test_database_creation_has_all_required_tables(tmp_path: Path) -> None:
    path = tmp_path / "adaptive.sqlite3"

    initialize_database(path)
    status = database_status(path)

    assert status["schema_version"] == 4
    assert set(status["tables"]) >= {
        "candles",
        "strategy_decisions",
        "risk_decisions",
        "simulated_orders",
        "fills",
        "positions",
        "portfolio_snapshots",
        "futures_candles",
        "futures_mark_prices",
        "futures_funding_rates",
    }


def test_repository_persists_candle(tmp_path: Path, candle) -> None:
    path = tmp_path / "adaptive.sqlite3"
    repository = DatabaseRepository(path)
    try:
        repository.save_candle(candle)
    finally:
        repository.close()

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 1
    finally:
        connection.close()


def test_upsert_is_idempotent_and_queries_in_order(tmp_path: Path, candle) -> None:
    path = tmp_path / "adaptive.sqlite3"
    repository = DatabaseRepository(path)
    try:
        later = candle.__class__(
            symbol=candle.symbol,
            timestamp=candle.timestamp + timedelta(minutes=1),
            open=Decimal("2030"),
            high=Decimal("2060"),
            low=Decimal("2020"),
            close=Decimal("2040"),
            volume=Decimal("11"),
        )
        repository.upsert_candles((candle, later, candle))
        updated = candle.__class__(
            symbol=candle.symbol,
            timestamp=candle.timestamp,
            open=candle.open,
            high=Decimal("2060"),
            low=candle.low,
            close=Decimal("2050"),
            volume=candle.volume,
        )
        repository.upsert_candles((updated,))
        rows = repository.get_candles("ETHUSDT", "1m")
        assert len(rows) == 2
        assert rows[0].close == Decimal("2050")
        assert rows[0].open_time < rows[1].open_time
    finally:
        repository.close()


def test_v1_database_is_migrated_without_deleting_legacy_data(tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00+00:00');
        CREATE TABLE candles(
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timestamp TEXT NOT NULL,
            open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
            volume TEXT NOT NULL, UNIQUE(symbol, timestamp)
        );
        CREATE TABLE simulated_orders(order_id TEXT PRIMARY KEY, intent_id TEXT, symbol TEXT,
            direction TEXT, quantity TEXT, price TEXT, status TEXT, created_at TEXT);
        CREATE TABLE fills(fill_id TEXT PRIMARY KEY, order_id TEXT, symbol TEXT, quantity TEXT,
            price TEXT, fee TEXT, filled_at TEXT);
        CREATE TABLE positions(position_id TEXT PRIMARY KEY, symbol TEXT, quantity TEXT,
            average_entry_price TEXT, current_price TEXT, opened_at TEXT);
        INSERT INTO candles(symbol, timestamp, open, high, low, close, volume)
            VALUES ('ETHUSDT', '2026-01-01T00:00:00+00:00', '100', '101', '99', '100', '1');
        """
    )
    connection.commit()
    connection.close()

    initialize_database(path)
    status = database_status(path)
    repository = DatabaseRepository(path)
    try:
        assert status["schema_version"] == 4
        assert "candles_v1_legacy" in status["tables"]
        assert repository.count_candles("ETHUSDT", "1m") == 1
    finally:
        repository.close()


def test_v2_snapshot_is_migrated_to_v3_without_losing_data(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        INSERT INTO schema_migrations VALUES (2, '2026-01-01T00:00:00+00:00');
        CREATE TABLE portfolio_snapshots(
            snapshot_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL,
            cash_balance TEXT NOT NULL, equity TEXT NOT NULL, daily_loss TEXT NOT NULL,
            trades_today INTEGER NOT NULL, positions_json TEXT NOT NULL
        );
        INSERT INTO portfolio_snapshots VALUES
            ('snapshot-1', '2026-01-01T00:00:00+00:00', '9900', '10000', '100', 3, '[]');
        """
    )
    connection.commit()
    connection.close()

    initialize_database(path)

    connection = sqlite3.connect(path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(portfolio_snapshots)")}
        row = connection.execute(
            "SELECT day_start_equity, orders_today FROM portfolio_snapshots"
        ).fetchone()
        assert columns >= {
            "day_start_equity",
            "entries_today",
            "orders_today",
            "closed_trades_today",
        }
        assert row == ("10000", 3)
    finally:
        connection.close()
