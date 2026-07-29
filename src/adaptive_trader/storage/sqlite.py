"""SQLite schema and append-oriented repository."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from adaptive_trader.domain.models import (
    Candle,
    Fill,
    PortfolioSnapshot,
    Position,
    RiskDecision,
    SimulatedOrder,
    StrategyDecisionRecord,
    serialize_model,
)

SCHEMA_VERSION = 1
SCHEMA_STATEMENTS = (
    (
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    ),
    """CREATE TABLE IF NOT EXISTS candles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        open TEXT NOT NULL,
        high TEXT NOT NULL,
        low TEXT NOT NULL,
        close TEXT NOT NULL,
        volume TEXT NOT NULL,
        UNIQUE(symbol, timestamp)
    )""",
    """CREATE TABLE IF NOT EXISTS strategy_decisions (
        record_id TEXT PRIMARY KEY,
        analysis_time TEXT NOT NULL,
        signal_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        regime TEXT NOT NULL,
        confidence TEXT NOT NULL,
        entry_price TEXT NOT NULL,
        stop_loss TEXT NOT NULL,
        take_profit TEXT NOT NULL,
        suggested_quantity TEXT NOT NULL,
        rationale TEXT NOT NULL,
        analyzer_name TEXT NOT NULL,
        context_candle_count INTEGER NOT NULL,
        indicators_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS risk_decisions (
        decision_id TEXT PRIMARY KEY,
        signal_id TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        approved INTEGER NOT NULL,
        reason TEXT NOT NULL,
        intent_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS simulated_orders (
        order_id TEXT PRIMARY KEY,
        intent_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        quantity TEXT NOT NULL,
        price TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS fills (
        fill_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        quantity TEXT NOT NULL,
        price TEXT NOT NULL,
        fee TEXT NOT NULL,
        filled_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS positions (
        position_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        quantity TEXT NOT NULL,
        average_entry_price TEXT NOT NULL,
        current_price TEXT NOT NULL,
        opened_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        captured_at TEXT NOT NULL,
        cash_balance TEXT NOT NULL,
        equity TEXT NOT NULL,
        daily_loss TEXT NOT NULL,
        trades_today INTEGER NOT NULL,
        positions_json TEXT NOT NULL
    )""",
)


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
            "VALUES (?, datetime('now'))",
            (SCHEMA_VERSION,),
        )


def initialize_database(path: Path) -> None:
    connection = connect_database(path)
    try:
        create_schema(connection)
    finally:
        connection.close()


def database_status(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"path": str(path), "exists": False, "schema_version": None, "tables": []}
    connection = connect_database(path)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        schema_version = None
        if "schema_migrations" in tables:
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            schema_version = row[0] if row is not None else None
        return {
            "path": str(path),
            "exists": True,
            "schema_version": schema_version,
            "tables": tables,
        }
    finally:
        connection.close()


class DatabaseRepository:
    def __init__(self, path: Path) -> None:
        self._connection = connect_database(path)
        create_schema(self._connection)

    def close(self) -> None:
        self._connection.close()

    def save_candle(self, candle: Candle) -> None:
        data = serialize_model(candle)
        with self._connection:
            self._connection.execute(
                """INSERT OR IGNORE INTO candles
                (symbol, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                tuple(
                    data[name]
                    for name in ("symbol", "timestamp", "open", "high", "low", "close", "volume")
                ),
            )

    def save_strategy_decision(self, record: StrategyDecisionRecord) -> None:
        data = serialize_model(record)
        signal = data["signal"]
        if not isinstance(signal, dict):
            raise TypeError("serialized signal must be an object")
        with self._connection:
            self._connection.execute(
                """INSERT OR REPLACE INTO strategy_decisions
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["record_id"],
                    data["analysis_time"],
                    signal["signal_id"],
                    signal["symbol"],
                    signal["direction"],
                    signal["regime"],
                    signal["confidence"],
                    signal["entry_price"],
                    signal["stop_loss"],
                    signal["take_profit"],
                    signal["suggested_quantity"],
                    signal["rationale"],
                    signal["analyzer_name"],
                    data["context_candle_count"],
                    json.dumps(data["indicators"], sort_keys=True),
                ),
            )

    def save_risk_decision(self, decision: RiskDecision) -> None:
        data = serialize_model(decision)
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO risk_decisions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    data["decision_id"],
                    data["signal_id"],
                    data["decided_at"],
                    int(decision.approved),
                    data["reason"],
                    json.dumps(data["order_intent"], sort_keys=True)
                    if decision.order_intent is not None
                    else None,
                ),
            )

    def save_simulated_order(self, order: SimulatedOrder) -> None:
        data = serialize_model(order)
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO simulated_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(
                    data[name]
                    for name in (
                        "order_id",
                        "intent_id",
                        "symbol",
                        "direction",
                        "quantity",
                        "price",
                        "status",
                        "created_at",
                    )
                ),
            )

    def save_fill(self, fill: Fill) -> None:
        data = serialize_model(fill)
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO fills VALUES (?, ?, ?, ?, ?, ?, ?)",
                tuple(
                    data[name]
                    for name in (
                        "fill_id",
                        "order_id",
                        "symbol",
                        "quantity",
                        "price",
                        "fee",
                        "filled_at",
                    )
                ),
            )

    def save_position(self, position: Position) -> None:
        data = serialize_model(position)
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO positions VALUES (?, ?, ?, ?, ?, ?)",
                tuple(
                    data[name]
                    for name in (
                        "position_id",
                        "symbol",
                        "quantity",
                        "average_entry_price",
                        "current_price",
                        "opened_at",
                    )
                ),
            )

    def save_portfolio_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        data = serialize_model(snapshot)
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO portfolio_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    data["snapshot_id"],
                    data["captured_at"],
                    data["cash_balance"],
                    data["equity"],
                    data["daily_loss"],
                    data["trades_today"],
                    json.dumps(data["positions"], sort_keys=True),
                ),
            )
