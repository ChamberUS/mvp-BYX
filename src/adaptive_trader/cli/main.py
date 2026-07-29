"""CLI entry point for safe local research operations."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from adaptive_trader.config.settings import ConfigError, TradingConfig, load_config
from adaptive_trader.observability.logging import configure_logging
from adaptive_trader.storage.sqlite import SCHEMA_VERSION, database_status, initialize_database


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adaptive-trader")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    config = commands.add_parser("config")
    config.add_subparsers(dest="config_command", required=True).add_parser("show")
    database = commands.add_parser("db")
    database_commands = database.add_subparsers(dest="db_command", required=True)
    database_commands.add_parser("init")
    database_commands.add_parser("status")
    return parser


def _check(label: str, passed: bool, detail: str) -> bool:
    state = "OK" if passed else "FAIL"
    print(f"[{state}] {label}: {detail}")
    return passed


def _doctor(config: TradingConfig) -> int:
    checks = [
        _check("python", sys.version_info >= (3, 12), sys.version.split()[0]),
        _check("configuration", True, "validated"),
        _check(
            "directories",
            config.database_path.parent.exists() or _create_parent(config.database_path),
            str(config.database_path.parent),
        ),
        _check("sqlite", _sqlite_check(config.database_path), str(config.database_path)),
        _check("research-only", config.is_research_only(), "real trading configuration absent"),
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


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        config = load_config()
        if args.command == "doctor":
            return _doctor(config)
        if args.command == "config":
            print(json.dumps(config.as_dict(), indent=2, sort_keys=True))
            return 0
        if args.command == "db" and args.db_command == "init":
            initialize_database(config.database_path)
            print(f"database initialized: {config.database_path}")
            return 0
        if args.command == "db" and args.db_command == "status":
            status = database_status(config.database_path)
            status["expected_schema_version"] = SCHEMA_VERSION
            print(json.dumps(status, indent=2, sort_keys=True))
            return 0
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"sqlite error: {exc}", file=sys.stderr)
        return 3
    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
