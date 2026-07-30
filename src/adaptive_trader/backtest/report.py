"""JSON, CSV and terminal report writers."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from adaptive_trader.backtest.models import BacktestResult
from adaptive_trader.domain.models import serialize_model


def write_json(result: BacktestResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(serialize_model(result), indent=2, sort_keys=True), encoding="utf-8")


def write_trades_csv(result: BacktestResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "trade_id",
        "symbol",
        "quantity",
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "gross_pnl",
        "fees",
        "slippage_cost",
        "spread_cost",
        "net_pnl",
        "exit_reason",
        "intrabar_ambiguous",
        "holding_candles",
    ]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for trade in result.trades:
            writer.writerow({field: str(getattr(trade, field)) for field in fieldnames})


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read report: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("report JSON must contain an object")
    return payload


def render_summary(result: BacktestResult) -> str:
    metrics = result.metrics
    return "\n".join(
        (
            "BACKTEST ONLY — no real orders were sent",
            f"{result.symbol} {result.interval}: "
            f"{result.start_time.isoformat()} -> {result.end_time.isoformat()}",
            f"input_candles={result.input_candle_count} "
            f"warmup_candles={result.warmup_candle_count} "
            f"evaluated_candles={result.evaluated_candle_count or result.candle_count} "
            f"entries={metrics.entry_count} "
            f"orders={metrics.order_count} closed_trades={metrics.closed_trade_count} "
            f"partial_exits={metrics.partial_exit_count}",
            f"initial={metrics.initial_capital} final={metrics.final_capital} "
            f"net={metrics.net_return}",
            f"fees={metrics.total_fees} slippage={metrics.estimated_slippage} "
            f"spread={metrics.total_spread_cost}",
            f"win_rate={metrics.win_rate if metrics.win_rate is not None else 'N/A'}%",
            f"max_drawdown={metrics.maximum_drawdown_value} ({metrics.maximum_drawdown_percent}%)",
        )
    )
