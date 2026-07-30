"""Versioned SQLite persistence with idempotent candle storage."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trader.domain.market import ContractType, MarketType
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
from adaptive_trader.futures.models import FundingRate, FuturesCandle, MarkPriceCandle

SCHEMA_VERSION = 4


class SchemaMigrationRequired(RuntimeError):
    """Raised when a database is newer than this application."""


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _decimal_or_none(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _create_v2_tables(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_time TEXT NOT NULL,
            close_time TEXT,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            quote_volume TEXT,
            trades_count INTEGER,
            taker_buy_base_volume TEXT,
            taker_buy_quote_volume TEXT,
            is_closed INTEGER NOT NULL,
            collected_at TEXT,
            UNIQUE(exchange, symbol, interval, open_time)
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
            created_at TEXT NOT NULL,
            reference_price TEXT,
            fee TEXT NOT NULL DEFAULT '0',
            slippage_cost TEXT NOT NULL DEFAULT '0',
            spread_cost TEXT NOT NULL DEFAULT '0'
        )""",
        """CREATE TABLE IF NOT EXISTS fills (
            fill_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            quantity TEXT NOT NULL,
            price TEXT NOT NULL,
            fee TEXT NOT NULL,
            filled_at TEXT NOT NULL,
            reference_price TEXT,
            slippage_cost TEXT NOT NULL DEFAULT '0',
            spread_cost TEXT NOT NULL DEFAULT '0'
        )""",
        """CREATE TABLE IF NOT EXISTS positions (
            position_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            quantity TEXT NOT NULL,
            average_entry_price TEXT NOT NULL,
            current_price TEXT NOT NULL,
            opened_at TEXT NOT NULL,
            stop_loss TEXT,
            take_profit TEXT,
            initial_risk TEXT,
            entry_fee TEXT NOT NULL DEFAULT '0',
            partial_taken INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            captured_at TEXT NOT NULL,
            cash_balance TEXT NOT NULL,
            equity TEXT NOT NULL,
            daily_loss TEXT NOT NULL,
            trades_today INTEGER NOT NULL,
            positions_json TEXT NOT NULL,
            day_start_equity TEXT NOT NULL DEFAULT '0',
            entries_today INTEGER NOT NULL DEFAULT 0,
            orders_today INTEGER NOT NULL DEFAULT 0,
            closed_trades_today INTEGER NOT NULL DEFAULT 0
        )""",
    )
    for statement in statements:
        connection.execute(statement)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_candles_lookup "
        "ON candles(exchange, symbol, interval, open_time)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_candles_closed "
        "ON candles(symbol, interval, is_closed, open_time)"
    )


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE candles RENAME TO candles_v1_legacy")
    _create_v2_tables(connection)
    connection.execute(
        """INSERT INTO candles(
            exchange, symbol, interval, open_time, close_time, open, high, low, close,
            volume, quote_volume, trades_count, taker_buy_base_volume,
            taker_buy_quote_volume, is_closed, collected_at
        )
        SELECT 'BINANCE', symbol, '1m', timestamp, timestamp, open, high, low, close,
            volume, NULL, NULL, NULL, NULL, 1, datetime('now')
        FROM candles_v1_legacy"""
    )
    for table, columns in (
        (
            "simulated_orders",
            (
                "reference_price TEXT",
                "fee TEXT NOT NULL DEFAULT '0'",
                "slippage_cost TEXT NOT NULL DEFAULT '0'",
                "spread_cost TEXT NOT NULL DEFAULT '0'",
            ),
        ),
        (
            "fills",
            (
                "reference_price TEXT",
                "slippage_cost TEXT NOT NULL DEFAULT '0'",
                "spread_cost TEXT NOT NULL DEFAULT '0'",
            ),
        ),
        (
            "positions",
            (
                "stop_loss TEXT",
                "take_profit TEXT",
                "initial_risk TEXT",
                "entry_fee TEXT NOT NULL DEFAULT '0'",
                "partial_taken INTEGER NOT NULL DEFAULT 0",
            ),
        ),
    ):
        existing = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            name = column.split(" ", 1)[0]
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column}")


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    existing = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(portfolio_snapshots)")
    }
    columns = (
        ("day_start_equity", "TEXT NOT NULL DEFAULT '0'"),
        ("entries_today", "INTEGER NOT NULL DEFAULT 0"),
        ("orders_today", "INTEGER NOT NULL DEFAULT 0"),
        ("closed_trades_today", "INTEGER NOT NULL DEFAULT 0"),
    )
    for name, definition in columns:
        if name not in existing:
            connection.execute(
                f"ALTER TABLE portfolio_snapshots ADD COLUMN {name} {definition}"
            )
    connection.execute(
        "UPDATE portfolio_snapshots SET day_start_equity = equity "
        "WHERE day_start_equity = '0'"
    )
    connection.execute(
        "UPDATE portfolio_snapshots SET orders_today = trades_today "
        "WHERE orders_today = 0 AND trades_today > 0"
    )


