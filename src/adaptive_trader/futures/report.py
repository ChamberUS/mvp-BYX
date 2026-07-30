"""Serializable Futures manifests and research-only report artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from adaptive_trader.domain.market import ContractType, MarginMode, MarketType, TradingMode
from adaptive_trader.domain.models import SerializedValue
from adaptive_trader.futures.datasets import FuturesDataset
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FuturesBacktestConfig,
    FuturesBacktestResult,
)


@dataclass(frozen=True, slots=True)
class FuturesManifest:
    experiment_id: str
    created_at: datetime
    market_type: MarketType
    contract_type: ContractType
    source: str
    leverage: Decimal
    margin_mode: MarginMode
    maintenance_margin_model: str
    liquidation_model: str
    liquidation_priority: str
    funding_policy: FundingMissingPolicy
    funding_hash: str
    mark_price_hash: str
    candle_hash: str
    combined_dataset_hash: str
    reproducibility_hash: str
    costs: dict[str, SerializedValue]
    strategy: str
    trading_mode: TradingMode
    consumed_test_exclusion: dict[str, SerializedValue]
    warnings: tuple[str, ...]
    research_only: bool = True
    authenticated_endpoints_used: bool = False


def build_manifest(
    dataset: FuturesDataset,
    config: FuturesBacktestConfig,
    result: FuturesBacktestResult,
    *,
    consumed_test_start: datetime | None = None,
    consumed_test_end: datetime | None = None,
) -> FuturesManifest:
    reproducibility_material = json.dumps(
        {
            "dataset": dataset.combined_dataset_hash,
            "config": config.as_dict(),
            "strategy": result.strategy_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    reproducibility_hash = hashlib.sha256(
        reproducibility_material.encode("utf-8")
    ).hexdigest()
    return FuturesManifest(
        experiment_id=f"futures-{reproducibility_hash[:16]}",
        created_at=datetime.now(tz=UTC),
        market_type=config.market_type,
        contract_type=config.contract_type,
        source=dataset.source,
        leverage=config.leverage,
        margin_mode=config.margin_mode,
        maintenance_margin_model="fixed_rate_approximation",
        liquidation_model="isolated_margin_fixed_maintenance_approximation",
        liquidation_priority=config.intrabar_policy.value,
        funding_policy=config.funding_missing_policy,
        funding_hash=dataset.funding_hash,
        mark_price_hash=dataset.mark_price_hash,
        candle_hash=dataset.candle_hash,
        combined_dataset_hash=dataset.combined_dataset_hash,
        reproducibility_hash=reproducibility_hash,
        costs={
            "maker_fee_bps": str(config.maker_fee_bps),
            "taker_fee_bps": str(config.taker_fee_bps),
            "spread_bps": str(config.spread_bps),
            "slippage_bps": str(config.slippage_bps),
            "liquidation_fee_rate": str(config.liquidation_fee_rate),
        },
        strategy=result.strategy_version,
        trading_mode=config.trading_mode,
        consumed_test_exclusion={
            "start": consumed_test_start.isoformat() if consumed_test_start else None,
            "end": consumed_test_end.isoformat() if consumed_test_end else None,
            "used_for_selection": False,
        },
        warnings=tuple(dict.fromkeys((*dataset.warnings, *result.warnings))),
    )


def _json(path: Path, value: object) -> None:
    def default(item: object) -> object:
        if isinstance(item, (Decimal, datetime)):
            return str(item) if isinstance(item, Decimal) else item.isoformat()
        if isinstance(item, Enum):
            return item.value
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        raise TypeError(f"cannot serialize {type(item).__name__}")

    path.write_text(
        json.dumps(value, default=default, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_futures_report(
    output_dir: Path,
    dataset: FuturesDataset,
    config: FuturesBacktestConfig,
    result: FuturesBacktestResult,
    *,
    fold_rows: tuple[dict[str, object], ...] = (),
    consumed_test_start: datetime | None = None,
    consumed_test_end: datetime | None = None,
) -> tuple[str, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(
        dataset,
        config,
        result,
        consumed_test_start=consumed_test_start,
        consumed_test_end=consumed_test_end,
    )
    _json(output_dir / "dataset.json", dataset)
    _json(output_dir / "manifest.json", manifest)
    _json(output_dir / "summary.json", {"metrics": result.metrics})
    _json(output_dir / "warnings.json", {"warnings": manifest.warnings})
    _json(output_dir / "trades.json", result.trades)
    with (output_dir / "folds.csv").open("w", encoding="utf-8", newline="") as file:
        fields = tuple(fold_rows[0]) if fold_rows else ("fold", "status")
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in fold_rows:
            writer.writerow({key: str(value) for key, value in row.items()})
    (output_dir / "report.md").write_text(
        _markdown(dataset, config, result, manifest),
        encoding="utf-8",
    )
    return tuple(path.name for path in sorted(output_dir.iterdir()) if path.is_file())


def _markdown(
    dataset: FuturesDataset,
    config: FuturesBacktestConfig,
    result: FuturesBacktestResult,
    manifest: FuturesManifest,
) -> str:
    metrics = result.metrics
    warnings = "\n".join(f"- {item}" for item in manifest.warnings) or "- None"
    return f"""# USD-M Futures research report

## Scope

- Research-only: yes
- Authenticated endpoints: no
- Market: {config.market_type}
- Contract: {config.contract_type}
- Margin: {config.margin_mode}
- Mode: {config.trading_mode}
- Leverage: {config.leverage}x

## Dataset

- Dataset: `{dataset.dataset_id}`
- Combined hash: `{dataset.combined_dataset_hash}`
- Candle hash: `{dataset.candle_hash}`
- Mark price hash: `{dataset.mark_price_hash}`
- Funding hash: `{dataset.funding_hash}`
- Range semantics: start and end are inclusive.

## Results

- Trades: {metrics.trade_count}
- Long/short: {metrics.long_trade_count}/{metrics.short_trade_count}
- Net PnL: {metrics.net_pnl}
- Return on wallet: {metrics.return_on_wallet}%
- Maximum drawdown: {metrics.maximum_drawdown}%
- Funding paid/received: {metrics.funding_paid}/{metrics.funding_received}
- Liquidations: {metrics.liquidation_count}
- Maximum position notional: {metrics.maximum_position_notional}
- Average initial margin: {metrics.average_initial_margin}
- Average margin utilization: {metrics.average_margin_utilization}%
- Minimum free balance: {metrics.minimum_free_balance}
- Bankrupt/depleted: {metrics.bankrupt}/{metrics.depleted}

## Warnings

{warnings}

## Limitations

Maintenance margin uses a fixed rate and liquidation is an OHLC approximation with
LIQUIDATION_FIRST priority. This does not reproduce Binance tiers or execution exactly.
Leverage changes exposure and liquidation risk; it does not create statistical edge.
No order, Testnet request, authenticated endpoint, paper trade, or real trade was used.
"""
