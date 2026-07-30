"""Fixed-parameter Futures research orchestration and comparisons."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.domain.models import serialize_model
from adaptive_trader.futures.accounting import funding_cash_flow
from adaptive_trader.futures.datasets import FuturesDataset, validate_futures_dataset
from adaptive_trader.futures.engine import FuturesBacktestEngine
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FuturesBacktestConfig,
    FuturesBacktestResult,
)
from adaptive_trader.futures.report import write_futures_report


@dataclass(frozen=True, slots=True)
class FuturesWalkForwardRun:
    fold: int
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    result: FuturesBacktestResult
    dataset_hash: str


def run_futures_backtest(
    dataset: FuturesDataset,
    config: FuturesBacktestConfig,
) -> FuturesBacktestResult:
    return FuturesBacktestEngine(config).run(
        dataset.candles,
        dataset.mark_prices,
        dataset.funding_rates,
    )


def run_futures_walk_forward(
    dataset: FuturesDataset,
    config: FuturesBacktestConfig,
    *,
    train_days: int,
    validation_days: int,
    step_days: int,
) -> tuple[FuturesWalkForwardRun, ...]:
    if min(train_days, validation_days, step_days) < 1:
        raise ValueError("walk-forward day windows must be positive")
    runs: list[FuturesWalkForwardRun] = []
    train_start = dataset.candles[0].open_time
    final_time = dataset.candles[-1].open_time
    fold = 1
    while True:
        train_end = train_start + timedelta(days=train_days)
        validation_start = train_end
        validation_end = validation_start + timedelta(days=validation_days)
        if validation_end > final_time:
            break
        evaluation = tuple(
            item
            for item in dataset.candles
            if validation_start <= item.open_time < validation_end
        )
        prior = tuple(
            item
            for item in dataset.candles
            if item.open_time < validation_start
        )[-config.warmup_candles :]
        candles = prior + evaluation
        if len(prior) < config.warmup_candles or not evaluation:
            train_start += timedelta(days=step_days)
            continue
        candle_times = {item.open_time for item in candles}
        marks = tuple(item for item in dataset.mark_prices if item.open_time in candle_times)
        funding = tuple(
            item
            for item in dataset.funding_rates
            if candles[0].open_time <= item.funding_time <= candles[-1].close_time
        )
        fold_dataset = validate_futures_dataset(
            candles,
            marks,
            funding,
            source=dataset.source,
            funding_enabled=config.funding_enabled,
            funding_missing_policy=config.funding_missing_policy,
            price_source=config.price_source,
        )
        result = run_futures_backtest(fold_dataset, config)
        runs.append(
            FuturesWalkForwardRun(
                fold=fold,
                train_start=train_start,
                train_end=train_end,
                validation_start=validation_start,
                validation_end=validation_end,
                result=result,
                dataset_hash=fold_dataset.combined_dataset_hash,
            )
        )
        fold += 1
        train_start += timedelta(days=step_days)
    if not runs:
        raise ValueError("no complete futures walk-forward folds")
    return tuple(runs)


def write_walk_forward_report(
    output_dir: Path,
    dataset: FuturesDataset,
    config: FuturesBacktestConfig,
    runs: tuple[FuturesWalkForwardRun, ...],
) -> tuple[str, ...]:
    fold_rows = tuple(
        {
            "fold": item.fold,
            "train_start": item.train_start.isoformat(),
            "train_end": item.train_end.isoformat(),
            "validation_start": item.validation_start.isoformat(),
            "validation_end": item.validation_end.isoformat(),
            "dataset_hash": item.dataset_hash,
            "trades": item.result.metrics.trade_count,
            "net_return": item.result.metrics.return_on_wallet,
            "maximum_drawdown": item.result.metrics.maximum_drawdown,
            "liquidations": item.result.metrics.liquidation_count,
        }
        for item in runs
    )
    files = write_futures_report(
        output_dir,
        dataset,
        config,
        runs[-1].result,
        fold_rows=fold_rows,
    )
    summary = {
        "fold_count": len(runs),
        "positive_fold_percent": (
            Decimal(sum(item.result.metrics.net_pnl > 0 for item in runs))
            / Decimal(len(runs))
            * Decimal("100")
        ),
        "zero_trade_fold_percent": (
            Decimal(sum(item.result.metrics.trade_count == 0 for item in runs))
            / Decimal(len(runs))
            * Decimal("100")
        ),
        "worst_fold": min(item.result.metrics.return_on_wallet for item in runs),
        "fixed_parameters": True,
        "automatic_selection": False,
    }
    (output_dir / "walk_forward_summary.json").write_text(
        json.dumps(serialize_model(summary), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return tuple(sorted((*files, "walk_forward_summary.json")))


def futures_benchmarks(
    dataset: FuturesDataset,
    config: FuturesBacktestConfig,
) -> tuple[dict[str, object], ...]:
    first = dataset.candles[config.warmup_candles]
    last = dataset.candles[-1]
    rows: list[dict[str, object]] = [
        {
            "benchmark": "CASH",
            "net_return": Decimal("0"),
            "fees": Decimal("0"),
            "funding": Decimal("0"),
            "leverage": Decimal("0"),
        }
    ]
    for side, name in (
        (PositionSide.LONG, "FUTURES_LONG_1X"),
        (PositionSide.SHORT, "FUTURES_SHORT_1X"),
    ):
        adverse = (config.spread_bps + config.slippage_bps) / Decimal("10000")
        entry = first.open * (
            Decimal("1") + adverse if side is PositionSide.LONG else Decimal("1") - adverse
        )
        exit_price = last.close * (
            Decimal("1") - adverse if side is PositionSide.LONG else Decimal("1") + adverse
        )
        fee_rate = config.taker_fee_bps / Decimal("10000")
        quantity = (
            config.initial_balance / (entry * (Decimal("1") + fee_rate))
        ).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        gross = (
            (exit_price - entry) * quantity
            if side is PositionSide.LONG
            else (entry - exit_price) * quantity
        )
        fees = (entry + exit_price) * quantity * fee_rate
        funding = sum(
            (
                funding_cash_flow(
                    side,
                    (item.mark_price or first.close) * quantity,
                    item.funding_rate,
                )
                for item in dataset.funding_rates
                if first.open_time <= item.funding_time <= last.close_time
            ),
            Decimal("0"),
        )
        net = gross - fees + funding
        rows.append(
            {
                "benchmark": name,
                "net_return": net / config.initial_balance * Decimal("100"),
                "fees": fees,
                "funding": funding,
                "leverage": Decimal("1"),
            }
        )
    return tuple(rows)


def development_only_dataset(
    dataset: FuturesDataset,
    *,
    consumed_test_start: datetime,
    consumed_test_end: datetime,
    config: FuturesBacktestConfig,
) -> FuturesDataset:
    if consumed_test_end < consumed_test_start:
        raise ValueError("consumed test end must not precede start")
    candles = tuple(item for item in dataset.candles if item.open_time < consumed_test_start)
    if len(candles) <= config.warmup_candles:
        raise ValueError("development dataset is too small after consumed-test exclusion")
    times = {item.open_time for item in candles}
    marks = tuple(item for item in dataset.mark_prices if item.open_time in times)
    funding = tuple(
        item
        for item in dataset.funding_rates
        if candles[0].open_time <= item.funding_time <= candles[-1].close_time
    )
    return validate_futures_dataset(
        candles,
        marks,
        funding,
        source=dataset.source,
        funding_enabled=config.funding_enabled,
        funding_missing_policy=config.funding_missing_policy,
        price_source=config.price_source,
    )


def write_market_comparison(
    output_dir: Path,
    rows: tuple[dict[str, object], ...],
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "market_comparison.json"
    csv_path = output_dir / "market_comparison.csv"
    markdown_path = output_dir / "market_comparison.md"
    json_path.write_text(
        json.dumps({"experiments": rows}, default=str, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    fields = (
        "experiment",
        "market_type",
        "trading_mode",
        "leverage",
        "trade_count",
        "long_trades",
        "short_trades",
        "net_return",
        "maximum_drawdown",
        "return_to_drawdown",
        "wallet_volatility",
        "exposure",
        "fees",
        "funding",
        "liquidations",
        "margin_utilization",
        "worst_fold",
        "positive_fold_percent",
        "zero_trade_fold_percent",
        "candidate_status",
        "warnings",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    lines = "\n".join(
        f"| {row.get('experiment')} | {row.get('market_type')} | "
        f"{row.get('leverage')} | {row.get('net_return')} | "
        f"{row.get('maximum_drawdown')} | {row.get('candidate_status')} |"
        for row in rows
    )
    markdown_path.write_text(
        """# Spot vs USD-M Futures research comparison