def _create_v4_tables(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS futures_candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            contract_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_time TEXT NOT NULL,
            close_time TEXT NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            quote_volume TEXT NOT NULL,
            trade_count INTEGER NOT NULL,
            is_closed INTEGER NOT NULL,
            collected_at TEXT,
            UNIQUE(exchange, market_type, contract_type, symbol, interval, open_time)
        )""",
        """CREATE TABLE IF NOT EXISTS futures_mark_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_time TEXT NOT NULL,
            close_time TEXT NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            index_price TEXT,
            estimated_settle_price TEXT,
            is_closed INTEGER NOT NULL,
            collected_at TEXT,
            UNIQUE(symbol, interval, open_time)
        )""",
        """CREATE TABLE IF NOT EXISTS futures_funding_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            funding_time TEXT NOT NULL,
            funding_rate TEXT NOT NULL,
            mark_price TEXT,
            UNIQUE(symbol, funding_time)
        )""",
    )
    for statement in statements:
        connection.execute(statement)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_futures_candles_lookup "
        "ON futures_candles(market_type, symbol, interval, open_time)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_futures_mark_lookup "
        "ON futures_mark_prices(symbol, interval, open_time)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_futures_funding_lookup "
        "ON futures_funding_rates(symbol, funding_time)"
    )


def create_schema(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        version = int(row[0]) if row and row[0] is not None else 0
        if version > SCHEMA_VERSION:
            raise SchemaMigrationRequired(
                f"database schema {version} is newer than supported {SCHEMA_VERSION}"
            )
        if version == 0:
            _create_v2_tables(connection)
            _create_v4_tables(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
                (SCHEMA_VERSION,),
            )
        elif version == 1:
            _migrate_v1_to_v2(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
                (2,),
            )
            _migrate_v2_to_v3(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
                (3,),
            )
            _create_v4_tables(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
                (SCHEMA_VERSION,),
            )
        elif version == 2:
            _create_v2_tables(connection)
            _migrate_v2_to_v3(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
                (3,),
            )
            _create_v4_tables(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
                (SCHEMA_VERSION,),
            )
        elif version == 3:
            _create_v2_tables(connection)
            _create_v4_tables(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
                (SCHEMA_VERSION,),
            )
        else:
            _create_v2_tables(connection)
            _create_v4_tables(connection)


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


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
        row = (
            connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            if "schema_migrations" in tables
            else None
        )
        return {
            "path": str(path),
            "exists": True,
            "schema_version": row[0] if row is not None else None,
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

    def upsert_candles(self, candles: tuple[Candle, ...]) -> int:
        if not candles:
            return 0
        with self._connection:
            self._connection.executemany(
                """INSERT INTO candles(
                    exchange, symbol, interval, open_time, close_time, open, high, low, close,
                    volume, quote_volume, trades_count, taker_buy_base_volume,
                    taker_buy_quote_volume, is_closed, collected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, symbol, interval, open_time) DO UPDATE SET
                    close_time=excluded.close_time, open=excluded.open, high=excluded.high,
                    low=excluded.low, close=excluded.close, volume=excluded.volume,
                    quote_volume=excluded.quote_volume, trades_count=excluded.trades_count,
                    taker_buy_base_volume=excluded.taker_buy_base_volume,
                    taker_buy_quote_volume=excluded.taker_buy_quote_volume,
                    is_closed=excluded.is_closed, collected_at=excluded.collected_at""",
                [
                    (
                        candle.exchange,
                        candle.symbol,
                        candle.interval,
                        candle.open_time.isoformat(),
                        candle.close_time.isoformat() if candle.close_time else None,
                        str(candle.open),
                        str(candle.high),
                        str(candle.low),
                        str(candle.close),
                        str(candle.volume),
                        str(candle.quote_volume) if candle.quote_volume is not None else None,
                        candle.trades_count,
                        str(candle.taker_buy_base_volume)
                        if candle.taker_buy_base_volume is not None
                        else None,
                        str(candle.taker_buy_quote_volume)
                        if candle.taker_buy_quote_volume is not None
                        else None,
                        int(candle.is_closed),
                        candle.collected_at.isoformat() if candle.collected_at else None,
                    )
                    for candle in candles
                ],
            )
        return len(candles)

    def save_candle(self, candle: Candle) -> None:
        self.upsert_candles((candle,))

    def get_candles(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int | None = None,
        closed_only: bool = True,
    ) -> tuple[Candle, ...]:
        clauses = ["exchange = 'BINANCE'", "symbol = ?", "interval = ?"]
        parameters: list[str | int] = [symbol, interval]
        if start_time is not None:
            clauses.append("open_time >= ?")
            parameters.append(start_time.isoformat())
        if end_time is not None:
            clauses.append("open_time <= ?")
            parameters.append(end_time.isoformat())
        if closed_only:
            clauses.append("is_closed = 1")
        statement = "SELECT * FROM candles WHERE " + " AND ".join(clauses)
        statement += " ORDER BY open_time ASC"
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive")
            statement += " LIMIT ?"
            parameters.append(limit)
        rows = self._connection.execute(statement, parameters).fetchall()
        return tuple(self._row_to_candle(row) for row in rows)

    def latest_candle(self, symbol: str, interval: str) -> Candle | None:
        row = self._connection.execute(
            "SELECT * FROM candles WHERE exchange = 'BINANCE' AND symbol = ? AND interval = ? "
            "AND is_closed = 1 ORDER BY open_time DESC LIMIT 1",
            (symbol, interval),
        ).fetchone()
        return self._row_to_candle(row) if row is not None else None

    def latest_candles(self, symbol: str, interval: str, limit: int) -> tuple[Candle, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        rows = self._connection.execute(
            "SELECT * FROM candles WHERE exchange = 'BINANCE' AND symbol = ? "
            "AND interval = ? AND is_closed = 1 ORDER BY open_time DESC LIMIT ?",
            (symbol, interval, limit),
        ).fetchall()
        return tuple(self._row_to_candle(row) for row in reversed(rows))

    def count_candles(self, symbol: str, interval: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM candles "
            "WHERE exchange = 'BINANCE' AND symbol = ? AND interval = ?",
            (symbol, interval),
        ).fetchone()
        return int(row[0]) if row else 0

    def upsert_futures_candles(self, candles: tuple[FuturesCandle, ...]) -> int:
        if not candles:
            return 0
        with self._connection:
            self._connection.executemany(
                """INSERT INTO futures_candles(
                    exchange, market_type, contract_type, symbol, interval, open_time,
                    close_time, open, high, low, close, volume, quote_volume, trade_count,
                    is_closed, collected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    exchange, market_type, contract_type, symbol, interval, open_time
                ) DO UPDATE SET
                    close_time=excluded.close_time, open=excluded.open, high=excluded.high,
                    low=excluded.low, close=excluded.close, volume=excluded.volume,
                    quote_volume=excluded.quote_volume, trade_count=excluded.trade_count,
                    is_closed=excluded.is_closed, collected_at=excluded.collected_at""",
                [
                    (
                        candle.exchange,
                        candle.market_type.value,
                        candle.contract_type.value,
                        candle.symbol,
                        candle.interval,
                        candle.open_time.isoformat(),
                        candle.close_time.isoformat(),
                        str(candle.open),
                        str(candle.high),
                        str(candle.low),
                        str(candle.close),
                        str(candle.volume),
                        str(candle.quote_volume),
                        candle.trade_count,
                        int(candle.is_closed),
                        candle.collected_at.isoformat() if candle.collected_at else None,
                    )
                    for candle in candles
                ],
            )
        return len(candles)

    def get_futures_candles(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        closed_only: bool = True,
    ) -> tuple[FuturesCandle, ...]:
        clauses = [
            "market_type = ?",
            "contract_type = ?",
            "symbol = ?",
            "interval = ?",
        ]
        parameters = [
            MarketType.USD_M_FUTURES.value,
            ContractType.PERPETUAL.value,
            symbol,
            interval,
        ]
        if start_time is not None:
            clauses.append("open_time >= ?")
            parameters.append(start_time.isoformat())
        if end_time is not None:
            clauses.append("open_time <= ?")
            parameters.append(end_time.isoformat())
        if closed_only:
            clauses.append("is_closed = 1")
        rows = self._connection.execute(
            "SELECT * FROM futures_candles WHERE "
            + " AND ".join(clauses)
            + " ORDER BY open_time",
            parameters,
        ).fetchall()
        return tuple(self._row_to_futures_candle(row) for row in rows)

    def latest_futures_candle(self, symbol: str, interval: str) -> FuturesCandle | None:
        row = self._connection.execute(
            "SELECT * FROM futures_candles WHERE market_type = ? AND contract_type = ? "
            "AND symbol = ? AND interval = ? AND is_closed = 1 "
            "ORDER BY open_time DESC LIMIT 1",
            (
                MarketType.USD_M_FUTURES.value,
                ContractType.PERPETUAL.value,
                symbol,
                interval,
            ),
        ).fetchone()
        return self._row_to_futures_candle(row) if row is not None else None

    def count_futures_candles(self, symbol: str, interval: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM futures_candles WHERE market_type = ? "
            "AND contract_type = ? AND symbol = ? AND interval = ?",
            (
                MarketType.USD_M_FUTURES.value,
                ContractType.PERPETUAL.value,
                symbol,
                interval,
            ),
        ).fetchone()
        return int(row[0]) if row else 0

    def upsert_mark_prices(self, prices: tuple[MarkPriceCandle, ...]) -> int:
        if not prices:
            return 0
        with self._connection:
            self._connection.executemany(
                """INSERT INTO futures_mark_prices(
                    symbol, interval, open_time, close_time, open, high, low, close,
                    index_price, estimated_settle_price, is_closed, collected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
                    close_time=excluded.close_time, open=excluded.open, high=excluded.high,
                    low=excluded.low, close=excluded.close,
                    index_price=excluded.index_price,
                    estimated_settle_price=excluded.estimated_settle_price,
                    is_closed=excluded.is_closed, collected_at=excluded.collected_at""",
                [
                    (
                        item.symbol,
                        item.interval,
                        item.open_time.isoformat(),
                        item.close_time.isoformat(),
                        str(item.open),
                        str(item.high),
                        str(item.low),
                        str(item.close),
                        str(item.index_price) if item.index_price is not None else None,
                        str(item.estimated_settle_price)
                        if item.estimated_settle_price is not None
                        else None,
                        int(item.is_closed),
                        item.collected_at.isoformat() if item.collected_at else None,
                    )
                    for item in prices
                ],
            )
        return len(prices)

    def get_mark_prices(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        closed_only: bool = True,
    ) -> tuple[MarkPriceCandle, ...]:
        clauses = ["symbol = ?", "interval = ?"]
        parameters = [symbol, interval]
        if start_time is not None:
            clauses.append("open_time >= ?")
            parameters.append(start_time.isoformat())
        if end_time is not None:
            clauses.append("open_time <= ?")
            parameters.append(end_time.isoformat())
        if closed_only:
            clauses.append("is_closed = 1")
        rows = self._connection.execute(
            "SELECT * FROM futures_mark_prices WHERE "
            + " AND ".join(clauses)
            + " ORDER BY open_time",
            parameters,
        ).fetchall()
        return tuple(self._row_to_mark_price(row) for row in rows)

    def count_mark_prices(self, symbol: str, interval: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM futures_mark_prices WHERE symbol = ? AND interval = ?",
            (symbol, interval),
        ).fetchone()
        return int(row[0]) if row else 0

    def upsert_funding_rates(self, rates: tuple[FundingRate, ...]) -> int:
        if not rates:
            return 0
        with self._connection:
            self._connection.executemany(
                """INSERT INTO futures_funding_rates(
                    symbol, funding_time, funding_rate, mark_price
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, funding_time) DO UPDATE SET
                    funding_rate=excluded.funding_rate, mark_price=excluded.mark_price""",
                [
                    (
                        item.symbol,
                        item.funding_time.isoformat(),
                        str(item.funding_rate),
                        str(item.mark_price) if item.mark_price is not None else None,
                    )
                    for item in rates
                ],
            )
        return len(rates)

    def get_funding_rates(
        self,
        symbol: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[FundingRate, ...]:
        clauses = ["symbol = ?"]
        parameters = [symbol]
        if start_time is not None:
            clauses.append("funding_time >= ?")
            parameters.append(start_time.isoformat())
        if end_time is not None:
            clauses.append("funding_time <= ?")
            parameters.append(end_time.isoformat())
        rows = self._connection.execute(
            "SELECT * FROM futures_funding_rates WHERE "
            + " AND ".join(clauses)
            + " ORDER BY funding_time",
            parameters,
        ).fetchall()
        return tuple(
            FundingRate(
                symbol=str(row["symbol"]),
                funding_time=_dt(str(row["funding_time"])),
                funding_rate=Decimal(str(row["funding_rate"])),
                mark_price=_decimal_or_none(row["mark_price"]),
            )
            for row in rows
        )

    def count_funding_rates(self, symbol: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM futures_funding_rates WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _row_to_futures_candle(row: sqlite3.Row) -> FuturesCandle:
        return FuturesCandle(
            exchange=str(row["exchange"]),
            market_type=MarketType(str(row["market_type"])),
            contract_type=ContractType(str(row["contract_type"])),
            symbol=str(row["symbol"]),
            interval=str(row["interval"]),
            open_time=_dt(str(row["open_time"])),
            close_time=_dt(str(row["close_time"])),
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=Decimal(str(row["volume"])),
            quote_volume=Decimal(str(row["quote_volume"])),
            trade_count=int(row["trade_count"]),
            is_closed=bool(row["is_closed"]),
            collected_at=_dt(str(row["collected_at"])) if row["collected_at"] else None,
        )

    @staticmethod
    def _row_to_mark_price(row: sqlite3.Row) -> MarkPriceCandle:
        return MarkPriceCandle(
            symbol=str(row["symbol"]),
            interval=str(row["interval"]),
            open_time=_dt(str(row["open_time"])),
            close_time=_dt(str(row["close_time"])),
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            index_price=_decimal_or_none(row["index_price"]),
            estimated_settle_price=_decimal_or_none(row["estimated_settle_price"]),
            is_closed=bool(row["is_closed"]),
            collected_at=_dt(str(row["collected_at"])) if row["collected_at"] else None,
        )

    def _row_to_candle(self, row: sqlite3.Row) -> Candle:
        return Candle(
            exchange=str(row["exchange"]),
            symbol=str(row["symbol"]),
            interval=str(row["interval"]),
            timestamp=_dt(str(row["open_time"])),
            close_time=_dt(str(row["close_time"])) if row["close_time"] else None,
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=Decimal(str(row["volume"])),
            quote_volume=_decimal_or_none(row["quote_volume"]),
            trades_count=int(row["trades_count"]) if row["trades_count"] is not None else None,
            taker_buy_base_volume=_decimal_or_none(row["taker_buy_base_volume"]),
            taker_buy_quote_volume=_decimal_or_none(row["taker_buy_quote_volume"]),
            is_closed=bool(row["is_closed"]),
            collected_at=_dt(str(row["collected_at"])) if row["collected_at"] else None,
        )

    def save_strategy_decision(self, record: StrategyDecisionRecord) -> None:
        data = serialize_model(record)
        signal = data["signal"]
        if not isinstance(signal, dict):
            raise TypeError("serialized signal must be an object")
        with self._connection:
            self._connection.execute(
                """INSERT OR REPLACE INTO strategy_decisions VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                """INSERT OR REPLACE INTO simulated_orders VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        "reference_price",
                        "fee",
                        "slippage_cost",
                        "spread_cost",
                    )
                ),
            )

    def save_fill(self, fill: Fill) -> None:
        data = serialize_model(fill)
        with self._connection:
            self._connection.execute(
                """INSERT OR REPLACE INTO fills VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        "reference_price",
                        "slippage_cost",
                        "spread_cost",
                    )
                ),
            )

    def save_position(self, position: Position) -> None:
        data = serialize_model(position)
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(
                    data[name]
                    for name in (
                        "position_id",
                        "symbol",
                        "quantity",
                        "average_entry_price",
                        "current_price",
                        "opened_at",
                        "stop_loss",
                        "take_profit",
                        "initial_risk",
                        "entry_fee",
                        "partial_taken",
                    )
                ),
            )

    def save_portfolio_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        data = serialize_model(snapshot)
        with self._connection:
            self._connection.execute(
                """INSERT OR REPLACE INTO portfolio_snapshots(
                    snapshot_id, captured_at, cash_balance, equity, daily_loss,
                    trades_today, positions_json, day_start_equity, entries_today,
                    orders_today, closed_trades_today
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["snapshot_id"],
                    data["captured_at"],
                    data["cash_balance"],
                    data["equity"],
                    data["daily_loss"],
                    data["orders_today"],
                    json.dumps(data["positions"], sort_keys=True),
                    data["day_start_equity"],
                    data["entries_today"],
                    data["orders_today"],
                    data["closed_trades_today"],
                ),
            )
