"""CLI for public data collection and local backtesting."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
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
from adaptive_trader.domain.market import MarketType, TradingMode
from adaptive_trader.domain.models import Candle, SignalDirection, serialize_model
from adaptive_trader.futures.datasets import FuturesDataset, validate_futures_dataset
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FundingRate,
    FuturesBacktestConfig,
    FuturesBacktestResult,
    FuturesCandle,
    MarkPriceCandle,
)
from adaptive_trader.futures.report import write_futures_report
from adaptive_trader.futures.research import (
    development_only_dataset,
    futures_benchmarks,
    futures_comparison_row,
    mode_from_cli,
    run_futures_backtest,
    run_futures_walk_forward,
    write_market_comparison,
    write_walk_forward_report,
)
from adaptive_trader.market_data.binance_futures_public import BinanceFuturesPublicClient
from adaptive_trader.market_data.binance_public import BinancePublicClient
from adaptive_trader.market_data.exceptions import MarketDataError
from adaptive_trader.market_data.futures_history import FuturesHistoricalDownloader
from adaptive_trader.market_data.history import HistoricalCandleDownloader
from adaptive_trader.observability.logging import configure_logging
from adaptive_trader.research.candidate_freeze import (
    freeze_candidate,
    inspect_candidate,
    verify_candidate,
)
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
from adaptive_trader.research.spot_experiment import SpotHypothesisExperiment
from adaptive_trader.research.spot_hypotheses import (
    SpotExperimentPeriods,
    load_spot_hypothesis_catalog,
)
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
    futures_market = market_commands.add_parser("futures")
    futures_market_commands = futures_market.add_subparsers(
        dest="futures_market_command",
        required=True,
    )
    futures_klines = futures_market_commands.add_parser("download-klines")
    _add_market_range_args(futures_klines, required_dates=True)
    futures_mark = futures_market_commands.add_parser("download-mark-price")
    _add_market_range_args(futures_mark, required_dates=True)
    futures_funding = futures_market_commands.add_parser("download-funding")
    futures_funding.add_argument("--symbol", default=None)
    futures_funding.add_argument("--start", required=True)
    futures_funding.add_argument("--end", required=True)
    futures_status = futures_market_commands.add_parser("status")
    futures_status.add_argument("--symbol", default=None)
    futures_status.add_argument("--interval", default=None)
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
    hypotheses = research_commands.add_parser("hypotheses")
    hypotheses_commands = hypotheses.add_subparsers(
        dest="hypotheses_market",
        required=True,
    )
    spot_hypotheses = hypotheses_commands.add_parser("spot")
    spot_hypotheses_commands = spot_hypotheses.add_subparsers(
        dest="spot_hypotheses_command",
        required=True,
    )
    spot_hypotheses_run = spot_hypotheses_commands.add_parser("run")
    spot_hypotheses_run.add_argument("--symbol", required=True)
    spot_hypotheses_run.add_argument("--interval", required=True)
    spot_hypotheses_run.add_argument("--development-start", required=True)
    spot_hypotheses_run.add_argument("--development-end", required=True)
    spot_hypotheses_run.add_argument("--validation-start", required=True)
    spot_hypotheses_run.add_argument("--validation-end", required=True)
    spot_hypotheses_run.add_argument("--consumed-test-start", required=True)
    spot_hypotheses_run.add_argument("--consumed-test-end", required=True)
    spot_hypotheses_run.add_argument("--output-dir", type=Path, required=True)
    spot_hypotheses_run.add_argument("--yes", action="store_true")
    spot_hypotheses_show = spot_hypotheses_commands.add_parser("show")
    spot_hypotheses_show.add_argument("--experiment", type=Path, required=True)
    candidate = research_commands.add_parser("candidate")
    candidate_commands = candidate.add_subparsers(
        dest="candidate_command",
        required=True,
    )
    candidate_freeze = candidate_commands.add_parser("freeze")
    candidate_freeze.add_argument("--experiment", type=Path, required=True)
    candidate_freeze.add_argument("--candidate-version", type=int, required=True)
    candidate_inspect = candidate_commands.add_parser("inspect")
    candidate_inspect.add_argument("--candidate", type=Path, required=True)
    candidate_verify = candidate_commands.add_parser("verify")
    candidate_verify.add_argument("--candidate", type=Path, required=True)
    futures_research = research_commands.add_parser("futures")
    futures_research_commands = futures_research.add_subparsers(
        dest="futures_research_command",
        required=True,
    )
    futures_inspect = futures_research_commands.add_parser("inspect")
    _add_market_range_args(futures_inspect, required_dates=True)
    _add_futures_research_options(futures_inspect, include_execution=False)
    futures_backtest = futures_research_commands.add_parser("backtest")
    _add_market_range_args(futures_backtest, required_dates=True)
    _add_futures_research_options(futures_backtest, include_execution=True)
    futures_backtest.add_argument("--output-dir", type=Path, required=True)
    futures_walk = futures_research_commands.add_parser("walk-forward")
    _add_market_range_args(futures_walk, required_dates=True)
    _add_futures_research_options(futures_walk, include_execution=True)
    futures_walk.add_argument("--train-days", type=int, required=True)
    futures_walk.add_argument("--validation-days", type=int, required=True)
    futures_walk.add_argument("--step-days", type=int, required=True)
    futures_walk.add_argument("--output-dir", type=Path, required=True)
    market_research = research_commands.add_parser("market")
    market_research_commands = market_research.add_subparsers(
        dest="research_market_command",
        required=True,
    )
    market_compare = market_research_commands.add_parser("compare")
    _add_market_range_args(market_compare, required_dates=True)
    market_compare.add_argument("--markets", default="spot,futures")
    market_compare.add_argument("--futures-modes", default="long,short,long-short")
    market_compare.add_argument("--leverages", default="1,2,3")
    market_compare.add_argument("--exclude-start", default="2026-01-01T00:00:00Z")
    market_compare.add_argument("--exclude-end", default="2026-07-01T00:00:00Z")
    market_compare.add_argument("--warmup-candles", type=int, default=None)
    market_compare.add_argument("--output-dir", type=Path, required=True)
    market_compare.add_argument("--yes", action="store_true")
    return parser


def _add_market_range_args(parser: argparse.ArgumentParser, *, required_dates: bool) -> None:
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--interval", default=None)
    parser.add_argument("--start", required=required_dates)
    parser.add_argument("--end", required=required_dates)


def _add_futures_research_options(
    parser: argparse.ArgumentParser,
    *,
    include_execution: bool,
) -> None:
    parser.add_argument(
        "--funding-missing-policy",
        choices=[item.value for item in FundingMissingPolicy],
        default=FundingMissingPolicy.FAIL.value,
    )
    parser.add_argument("--disable-funding", action="store_true")
    if include_execution:
        parser.add_argument(
            "--mode",
            choices=("long", "short", "long-short"),
            default="long-short",
        )
        parser.add_argument("--leverage", default="1")
        parser.add_argument("--warmup-candles", type=int, default=None)
        parser.add_argument("--time-exit-candles", type=int, default=None)
        parser.add_argument("--target-r", default="2")


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
        _check(
            "research-only",
            config.is_research_only(),
            "trading disabled; Spot default; Futures endpoints are public research-only",
        ),
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


async def _futures_download(config: TradingConfig, args: argparse.Namespace) -> int:
    symbol = args.symbol or config.symbol
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end)
    repository = DatabaseRepository(config.database_path)
    client = BinanceFuturesPublicClient(
        timeout_seconds=config.request_timeout_seconds,
        maximum_retries=config.maximum_retries,
    )
    downloader = FuturesHistoricalDownloader(client, repository)
    try:
        if args.futures_market_command == "download-klines":
            stats = await downloader.download_klines(
                symbol=symbol,
                interval=args.interval or config.interval,
                start_time=start,
                end_time=end,
            )
        elif args.futures_market_command == "download-mark-price":
            stats = await downloader.download_mark_prices(
                symbol=symbol,
                interval=args.interval or config.interval,
                start_time=start,
                end_time=end,
            )
        elif args.futures_market_command == "download-funding":
            stats = await downloader.download_funding(
                symbol=symbol,
                start_time=start,
                end_time=end,
            )
        else:
            raise ValueError("unsupported futures download command")
    finally:
        await client.aclose()
        repository.close()
    print(json.dumps(serialize_model(stats), indent=2, sort_keys=True))
    return 0


def _futures_status(config: TradingConfig, args: argparse.Namespace) -> int:
    symbol = args.symbol or config.symbol
    interval = args.interval or config.interval
    repository = DatabaseRepository(config.database_path)
    try:
        candles = repository.get_futures_candles(symbol, interval)
        marks = repository.get_mark_prices(symbol, interval)
        funding = repository.get_funding_rates(symbol)
    finally:
        repository.close()
    content = serialize_model(
        {
            "candles": candles,
            "mark_prices": marks,
            "funding_rates": funding,
        }
    )
    digest = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    print(
        json.dumps(
            {
                "market_type": MarketType.USD_M_FUTURES.value,
                "contract_type": "PERPETUAL",
                "symbol": symbol,
                "interval": interval,
                "candle_count": len(candles),
                "mark_price_count": len(marks),
                "funding_rate_count": len(funding),
                "latest_open_time": candles[-1].open_time.isoformat() if candles else None,
                "content_hash": digest,
                "range_semantics": "start_and_end_inclusive",
                "research_only": True,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _futures_policy(args: argparse.Namespace) -> tuple[bool, FundingMissingPolicy]:
    if args.disable_funding:
        return False, FundingMissingPolicy.DISABLE_EXPLICITLY
    policy = FundingMissingPolicy(args.funding_missing_policy)
    if policy is FundingMissingPolicy.DISABLE_EXPLICITLY:
        raise ValueError("DISABLE_EXPLICITLY requires --disable-funding")
    return True, policy


def _futures_config(
    config: TradingConfig,
    args: argparse.Namespace,
) -> FuturesBacktestConfig:
    funding_enabled, funding_policy = _futures_policy(args)
    return FuturesBacktestConfig(
        initial_balance=config.initial_balance,
        leverage=_parse_decimal(str(args.leverage), "leverage"),
        maximum_leverage=Decimal("3"),
        trading_mode=mode_from_cli(args.mode),
        funding_enabled=funding_enabled,
        funding_missing_policy=funding_policy,
        symbol=args.symbol or config.symbol,
        interval=args.interval or config.interval,
        warmup_candles=args.warmup_candles or config.warmup_candles,
        short_ema_period=config.short_ema_period,
        long_ema_period=config.long_ema_period,
        atr_period=config.atr_period,
        volume_period=config.volume_period,
        minimum_volume_ratio=config.minimum_volume_ratio,
        maximum_atr_relative=config.maximum_atr_relative,
        stop_atr_multiple=config.stop_atr_multiple,
        target_r_multiple=_parse_decimal(str(args.target_r), "target_r"),
        time_exit_candles=args.time_exit_candles,
    )


def _load_futures_dataset(
    config: TradingConfig,
    args: argparse.Namespace,
) -> FuturesDataset:
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end)
    if end < start:
        raise ValueError("end must not precede start")
    symbol = args.symbol or config.symbol
    interval = args.interval or config.interval
    funding_enabled, funding_policy = _futures_policy(args)
    repository = DatabaseRepository(config.database_path)
    try:
        candles = repository.get_futures_candles(
            symbol,
            interval,
            start_time=start,
            end_time=end,
        )
        marks = repository.get_mark_prices(
            symbol,
            interval,
            start_time=start,
            end_time=end,
        )
        funding = repository.get_funding_rates(
            symbol,
            start_time=start,
            end_time=end,
        )
    finally:
        repository.close()
    if not candles:
        raise ValueError("no local USD-M Futures candles; research never downloads automatically")
    return validate_futures_dataset(
        candles,
        marks,
        funding,
        source="BINANCE_USD_M_PUBLIC_SQLITE",
        funding_enabled=funding_enabled,
        funding_missing_policy=funding_policy,
    )


def _research_futures_inspect(config: TradingConfig, args: argparse.Namespace) -> int:
    dataset = _load_futures_dataset(config, args)
    payload = {
        "dataset_id": dataset.dataset_id,
        "market_type": dataset.market_type.value,
        "contract_type": dataset.contract_type.value,
        "symbol": dataset.symbol,
        "interval": dataset.interval,
        "first_open_time": dataset.candles[0].open_time.isoformat(),
        "last_open_time": dataset.candles[-1].open_time.isoformat(),
        "end_is_inclusive": True,
        "candle_count": len(dataset.candles),
        "mark_price_count": len(dataset.mark_prices),
        "funding_rate_count": len(dataset.funding_rates),
        "duplicate_count": dataset.duplicate_count,
        "gap_count": dataset.gap_count,
        "mark_price_missing_count": dataset.mark_price_missing_count,
        "funding_gap_count": dataset.funding_gap_count,
        "all_candles_closed": all(item.is_closed for item in dataset.candles),
        "candle_hash": dataset.candle_hash,
        "mark_price_hash": dataset.mark_price_hash,
        "funding_hash": dataset.funding_hash,
        "combined_dataset_hash": dataset.combined_dataset_hash,
        "valid_for_research": dataset.valid_for_research,
        "warnings": dataset.warnings,
    }
    print(json.dumps(serialize_model(payload), indent=2, sort_keys=True))
    return 0


def _research_futures_backtest(config: TradingConfig, args: argparse.Namespace) -> int:
    run_config = _futures_config(config, args)
    dataset = _load_futures_dataset(config, args)
    result = run_futures_backtest(dataset, run_config)
    files = write_futures_report(args.output_dir, dataset, run_config, result)
    (args.output_dir / "benchmarks.json").write_text(
        json.dumps(
            {"benchmarks": futures_benchmarks(dataset, run_config)},
            default=str,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "research_only": True,
                "metrics": serialize_model(result.metrics),
                "files": sorted((*files, "benchmarks.json")),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _research_futures_walk_forward(config: TradingConfig, args: argparse.Namespace) -> int:
    run_config = _futures_config(config, args)
    dataset = _load_futures_dataset(config, args)
    runs = run_futures_walk_forward(
        dataset,
        run_config,
        train_days=args.train_days,
        validation_days=args.validation_days,
        step_days=args.step_days,
    )
    files = write_walk_forward_report(args.output_dir, dataset, run_config, runs)
    print(
        json.dumps(
            {
                "research_only": True,
                "fold_count": len(runs),
                "automatic_selection": False,
                "files": files,
            },
            indent=2,
            sort_keys=True,
        )
    )
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


def _research_market_compare(config: TradingConfig, args: argparse.Namespace) -> int:
    if not args.yes:
        raise ValueError("market comparison requires --yes acknowledgement")
    markets = {item.strip().lower() for item in args.markets.split(",") if item.strip()}
    if not markets or markets - {"spot", "futures"}:
        raise ValueError("--markets supports only spot,futures")
    modes = tuple(item.strip() for item in args.futures_modes.split(",") if item.strip())
    if not modes or any(item not in {"long", "short", "long-short"} for item in modes):
        raise ValueError("unsupported futures mode in --futures-modes")
    leverages = tuple(
        sorted(
            {
                _parse_decimal(item.strip(), "leverage")
                for item in args.leverages.split(",")
                if item.strip()
            }
        )
    )
    if not leverages or any(item > Decimal("3") for item in leverages):
        raise ValueError("leverages must be in [1, 3]")
    if Decimal("1") not in leverages:
        leverages = (Decimal("1"), *leverages)
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end)
    exclude_start = _parse_datetime(args.exclude_start)
    exclude_end = _parse_datetime(args.exclude_end)
    if end < start or exclude_end < exclude_start:
        raise ValueError("invalid comparison period")
    symbol = args.symbol or config.symbol
    interval = args.interval or config.interval
    warmup = args.warmup_candles or config.warmup_candles
    rows: list[dict[str, object]] = []
    futures_candles: tuple[FuturesCandle, ...] = ()
    marks: tuple[MarkPriceCandle, ...] = ()
    funding: tuple[FundingRate, ...] = ()
    repository = DatabaseRepository(config.database_path)
    try:
        if "spot" in markets:
            spot_candles = tuple(
                item
                for item in repository.get_candles(
                    symbol,
                    interval,
                    start_time=start,
                    end_time=end,
                )
                if item.open_time < exclude_start
            )
            rows.append(_spot_comparison_row(config, spot_candles, symbol, interval, warmup))
        if "futures" in markets:
            futures_candles = repository.get_futures_candles(
                symbol,
                interval,
                start_time=start,
                end_time=end,
            )
            marks = repository.get_mark_prices(
                symbol,
                interval,
                start_time=start,
                end_time=end,
            )
            funding = repository.get_funding_rates(
                symbol,
                start_time=start,
                end_time=end,
            )
    finally:
        repository.close()
    if "futures" in markets:
        if not futures_candles:
            raise ValueError("no local Futures data; comparison never downloads automatically")
        base_config = FuturesBacktestConfig(
            initial_balance=config.initial_balance,
            funding_enabled=True,
            funding_missing_policy=FundingMissingPolicy.FAIL,
            symbol=symbol,
            interval=interval,
            warmup_candles=warmup,
            short_ema_period=config.short_ema_period,
            long_ema_period=config.long_ema_period,
            atr_period=config.atr_period,
            volume_period=config.volume_period,
            minimum_volume_ratio=config.minimum_volume_ratio,
            maximum_atr_relative=config.maximum_atr_relative,
            stop_atr_multiple=config.stop_atr_multiple,
            target_r_multiple=config.target_r_multiple,
        )
        complete_dataset = validate_futures_dataset(
            futures_candles,
            marks,
            funding,
            source="BINANCE_USD_M_PUBLIC_SQLITE",
        )
        safe_dataset = development_only_dataset(
            complete_dataset,
            consumed_test_start=exclude_start,
            consumed_test_end=exclude_end,
            config=base_config,
        )
        for mode_name in modes:
            one_x_candidate = False
            for leverage in leverages:
                run_config = replace(
                    base_config,
                    trading_mode=mode_from_cli(mode_name),
                    leverage=leverage,
                )
                result = run_futures_backtest(safe_dataset, run_config)
                if leverage == Decimal("1"):
                    one_x_candidate = _futures_candidate(result)
                prefix = {
                    "long": "FUTURES_LONG_BASELINE",
                    "short": "FUTURES_SHORT_MIRRORED",
                    "long-short": "FUTURES_LONG_SHORT",
                }[mode_name]
                rows.append(
                    futures_comparison_row(
                        f"{prefix}_{leverage}X",
                        result,
                        one_x_candidate=one_x_candidate,
                    )
                )
        for experiment, changes in (
            ("FUTURES_TIME_EXIT_12", {"time_exit_candles": 12}),
            ("FUTURES_TIME_EXIT_24", {"time_exit_candles": 24}),
            ("FUTURES_TARGET_R_2_5", {"target_r_multiple": Decimal("2.5")}),
        ):
            variant_config = replace(
                base_config,
                trading_mode=TradingMode.FUTURES_LONG_SHORT,
                leverage=Decimal("1"),
                **changes,
            )
            variant = run_futures_backtest(safe_dataset, variant_config)
            rows.append(
                futures_comparison_row(
                    experiment,
                    variant,
                    one_x_candidate=_futures_candidate(variant),
                )
            )
    paths = write_market_comparison(args.output_dir, tuple(rows))
    print(
        json.dumps(
            {
                "research_only": True,
                "consumed_test_used_for_selection": False,
                "automatic_selection": False,
                "experiments": len(rows),
                "files": [str(path) for path in paths],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _spot_comparison_row(
    config: TradingConfig,
    candles: tuple[Candle, ...],
    symbol: str,
    interval: str,
    warmup: int,
) -> dict[str, object]:
    if len(candles) <= warmup:
        raise ValueError("insufficient local Spot data after consumed-test exclusion")
    dataset = validate_dataset(candles, source="sqlite", gap_policy=GapPolicy.WARN)
    segment = _segment(
        dataset,
        name="SPOT_BASELINE",
        evaluation_start=dataset.start_time,
        evaluation_end=dataset.end_time + timedelta(microseconds=1),
        warmup_candles=warmup,
    )
    run = ResearchExperimentRunner().run_segment(
        segment,
        replace(config, symbol=symbol, interval=interval, warmup_candles=warmup),
    )
    if run.result is None:
        raise ValueError(run.error or "Spot comparison backtest failed")
    result = run.result
    metrics = result.metrics
    net_return = metrics.net_return / metrics.initial_capital * Decimal("100")
    drawdown = metrics.maximum_drawdown_percent
    return {
        "experiment": "SPOT_BASELINE",
        "market_type": MarketType.SPOT.value,
        "trading_mode": TradingMode.SPOT_LONG_ONLY.value,
        "leverage": Decimal("1"),
        "trade_count": metrics.closed_trade_count,
        "long_trades": metrics.closed_trade_count,
        "short_trades": 0,
        "net_return": net_return,
        "maximum_drawdown": drawdown,
        "return_to_drawdown": net_return / drawdown if drawdown else Decimal("0"),
        "wallet_volatility": _equity_volatility(result.equity_curve),
        "exposure": metrics.average_exposure_percent,
        "fees": metrics.total_fees,
        "funding": Decimal("0"),
        "liquidations": 0,
        "margin_utilization": Decimal("0"),
        "worst_fold": net_return,
        "positive_fold_percent": Decimal("100") if net_return > 0 else Decimal("0"),
        "zero_trade_fold_percent": (
            Decimal("100") if metrics.closed_trade_count == 0 else Decimal("0")
        ),
        "candidate_status": (
            "CANDIDATE"
            if net_return > 0 and metrics.closed_trade_count >= 10
            else "NOT_CANDIDATE"
        ),
        "warnings": "",
    }


def _futures_candidate(result: FuturesBacktestResult) -> bool:
    return (
        result.metrics.net_pnl > 0
        and result.metrics.trade_count >= 10
        and result.metrics.liquidation_count == 0
    )


def _equity_volatility(curve: tuple[Decimal, ...]) -> Decimal:
    returns = tuple(
        (current - previous) / previous
        for previous, current in zip(curve, curve[1:], strict=False)
        if previous
    )
    if len(returns) < 2:
        return Decimal("0")
    mean = sum(returns, Decimal("0")) / Decimal(len(returns))
    variance = sum((item - mean) ** 2 for item in returns) / Decimal(len(returns))
    return variance.sqrt() * Decimal("100")


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


def _research_hypotheses_spot_run(
    config: TradingConfig,
    args: argparse.Namespace,
) -> int:
    if not args.yes:
        raise ValueError("controlled hypothesis execution requires --yes")
    periods = SpotExperimentPeriods(
        development_start=_parse_datetime(args.development_start),
        development_end=_parse_datetime(args.development_end),
        validation_start=_parse_datetime(args.validation_start),
        validation_end=_parse_datetime(args.validation_end),
        consumed_test_start=_parse_datetime(args.consumed_test_start),
        consumed_test_end=_parse_datetime(args.consumed_test_end),
    )
    periods.assert_pre_registered()
    if args.symbol != "ETHUSDT" or args.interval != "1h":
        raise ValueError("Sprint 3A.4 is pre-registered for ETHUSDT 1h only")
    repository = DatabaseRepository(config.database_path)
    try:
        candles = repository.get_candles(
            args.symbol,
            args.interval,
            start_time=periods.development_start,
            end_time=periods.validation_end,
        )
    finally:
        repository.close()
    dataset = validate_dataset(
        candles,
        source="sqlite-local-only",
        gap_policy=GapPolicy.WARN,
    )
    result = SpotHypothesisExperiment(
        config=config,
        dataset=dataset,
        periods=periods,
        catalog=load_spot_hypothesis_catalog(),
        output_dir=args.output_dir,
    ).run()
    print(
        json.dumps(
            {
                "research_only": True,
                "experiment_id": result.experiment_id,
                "output_path": str(result.output_path),
                "stage_one_winner": result.stage_one_selection.selected_variant_id,
                "final_variant": result.final_selection.selected_variant_id,
                "final_regime_mode": (
                    result.final_selection.selected_regime_mode.value
                    if result.final_selection.selected_regime_mode
                    else None
                ),
                "candidate_status": result.candidate_status,
                "duration_seconds": str(result.duration_seconds),
                "consumed_test_used": False,
                "network_used": False,
                "futures_executed": False,
                "external_orders_sent": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _research_hypotheses_spot_show(args: argparse.Namespace) -> int:
    payload = {
        "manifest": read_json(args.experiment / "experiment_manifest.json"),
        "criteria": read_json(args.experiment / "candidate_criteria.json"),
        "freeze_decision": read_json(
            args.experiment / "candidate_freeze_decision.json"
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _research_candidate_freeze(args: argparse.Namespace) -> int:
    files = freeze_candidate(args.experiment, args.candidate_version)
    print(
        json.dumps(
            {
                "candidate_id": files.candidate_id,
                "config_path": str(files.config_path),
                "manifest_path": str(files.manifest_path),
                "hash_path": str(files.hash_path),
                "config_hash": files.config_hash,
                "declaration": "NOT_APPROVED_FOR_PRODUCTION",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _research_candidate_inspect(args: argparse.Namespace) -> int:
    print(json.dumps(inspect_candidate(args.candidate), indent=2, sort_keys=True))
    return 0


def _research_candidate_verify(args: argparse.Namespace) -> int:
    print(json.dumps(verify_candidate(args.candidate), indent=2, sort_keys=True))
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
        if args.command == "market" and args.market_command == "futures":
            if args.futures_market_command == "status":
                return _futures_status(config, args)
            return asyncio.run(_futures_download(config, args))
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
        if args.command == "research" and args.research_command == "hypotheses":
            if args.spot_hypotheses_command == "run":
                return _research_hypotheses_spot_run(config, args)
            if args.spot_hypotheses_command == "show":
                return _research_hypotheses_spot_show(args)
        if args.command == "research" and args.research_command == "candidate":
            if args.candidate_command == "freeze":
                return _research_candidate_freeze(args)
            if args.candidate_command == "inspect":
                return _research_candidate_inspect(args)
            if args.candidate_command == "verify":
                return _research_candidate_verify(args)
        if args.command == "research" and args.research_command == "futures":
            if args.futures_research_command == "inspect":
                return _research_futures_inspect(config, args)
            if args.futures_research_command == "backtest":
                return _research_futures_backtest(config, args)
            if args.futures_research_command == "walk-forward":
                return _research_futures_walk_forward(config, args)
        if args.command == "research" and args.research_command == "market":
            if args.research_market_command == "compare":
                return _research_market_compare(config, args)
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
