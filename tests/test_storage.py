import sqlite3
from pathlib import Path

from adaptive_trader.storage.sqlite import DatabaseRepository, database_status, initialize_database


def test_database_creation_has_all_required_tables(tmp_path: Path) -> None:
    path = tmp_path / "adaptive.sqlite3"

    initialize_database(path)
    status = database_status(path)

    assert status["schema_version"] == 1
    assert set(status["tables"]) >= {
        "candles",
        "strategy_decisions",
        "risk_decisions",
        "simulated_orders",
        "fills",
        "positions",
        "portfolio_snapshots",
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