Results are separate experiments and are never added together. The consumed test interval is
excluded from all selection. Leverage is evaluated only after the corresponding 1x result.

| Experiment | Market | Leverage | Net return | Drawdown | Candidate |
|---|---:|---:|---:|---:|---|
"""
        + lines
        + "\n",
        encoding="utf-8",
    )
    return csv_path, json_path, markdown_path


def futures_comparison_row(
    experiment: str,
    result: FuturesBacktestResult,
    *,
    one_x_candidate: bool,
) -> dict[str, object]:
    metrics = result.metrics
    candidate = metrics.net_pnl > 0 and metrics.trade_count >= 10 and metrics.liquidation_count == 0
    warnings = list(result.warnings)
    if result.leverage > 1 and not one_x_candidate:
        candidate = False
        warnings.append("LEVERAGE_AMPLIFIES_NON_CANDIDATE")
    drawdown = metrics.maximum_drawdown
    return {
        "experiment": experiment,
        "market_type": result.market_type.value,
        "trading_mode": result.trading_mode.value,
        "leverage": result.leverage,
        "trade_count": metrics.trade_count,
        "long_trades": metrics.long_trade_count,
        "short_trades": metrics.short_trade_count,
        "net_return": metrics.return_on_wallet,
        "maximum_drawdown": drawdown,
        "return_to_drawdown": metrics.return_on_wallet / drawdown if drawdown else Decimal("0"),
        "wallet_volatility": _wallet_volatility(result.equity_curve),
        "exposure": metrics.exposure_long_percent + metrics.exposure_short_percent,
        "fees": metrics.trading_fees + metrics.liquidation_fees,
        "funding": metrics.net_funding,
        "liquidations": metrics.liquidation_count,
        "margin_utilization": metrics.average_margin_utilization,
        "worst_fold": metrics.return_on_wallet,
        "positive_fold_percent": Decimal("100") if metrics.net_pnl > 0 else Decimal("0"),
        "zero_trade_fold_percent": Decimal("100") if metrics.trade_count == 0 else Decimal("0"),
        "candidate_status": "CANDIDATE" if candidate else "NOT_CANDIDATE",
        "warnings": ";".join(dict.fromkeys(warnings)),
    }


def _wallet_volatility(curve: tuple[Decimal, ...]) -> Decimal:
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


def mode_from_cli(value: str) -> TradingMode:
    mapping = {
        "long": TradingMode.FUTURES_LONG_ONLY,
        "short": TradingMode.FUTURES_SHORT_ONLY,
        "long-short": TradingMode.FUTURES_LONG_SHORT,
    }
    try:
        return mapping[value]
    except KeyError as exc:
        raise ValueError(f"unsupported futures mode: {value}") from exc


def config_without_funding(config: FuturesBacktestConfig) -> FuturesBacktestConfig:
    return replace(
        config,
        funding_enabled=False,
        funding_missing_policy=FundingMissingPolicy.DISABLE_EXPLICITLY,
    )
