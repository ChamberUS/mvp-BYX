"""CLI for public data collection and local backtesting."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from adaptive_trader.backtest.cli import build_engine
from adaptive_trader.backtest.report import read_json, render_summary, write_json, write_trades_csv
from adaptive_trader.config.settings import ConfigError, TradingConfig, load_config
from adaptive_trader.market_data.binance_public import BinancePublicClient
from adaptive_trader.market_data.exceptions import MarketDataError
from adaptive_trader.market_data.history import HistoricalCandleDownloader
from adaptive_trader.observability.logging import configure_logging
from adaptive_trader.storage.sqlite import (
    SCHEMA_VERSION,
    DatabaseRepository,
    SchemaMigrationRequired,
    database_status,
    initialize_database,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adaptive-trader")
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    config = commands.add_parser("config")
    config.add_subparsers(dest="config_command", required=True).add_parser("show")
    database = commands.add_parser("db")
    database_commands = database.add_subparsers(dest="db_command", required=True)
    database_commands.add_parser("init")
    database_commands.add_parser("status")
    market = commands.add_parser("market")
    market_commands = market.add_subparsers(dest="market_command", required=True)
    download = market_commands.add_parser("download")
    _add_market_range_args(download, required_dates=True)
    update = market_commands.add_parser("update")
    _add_market_range_args(update, required_dates=False)
    status = market_commands.add_parser("status")
    status.add_argument("--symbol", default=None)
    status.add_argument("--interval", default=None)
    backtest = commands.add_parser("backtest")
    backtest_commands = backtest.add_subparsers(dest="backtest_command", required=True)
    run = backtest_commands.add_parser("run")
    _add_market_range_args(run, required_dates=True)
    run.add_argument("--initial-balance", default=None)
    run.add_argument("--output", type=Path, required=True)
    show = backtest_commands.add_parser("show")
    show.add_argument("--file", type=Path, required=True)
    return parser


def _add_market_range_args(parser: argparse.ArgumentParser, *, required_dates: bool) -> None:
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--interval", default=None)
    parser.add_argument("--start", required=required_dates)
    parser.add_argument("--end", required=required_dates)


def _parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid datetime: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("dates must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_decimal(value: str, name: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a valid Decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return parsed


def _check(label: str, passed: bool, detail: str) -> bool:
    state = "OK" if passed else "FAIL"
    print(f"[{state}] {label}: {detail}")
    return passed


def _doctor(config: TradingConfig) -> int:
    no_credentials = not any(
        name in os.environ
        for name in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "ADAPTIVE_TRADER_API_KEY")
    )
    checks = [
        _check("python", sys.version_info >= (3, 12), sys.version.split()[0]),
        _check("configuration", True, "validated"),
        _check(
            "directories",
            config.database_path.parent.exists() or _create_parent(config.database_path),
            str(config.database_path.parent),
        ),
        _check("sqlite", _sqlite_check(config.database_path), str(config.database_path)),
        _check("research-only", config.is_research_only(), "trading_enabled=false and spot-only"),
        _check("credentials", no_credentials, "no authenticated Binance configuration found"),
    ]
    return 0 if all(checks) else 1


def _create_parent(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.parent.exists()


def _sqlite_check(path: Path) -> bool:
    try:
        initialize_database(path)
    except sqlite3.Error:
        return False
    return True


async def _download(config: TradingConfig, args: argparse.Namespace) -> int:
    symbol = args.symbol or config.symbol
    interval = args.interval or config.interval
    start = _parse_datetime(args.start) if args.start else None
    end = _parse_datetime(args.end) if args.end else None
    repository = DatabaseRepository(config.database_path)
    client = BinancePublicClient(
        timeout_seconds=config.request_timeout_seconds,
        maximum_retries=config.maximum_retries,
    )
    try:
        stats = await HistoricalCandleDownloader(client, repository).download(
            symbol=symbol,
            interval=interval,
            start_time=start,
            end_time=end,
            include_open_candle=config.include_open_candle,
            force=args.market_command == "download",
        )
    finally:
        await client.aclose()
        repository.close()
    print(
        json.dumps(
            {
                "pages": stats.pages,
                "received": stats.received,
                "ignored": stats.ignored,
                "persisted": stats.persisted,
            },
            sort_keys=True,
        )
    )
    return 0


def _market_status(config: TradingConfig, args: argparse.Namespace) -> int:
    symbol = args.symbol or config.symbol
    interval = args.interval or config.interval
    repository = DatabaseRepository(config.database_path)
    try:
        latest = repository.latest_candle(symbol, interval)
        payload = {
            "symbol": symbol,
            "interval": interval,
            "count": repository.count_candles(symbol, interval),
            "latest_open_time": latest.open_time.isoformat() if latest else None,
        }
    finally:
        repository.close()
    print(json.dumps(payload, sort_keys=True))
    return 0


def _backtest_run(config: TradingConfig, args: argparse.Namespace) -> int:
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end)
    if end < start:
        raise ValueError("end must not precede start")
    initial = (
        _parse_decimal(args.initial_balance, "initial_balance")
        if args.initial_balance
        else config.initial_balance
    )
    run_config = replace(
        config,
        symbol=args.symbol or config.symbol,
        interval=args.interval or config.interval,
        initial_balance=initial,
    )
    if not run_config.execute_on_next_candle_open:
        raise ValueError("backtest requires execution on next candle open")
    repository = DatabaseRepository(run_config.database_path)
    try:
        candles = repository.get_candles(
            run_config.symbol, run_config.interval, start_time=start, end_time=end
        )
        if not candles:
            raise ValueError("no persisted closed candles match the requested period")
        result = build_engine(run_config, repository=repository).run(candles)
    finally:
        repository.close()
    write_json(result, args.output)
    write_trades_csv(result, args.output.with_suffix(".csv"))
    print(render_summary(result))
    print(f"report={args.output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        config = load_config()
        if args.command == "doctor":
            return _doctor(config)
        if args.command == "config":
            print(json.dumps(config.as_dict(), indent=2, sort_keys=True))
            return 0
        if args.command == "db" and args.db_command == "init":
            initialize_database(config.database_path)
            print(f"database initialized: {config.database_path} schema={SCHEMA_VERSION}")
            return 0
        if args.command == "db" and args.db_command == "status":
            status = database_status(config.database_path)
            status["expected_schema_version"] = SCHEMA_VERSION
            print(json.dumps(status, indent=2, sort_keys=True))
            return 0
        if args.command == "market" and args.market_command in {"download", "update"}:
            return asyncio.run(_download(config, args))
        if args.command == "market" and args.market_command == "status":
            return _market_status(config, args)
        if args.command == "backtest" and args.backtest_command == "run":
            return _backtest_run(config, args)
        if args.command == "backtest" and args.backtest_command == "show":
            payload = read_json(args.file)
            metrics = payload.get("metrics", {})
            print(
                json.dumps(
                    {
                        "backtest_only": True,
                        "metrics": metrics,
                        "warnings": payload.get("warnings", []),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
    except (
        ConfigError,
        MarketDataError,
        SchemaMigrationRequired,
        ValueError,
        sqlite3.Error,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    raise RuntimeError("unsupported command")


if __name__ == "__main__":
    raise SystemExit(main())
