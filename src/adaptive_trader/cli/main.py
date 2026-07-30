"""CLI for public data collection and local backtesting."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from adaptive_trader.backtest.cli import build_engine
from adaptive_trader.backtest.report import read_json, render_summary, write_json, write_trades_csv
from adaptive_trader.config.settings import ConfigError, TradingConfig, load_config
from adaptive_trader.domain.models import Candle, SignalDirection, serialize_model
from adaptive_trader.market_data.binance_public import BinancePublicClient
from adaptive_trader.market_data.exceptions import MarketDataError
from adaptive_trader.market_data.history import HistoricalCandleDownloader
from adaptive_trader.observability.logging import configure_logging
from adaptive_trader.research.config import ResearchFileConfig, load_experiment_toml
from adaptive_trader.research.costs import run_cost_scenarios_by_fold
from adaptive_trader.research.datasets import (
    _segment,
    dataset_to_dict,
    holdout_split,
    validate_dataset,
)
from adaptive_trader.research.diagnostics import entry_exit_decomposition_rows
from adaptive_trader.research.experiment import ResearchExperimentRunner
from adaptive_trader.research.models import GapPolicy, SelectionMode, WalkForwardMode
from adaptive_trader.research.periods import ResearchPeriods
from adaptive_trader.research.service import (
    run_diagnostics_experiment,
    run_holdout_experiment,
    run_walk_forward_experiment,
)
from adaptive_trader.research.splits import build_walk_forward_plan
from adaptive_trader.research.walk_forward import WalkForwardRunner
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
    research = commands.add_parser("research")
    research_commands = research.add_subparsers(dest="research_command", required=True)
    dataset = research_commands.add_parser("dataset")
    dataset_commands = dataset.add_subparsers(dest="dataset_command", required=True)
    inspect = dataset_commands.add_parser("inspect")
    _add_market_range_args(inspect, required_dates=True)
    inspect.add_argument("--gap-policy", choices=[item.value for item in GapPolicy], default="WARN")
    holdout = research_commands.add_parser("holdout")
    holdout_commands = holdout.add_subparsers(dest="holdout_command", required=True)
    holdout_run = holdout_commands.add_parser("run")
    _add_market_range_args(holdout_run, required_dates=False)
    holdout_run.add_argument("--train-percent", default="60")
    holdout_run.add_argument("--validation-percent", default="20")
    holdout_run.add_argument("--test-percent", default="20")
    holdout_run.add_argument("--warmup-candles", type=int, default=None)
    holdout_run.add_argument(
        "--gap-policy", choices=[item.value for item in GapPolicy], default="WARN"
    )
    holdout_run.add_argument("--output-dir", type=Path, required=True)
    holdout_run.add_argument("--config", dest="config_file", type=Path, default=None)
    holdout_run.add_argument("--yes", action="store_true")
    walk = research_commands.add_parser("walk-forward")
    walk_commands = walk.add_subparsers(dest="walk_forward_command", required=True)
    walk_run = walk_commands.add_parser("run")
    _add_market_range_args(walk_run, required_dates=False)
    walk_run.add_argument("--train-days", type=int, required=True)
    walk_run.add_argument("--validation-days", type=int, required=True)
    walk_run.add_argument("--step-days", type=int, required=True)
    walk_run.add_argument("--warmup-candles", type=int, default=None)
    walk_run.add_argument(
        "--mode", choices=[item.value.lower() for item in WalkForwardMode], default="rolling"
    )
    walk_run.add_argument(
        "--gap-policy", choices=[item.value for item in GapPolicy], default="WARN"
    )
    walk_run.add_argument("--output-dir", type=Path, required=True)
    walk_run.add_argument("--config", dest="config_file", type=Path, default=None)
    walk_run.add_argument("--yes", action="store_true")
    sensitivity = research_commands.add_parser("sensitivity")
    sensitivity_commands = sensitivity.add_subparsers(dest="sensitivity_command", required=True)
    sensitivity_run = sensitivity_commands.add_parser("run")
    _add_market_range_args(sensitivity_run, required_dates=False)
    sensitivity_run.add_argument("--warmup-candles", type=int, default=None)
    sensitivity_run.add_argument("--train-percent", default="60")
    sensitivity_run.add_argument("--validation-percent", default="20")
    sensitivity_run.add_argument("--test-percent", default="20")
    sensitivity_run.add_argument(
        "--gap-policy", choices=[item.value for item in GapPolicy], default="WARN"
    )
    sensitivity_run.add_argument("--output-dir", type=Path, required=True)
    sensitivity_run.add_argument("--config", dest="config_file", type=Path, default=None)
    sensitivity_ofat_run = sensitivity_commands.add_parser("ofat")
    _add_market_range_args(sensitivity_ofat_run, required_dates=True)
    sensitivity_ofat_run.add_argument("--exclude-start", required=True)
    sensitivity_ofat_run.add_argument("--exclude-end", required=True)
    sensitivity_ofat_run.add_argument("--warmup-candles", type=int, default=None)
    sensitivity_ofat_run.add_argument(
        "--gap-policy", choices=[item.value for item in GapPolicy], default="WARN"
    )
    sensitivity_ofat_run.add_argument("--output-dir", type=Path, required=True)
    sensitivity_ofat_run.add_argument("--maximum-parameter-combinations", type=int, default=60)
    sensitivity_ofat_run.add_argument("--yes", action="store_true")
    diagnose = research_commands.add_parser("diagnose")
    diagnose_commands = diagnose.add_subparsers(dest="diagnose_command", required=True)
    diagnose_run = diagnose_commands.add_parser("run")
    _add_market_range_args(diagnose_run, required_dates=True)
    diagnose_run.add_argument("--exclude-start", required=True)
    diagnose_run.add_argument("--exclude-end", required=True)
    diagnose_run.add_argument("--warmup-candles", type=int, default=None)
    diagnose_run.add_argument(
        "--gap-policy", choices=[item.value for item in GapPolicy], default="WARN"
    )
    diagnose_run.add_argument("--output-dir", type=Path, required=True)
    diagnose_run.add_argument("--maximum-parameter-combinations", type=int, default=60)
    diagnose_run.add_argument("--yes", action="store_true")
    exits = research_commands.add_parser("exits")
    exits_commands = exits.add_subparsers(dest="exits_command", required=True)
    exits_compare = exits_commands.add_parser("compare")
    _add_market_range_args(exits_compare, required_dates=True)
    exits_compare.add_argument("--exclude-start", default="2026-01-01T00:00:00Z")
    exits_compare.add_argument("--exclude-end", default="2026-07-01T00:00:00Z")
    exits_compare.add_argument("--warmup-candles", type=int, default=None)
    exits_compare.add_argument(
        "--gap-policy", choices=[item.value for item in GapPolicy], default="WARN"
    )
    exits_compare.add_argument("--output-dir", type=Path, required=True)
    exits_compare.add_argument("--yes", action="store_true")
    costs = research_commands.add_parser("costs")
    costs_commands = costs.add_subparsers(dest="costs_command", required=True)
    costs_walk = costs_commands.add_parser("walk-forward")
    costs_walk.add_argument("--experiment", type=Path, required=True)
    timeframe = research_commands.add_parser("timeframe")
    timeframe_commands = timeframe.add_subparsers(dest="timeframe_command", required=True)
    timeframe_compare = timeframe_commands.add_parser("compare")
    timeframe_compare.add_argument("--symbol", required=True)
    timeframe_compare.add_argument("--intervals", required=True)
    timeframe_compare.add_argument("--start", required=True)
    timeframe_compare.add_argument("--end", required=True)
    timeframe_compare.add_argument("--exclude-start", default="2026-01-01T00:00:00Z")
    timeframe_compare.add_argument("--exclude-end", default="2026-07-01T00:00:00Z")
    timeframe_compare.add_argument("--warmup-candles", type=int, default=None)
    timeframe_compare.add_argument("--output-dir", type=Path, required=True)
    diagnostics = research_commands.add_parser("diagnostics")
    diagnostics_commands = diagnostics.add_subparsers(
        dest="diagnostics_command", required=True
    )
    diagnostics_show = diagnostics_commands.add_parser("show")
    diagnostics_show.add_argument("--experiment", type=Path, required=True)
    report = research_commands.add_parser("report")
    report_commands = report.add_subparsers(dest="report_command", required=True)
    report_show = report_commands.add_parser("show")
    report_show.add_argument("--experiment", type=Path, required=True)
    research_config = research_commands.add_parser("config")
    research_config_commands = research_config.add_subparsers(
        dest="research_config_command", required=True
    )
    research_config_show = research_config_commands.add_parser("show")
    research_config_show.add_argument("--file", type=Path, required=True)
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


def _research_dataset(config: TradingConfig, args: argparse.Namespace) -> int:
    candles = _research_candles(config, args)
    dataset = validate_dataset(
        candles,
        source="sqlite",
        gap_policy=GapPolicy(args.gap_policy),
    )
    print(json.dumps({"dataset": dataset_to_dict(dataset)}, indent=2, sort_keys=True))
    return 0


def _research_candles(
    config: TradingConfig, args: argparse.Namespace
) -> tuple[Candle, ...]:
    file_config = _research_file_config(args)
    if file_config is None and (args.start is None or args.end is None):
        raise ValueError("research dates are required unless --config is provided")
    start = file_config.start if file_config else _parse_datetime(args.start)
    end = file_config.end if file_config else _parse_datetime(args.end)
    if end <= start:
        raise ValueError("end must be after start")
    symbol = file_config.symbol if file_config else args.symbol or config.symbol
    interval = file_config.interval if file_config else args.interval or config.interval
    repository = DatabaseRepository(config.database_path)
    try:
        candles = repository.get_candles(symbol, interval, start_time=start, end_time=end)
    finally:
        repository.close()
    if not candles:
        raise ValueError("no persisted closed candles match the research period")
    return candles


def _research_file_config(args: argparse.Namespace) -> ResearchFileConfig | None:
    path = getattr(args, "config_file", None)
    return load_experiment_toml(path) if path is not None else None


def _research_config(config: TradingConfig, args: argparse.Namespace) -> TradingConfig:
    file_config = _research_file_config(args)
    symbol = file_config.symbol if file_config else args.symbol or config.symbol
    interval = file_config.interval if file_config else args.interval or config.interval
    warmup = (
        file_config.warmup_candles
        if file_config
        else args.warmup_candles
        if args.warmup_candles is not None
        else config.warmup_candles
    )
    if warmup < 1:
        raise ValueError("warmup_candles must be positive")
    return replace(config, symbol=symbol, interval=interval, warmup_candles=warmup)


def _research_holdout(config: TradingConfig, args: argparse.Namespace) -> int:
    if (
        getattr(args, "research_command", None) == "sensitivity"
        and getattr(args, "sensitivity_command", None) == "run"
    ):
        raise ValueError(
            "legacy sensitivity cannot use a consumed test period; "
            "use research sensitivity ofat with --exclude-start and --exclude-end"
        )
    run_config = _research_config(config, args)
    file_config = _research_file_config(args)
    gap_policy = file_config.gap_policy if file_config else GapPolicy(args.gap_policy)
    dataset = validate_dataset(
        _research_candles(run_config, args),
        source="sqlite",
        gap_policy=gap_policy,
    )
    split = holdout_split(
        dataset,
        train_percent=_parse_decimal(
            str(file_config.train_percent if file_config else getattr(args, "train_percent", "60")),
            "train_percent",
        ),
        validation_percent=_parse_decimal(
            str(
                file_config.validation_percent
                if file_config
                else getattr(args, "validation_percent", "20")
            ),
            "validation_percent",
        ),
        test_percent=_parse_decimal(
            str(file_config.test_percent if file_config else getattr(args, "test_percent", "20")),
            "test_percent",
        ),
        warmup_candles=run_config.warmup_candles,
    )
    result = run_holdout_experiment(
        dataset=dataset,
        split=split,
        config=run_config,
        experiment_name=file_config.experiment_name if file_config else "holdout",
        output_root=file_config.output_dir if file_config else args.output_dir,
        gap_policy=gap_policy.value,
        include_sensitivity=args.research_command == "sensitivity",
    )
    print(
        json.dumps(
            {"experiment_id": result.experiment_id, "summary": serialize_model(result.summary)},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _research_walk_forward(config: TradingConfig, args: argparse.Namespace) -> int:
    run_config = _research_config(config, args)
    file_config = _research_file_config(args)
    dataset = validate_dataset(
        _research_candles(run_config, args),
        source="sqlite",
        gap_policy=GapPolicy(args.gap_policy),
    )
    plan = build_walk_forward_plan(
        dataset,
        train_days=file_config.train_days if file_config else args.train_days,
        validation_days=file_config.validation_days if file_config else args.validation_days,
        step_days=file_config.step_days if file_config else args.step_days,
        warmup_candles=run_config.warmup_candles,
        mode=file_config.walk_mode if file_config else WalkForwardMode(args.mode.upper()),
    )
    results = run_walk_forward_experiment(
        dataset=dataset,
        plan=plan,
        config=run_config,
        experiment_name=file_config.experiment_name if file_config else "walk-forward",
        output_root=file_config.output_dir if file_config else args.output_dir,
        gap_policy=file_config.gap_policy.value if file_config else args.gap_policy,
    )
    print(
        json.dumps(
            {"fold_count": len(results), "plan_id": plan.plan_id},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _csv_value(value: object) -> str | int | bool:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, sort_keys=True)
    return value if isinstance(value, (str, int, bool)) else str(value)


def _write_rows_csv(path: Path, rows: tuple[dict[str, object], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields or ["status"])
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})


def _research_diagnose(config: TradingConfig, args: argparse.Namespace) -> int:
    run_config = _research_config(config, args)
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end)
    exclude_start = _parse_datetime(args.exclude_start)
    exclude_end = _parse_datetime(args.exclude_end)
    if end < start:
        raise ValueError("end must not precede start")
    if exclude_end < exclude_start:
        raise ValueError("exclude-end must not precede exclude-start")
    raw = _research_candles(run_config, args)
    safe_candles = tuple(candle for candle in raw if candle.open_time < exclude_start)
    if len(safe_candles) < 4:
        raise ValueError("diagnostic period has too few candles after consumed-test exclusion")
    dataset = validate_dataset(
        safe_candles,
        source="sqlite",
        gap_policy=GapPolicy(args.gap_policy),
    )
    split_index = max(1, min(len(safe_candles) - 1, len(safe_candles) * 80 // 100))
    validation_start = safe_candles[split_index].open_time
    validation_end = dataset.end_time
    periods = ResearchPeriods(
        development_start=dataset.start_time,
        development_end=validation_start - timedelta(microseconds=1),
        validation_start=validation_start,
        validation_end=validation_end,
        consumed_test_start=exclude_start,
        consumed_test_end=exclude_end,
    )
    periods.assert_not_consumed(
        dataset.start_time,
        validation_end,
        "diagnostic selection",
    )
    result = run_diagnostics_experiment(
        dataset=dataset,
        development_start=periods.development_start,
        development_end=validation_start,
        validation_start=periods.validation_start,
        validation_end=periods.validation_end + timedelta(microseconds=1),
        config=run_config,
        experiment_name="diagnose",
        output_root=args.output_dir,
        gap_policy=args.gap_policy,
        excluded_period=(exclude_start, exclude_end),
        maximum_parameter_combinations=args.maximum_parameter_combinations,
    )
    print(
        json.dumps(
            {
                "experiment_id": result.experiment_id,
                "consumed_test_used": False,
                "periods": periods.as_dict(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _research_timeframe_compare(config: TradingConfig, args: argparse.Namespace) -> int:
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end)
    exclude_start = _parse_datetime(args.exclude_start)
    exclude_end = _parse_datetime(args.exclude_end)
    if end < start:
        raise ValueError("end must not precede start")
    if exclude_end < exclude_start:
        raise ValueError("exclude-end must not precede exclude-start")
    if start <= exclude_end and end >= exclude_start:
        raise ValueError("timeframe comparison cannot use the consumed test period")
    warmup = args.warmup_candles or config.warmup_candles
    repository = DatabaseRepository(config.database_path)
    rows: list[dict[str, object]] = []
    try:
        for interval in tuple(item.strip() for item in args.intervals.split(",") if item.strip()):
            candles = repository.get_candles(
                args.symbol,
                interval,
                start_time=start,
                end_time=end,
            )
            if not candles:
                rows.append(
                    {
                        "symbol": args.symbol,
                        "interval": interval,
                        "available": False,
                        "warning": "INTERVAL_NOT_AVAILABLE_IN_DATABASE",
                    }
                )
                continue
            dataset = validate_dataset(candles, source="sqlite", gap_policy=GapPolicy.WARN)
            if len(candles) <= warmup:
                rows.append(
                    {
                        "symbol": args.symbol,
                        "interval": interval,
                        "available": True,
                        "warning": "INSUFFICIENT_CANDLES_FOR_WARMUP",
                    }
                )
                continue
            segment = _segment(
                dataset,
                name=interval,
                evaluation_start=dataset.start_time,
                evaluation_end=dataset.end_time + timedelta(microseconds=1),
                warmup_candles=warmup,
            )
            run_config = replace(
                config, symbol=args.symbol, interval=interval, warmup_candles=warmup
            )
            run = ResearchExperimentRunner().run_segment(segment, run_config)
            result = run.result
            benchmark = next(
                (item for item in run.benchmarks if item.name == "BUY_AND_HOLD"), None
            )
            duration_years = Decimal(
                str((segment.end_time - segment.start_time).total_seconds())
            ) / Decimal("31557600")
            rows.append(
                {
                    "symbol": args.symbol,
                    "interval": interval,
                    "available": True,
                    "candle_count": dataset.candle_count,
                    "trades": result.metrics.closed_trade_count if result else 0,
                    "trades_per_year": (
                        Decimal(result.metrics.closed_trade_count) / duration_years
                        if result and duration_years > 0
                        else None
                    ),
                    "net_return": (
                        result.metrics.net_return / result.metrics.initial_capital * Decimal("100")
                        if result
                        else None
                    ),
                    "drawdown": result.metrics.maximum_drawdown_percent if result else None,
                    "costs": (
                        result.metrics.total_fees
                        + result.metrics.estimated_slippage
                        + result.metrics.total_spread_cost
                        if result
                        else None
                    ),
                    "buy_and_hold": benchmark.net_return_percent if benchmark else None,
                    "positive_folds": None,
                    "signals": (
                        sum(
                            trace.signal_direction is SignalDirection.BUY
                            for trace in result.decision_traces
                        )
                        if result
                        else 0
                    ),
                    "warning": "" if result else run.error or "BACKTEST_FAILED",
                }
            )
    finally:
        repository.close()
    output = args.output_dir / "timeframe_comparison.csv"
    _write_rows_csv(output, tuple(rows))
    print(
        json.dumps(
            {"output": str(output), "intervals": rows},
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def _research_exits_compare(config: TradingConfig, args: argparse.Namespace) -> int:
    run_config = _research_config(config, args)
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end)
    exclude_start = _parse_datetime(args.exclude_start)
    exclude_end = _parse_datetime(args.exclude_end)
    if end < start or exclude_end < exclude_start:
        raise ValueError("exit comparison dates are invalid")
    raw = _research_candles(run_config, args)
    candles = tuple(candle for candle in raw if candle.open_time < exclude_start)
    dataset = validate_dataset(
        candles,
        source="sqlite",
        gap_policy=GapPolicy(args.gap_policy),
    )
    segment = _segment(
        dataset,
        name="exit-comparison",
        evaluation_start=dataset.start_time,
        evaluation_end=dataset.end_time + timedelta(microseconds=1),
        warmup_candles=run_config.warmup_candles,
    )
    rows = entry_exit_decomposition_rows(
        segment,
        run_config,
        ResearchExperimentRunner(),
    )
    output = args.output_dir / "entry_exit_decomposition.csv"
    _write_rows_csv(output, rows)
    print(json.dumps({"output": str(output), "scenario_count": len(rows)}, indent=2))
    return 0


def _research_diagnostics_show(args: argparse.Namespace) -> int:
    payload = read_json(args.experiment / "candidate_assessment.json")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _research_costs_walk_forward(config: TradingConfig, args: argparse.Namespace) -> int:
    experiment = args.experiment
    manifest = read_json(experiment / "manifest.json")
    dataset_payload = read_json(experiment / "dataset.json")
    plan_id = str(manifest.get("split", {}).get("plan_id", ""))
    match = re.fullmatch(r"(rolling|expanding)-(\d+)d-(\d+)d-(\d+)d", plan_id)
    if match is None:
        raise ValueError("experiment manifest does not contain a supported walk-forward plan")
    mode, train_days, validation_days, step_days = match.groups()
    symbol = str(dataset_payload["symbol"])
    interval = str(dataset_payload["interval"])
    start = _parse_datetime(str(dataset_payload["start_time"]))
    end = _parse_datetime(str(dataset_payload["last_close_time"]))
    repository = DatabaseRepository(config.database_path)
    try:
        candles = repository.get_candles(
            symbol,
            interval,
            start_time=start,
            end_time=end,
        )
    finally:
        repository.close()
    dataset = validate_dataset(candles, source="sqlite", gap_policy=GapPolicy.WARN)
    plan = build_walk_forward_plan(
        dataset,
        train_days=int(train_days),
        validation_days=int(validation_days),
        step_days=int(step_days),
        warmup_candles=config.warmup_candles,
        mode=WalkForwardMode(mode.upper()),
    )
    runner = ResearchExperimentRunner()
    results = WalkForwardRunner(runner).run(
        plan, config, selection_mode=SelectionMode.FIXED_PARAMETERS
    )
    validation_runs = tuple(item.validation for item in results if item.validation is not None)
    rows = run_cost_scenarios_by_fold(validation_runs, config, runner)
    output = experiment / "cost_scenarios_by_fold.csv"
    _write_rows_csv(output, rows)
    consolidated_output = experiment / "cost_scenarios.csv"
    _write_rows_csv(consolidated_output, rows)
    print(
        json.dumps(
            {
                "output": str(output),
                "consolidated_output": str(consolidated_output),
                "fold_count": len(validation_runs),
            },
            indent=2,
        )
    )
    return 0


def _research_report(args: argparse.Namespace) -> int:
    summary = read_json(args.experiment / "summary.json")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _research_config_show(args: argparse.Namespace) -> int:
    research_config = load_experiment_toml(args.file)
    print(json.dumps(serialize_model(research_config), indent=2, sort_keys=True))
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
        if args.command == "research" and args.research_command == "dataset":
            if args.dataset_command == "inspect":
                return _research_dataset(config, args)
        if args.command == "research" and args.research_command == "holdout":
            if args.holdout_command == "run":
                return _research_holdout(config, args)
        if args.command == "research" and args.research_command == "walk-forward":
            if args.walk_forward_command == "run":
                return _research_walk_forward(config, args)
        if args.command == "research" and args.research_command == "sensitivity":
            if args.sensitivity_command == "run":
                return _research_holdout(config, args)
            if args.sensitivity_command == "ofat":
                return _research_diagnose(config, args)
        if args.command == "research" and args.research_command == "diagnose":
            if args.diagnose_command == "run":
                return _research_diagnose(config, args)
        if args.command == "research" and args.research_command == "costs":
            if args.costs_command == "walk-forward":
                return _research_costs_walk_forward(config, args)
        if args.command == "research" and args.research_command == "exits":
            if args.exits_command == "compare":
                return _research_exits_compare(config, args)
        if args.command == "research" and args.research_command == "timeframe":
            if args.timeframe_command == "compare":
                return _research_timeframe_compare(config, args)
        if args.command == "research" and args.research_command == "diagnostics":
            if args.diagnostics_command == "show":
                return _research_diagnostics_show(args)
        if args.command == "research" and args.research_command == "report":
            if args.report_command == "show":
                return _research_report(args)
        if args.command == "research" and args.research_command == "config":
            if args.research_config_command == "show":
                return _research_config_show(args)
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
