"""Artifact writer for the pre-registered real Futures 1x validation."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from adaptive_trader.domain.models import serialize_model
from adaptive_trader.futures.real_validation import RealValidationBundle

PUBLIC_ENDPOINTS = (
    "GET /fapi/v1/klines",
    "GET /fapi/v1/markPriceKlines",
    "GET /fapi/v1/fundingRate",
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(serialize_model(value), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, object]],
    *,
    empty_fields: tuple[str, ...],
) -> None:
    fieldnames = list(empty_fields)
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            serialized = serialize_model(row)
            if not isinstance(serialized, dict):
                raise TypeError("CSV row serialization must produce a mapping")
            writer.writerow(
                {
                    key: serialized.get(key, "")
                    if not isinstance(serialized.get(key), (dict, list))
                    else json.dumps(serialized.get(key), sort_keys=True)
                    for key in fieldnames
                }
            )


def write_real_validation_report(
    bundle: RealValidationBundle,
    output_root: Path,
    *,
    git_commit: str,
    git_dirty: bool,
    download_audit: dict[str, object] | None = None,
) -> Path:
    output_dir = output_root / bundle.experiment_id
    output_dir.mkdir(parents=True, exist_ok=False)
    integrity = bundle.integrity
    manifest = {
        "experiment_id": bundle.experiment_id,
        "experiment_version": "FUTURES_REAL_VALIDATION_1X_V1",
        "started_at": bundle.started_at,
        "completed_at": bundle.completed_at,
        "duration_seconds": bundle.duration_seconds,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "schema_version": 4,
        "source": "BINANCE_USD_M_PUBLIC_SQLITE",
        "endpoints": PUBLIC_ENDPOINTS,
        "authenticated_api_used": False,
        "api_key_used": False,
        "external_orders_sent": False,
        "paper_trading_enabled": False,
        "automatic_download": False,
        "symbol": integrity.candles.symbol,
        "interval": integrity.candles.interval,
        "market_type": integrity.candles.market_type,
        "contract_type": integrity.candles.contract_type,
        "periods": bundle.periods,
        "excluded_consumed_test": {
            "start": bundle.periods.consumed_test_start,
            "end": bundle.periods.consumed_test_end,
            "downloaded": False,
            "loaded": False,
            "used": False,
        },
        "counts": {
            "candles": integrity.candles.count,
            "mark_prices": integrity.marks.count,
            "funding_events": integrity.funding.event_count,
        },
        "hashes": {
            "futures_candle_hash": integrity.futures_candle_hash,
            "mark_price_hash": integrity.mark_price_hash,
            "funding_hash": integrity.funding_hash,
            "combined_dataset_hash": integrity.combined_dataset_hash,
        },
        "alignment_policy": integrity.marks.alignment_policy,
        "gap_policy": integrity.candles.gap_policy,
        "mark_missing_policy": "FAIL",
        "funding_missing_policy": "FAIL",
        "funding_policy": "REAL_FUNDING_ENABLED",
        "liquidation_policy": "LIQUIDATION_FIRST_THEN_STOP_FIRST",
        "event_priority": (
            "FUNDING",
            "MARK_UPDATE",
            "LIQUIDATION",
            "STOP",
            "TAKE_PROFIT",
            "TIME_EXIT",
            "SIGNAL_EXIT",
            "FORCED_END",
        ),
        "maintenance_model": "FIXED_RATE_APPROXIMATION",
        "configurations": tuple(item.variant_id for item in bundle.variants),
        "leverage": "1",
        "leverages_executed": ("1",),
        "cost_scenarios": ("LOW_COST", "BASE_COST", "HIGH_COST", "STRESS_COST"),
        "funding_diagnostic": {
            "scenario": "FUNDING_DISABLED_EXPLICITLY",
            "candidate_assessment_eligible": False,
            "warning": "FUNDING_DISABLED_DIAGNOSTIC_ONLY",
        },
        "walk_forward": {
            "train_days": 365,
            "validation_days": 90,
            "step_days": 90,
            "mode": "ROLLING",
            "fixed_parameters": True,
            "adaptive_selection": False,
        },
        "readiness": integrity.readiness,
        "warnings": bundle.warnings,
        "result_affecting_corrections": (
            {
                "issue": "historical funding markPrice may be an empty string",
                "correction": (
                    "parse empty optional markPrice as absent instead of rejecting the page"
                ),
            },
            {
                "issue": "absent funding markPrice previously fell back to mark close",
                "correction": "use current mark open to prevent hourly look-ahead",
            },
        ),
        "report_corrections": (
            {
                "issue": "benchmark rows were repeated once per strategy variant",
                "correction": "emit each period and benchmark pair exactly once",
            },
        ),
        "test_results": {
            "baseline": "178 passed before Sprint 3A.5 changes",
            "final": "run after artifact generation; recorded in delivery",
        },
        "download_audit": download_audit or {},
        "reproducibility_hash": bundle.reproducibility_hash,
        "candidate_frozen": False,
        "research_only": True,
    }
    _write_json(output_dir / "experiment_manifest.json", manifest)
    _write_json(output_dir / "futures_candle_integrity.json", integrity.candles)
    _write_csv(
        output_dir / "futures_candle_gaps.csv",
        tuple(serialize_model(item) for item in integrity.candle_gaps),
        empty_fields=(
            "previous_open_time",
            "next_open_time",
            "missing_candle_count",
            "documented",
        ),
    )
    _write_json(output_dir / "mark_price_integrity.json", integrity.marks)
    _write_csv(
        output_dir / "mark_price_alignment.csv",
        tuple(serialize_model(item) for item in integrity.mark_alignment),
        empty_fields=(
            "candle_open_time",
            "mark_open_time",
            "match_type",
            "alignment_delay_seconds",
            "future_match",
        ),
    )
    _write_json(output_dir / "funding_integrity.json", integrity.funding)
    funding_rows = tuple(
        {
            "symbol": item.symbol,
            "funding_time": item.funding_time,
            "funding_rate": item.funding_rate,
            "mark_price": item.mark_price,
        }
        for item in bundle.funding_events
    )
    _write_csv(
        output_dir / "funding_events.csv",
        funding_rows,
        empty_fields=("symbol", "funding_time", "funding_rate", "mark_price"),
    )
    _write_json(
        output_dir / "dataset_hashes.json",
        {
            "futures_candle_hash": integrity.futures_candle_hash,
            "mark_price_hash": integrity.mark_price_hash,
            "funding_hash": integrity.funding_hash,
            "combined_dataset_hash": integrity.combined_dataset_hash,
            "period_start": integrity.requested_start,
            "period_end": integrity.requested_end,
        },
    )
    _write_json(
        output_dir / "predefined_futures_variants.json",
        {
            "fixed_before_results": True,
            "automatic_selection": False,
            "variants": bundle.variants,
        },
    )
    _write_csv(
        output_dir / "segment_results.csv",
        bundle.segment_rows,
        empty_fields=("configuration", "period", "scenario"),
    )
    _write_csv(
        output_dir / "walk_forward_results.csv",
        bundle.walk_forward_rows,
        empty_fields=(
            "configuration",
            "period",
            "scenario",
            "fold",
            "train_start",
            "train_end",
            "validation_start",
            "validation_end",
        ),
    )
    _write_csv(
        output_dir / "cost_scenarios.csv",
        bundle.cost_rows,
        empty_fields=("configuration", "period", "scenario", "fold"),
    )
    _write_csv(
        output_dir / "funding_impact.csv",
        bundle.funding_rows,
        empty_fields=(
            "configuration",
            "period",
            "scenario",
            "diagnostic_only",
            "warning",
        ),
    )
    _write_csv(
        output_dir / "liquidation_events.csv",
        bundle.liquidation_rows,
        empty_fields=(
            "timestamp",
            "side",
            "entry",
            "mark",
            "liquidation_price",
            "quantity",
            "isolated_margin",
            "maintenance_margin",
            "wallet_before",
            "loss",
            "liquidation_fee",
            "wallet_after",
            "ambiguity",
            "fold",
            "configuration",
        ),
    )
    _write_csv(
        output_dir / "exit_reason_metrics.csv",
        bundle.exit_reason_rows,
        empty_fields=("configuration", "period", "exit_reason", "count", "net_pnl"),
    )
    _write_csv(
        output_dir / "regime_metrics.csv",
        bundle.regime_rows,
        empty_fields=("configuration", "period", "regime", "trades", "net_pnl"),
    )
    _write_csv(
        output_dir / "benchmarks.csv",
        bundle.benchmark_rows,
        empty_fields=(
            "period",
            "benchmark",
            "net_return_percent",
            "fees",
            "funding",
            "liquidations",
        ),
    )
    _write_csv(
        output_dir / "spot_futures_1x_comparison.csv",
        bundle.comparison_rows,
        empty_fields=(
            "configuration",
            "source_experiment",
            "market_type",
            "mode",
            "development_return_percent",
            "validation_return_percent",
            "status",
        ),
    )
    _write_json(
        output_dir / "spot_futures_1x_comparison.json",
        {
            "markets_combined": False,
            "leverage_above_one_executed": False,
            "experiments": bundle.comparison_rows,
        },
    )
    (output_dir / "spot_futures_1x_comparison.md").write_text(
        _comparison_markdown(bundle),
        encoding="utf-8",
    )
    _write_json(
        output_dir / "futures_candidate_assessment.json",
        {
            "candidate_frozen": False,
            "classifications": bundle.assessments,
            "promising_count": sum(
                item["status"] == "PROMISING_FOR_FURTHER_VALIDATION"
                for item in bundle.assessments
            ),
        },
    )
    (output_dir / "futures_real_validation_report.md").write_text(
        _report_markdown(bundle),
        encoding="utf-8",
    )
    return output_dir


def _comparison_markdown(bundle: RealValidationBundle) -> str:
    rows = "\n".join(
        f"| {item['configuration']} | {item['market_type']} | {item['mode']} | "
        f"{item['development_return_percent']} | {item['validation_return_percent']} | "
        f"{item['trades']} | {item['status']} |"
        for item in bundle.comparison_rows
    )
    return f"""# Spot versus Futures 1x

Os experimentos permanecem separados; retornos Spot e Futures nunca são somados. Nenhuma
alavancagem acima de 1x foi executada.

| Configuração | Mercado | Modo | Development % | Validation % | Trades | Status |
|---|---|---|---:|---:|---:|---|
{rows}
"""


def _report_markdown(bundle: RealValidationBundle) -> str:
    integrity = bundle.integrity
    classifications = "\n".join(
        f"- {item['configuration']}: **{item['status']}**"
        for item in bundle.assessments
    )
    warnings = "\n".join(f"- {item}" for item in bundle.warnings) or "- Nenhum"
    promising = sum(
        item["status"] == "PROMISING_FOR_FURTHER_VALIDATION"
        for item in bundle.assessments
    )
    return f"""# Validação real USD-M Futures 1x

## 1. Fontes públicas

Somente `GET /fapi/v1/klines`, `GET /fapi/v1/markPriceKlines` e
`GET /fapi/v1/fundingRate`, sem autenticação.

## 2. Períodos

- Development: {bundle.periods.development_start} a {bundle.periods.development_end}
- Validation: {bundle.periods.validation_start} a {bundle.periods.validation_end}

## 3. Exclusão de 2026

O período consumido {bundle.periods.consumed_test_start} a
{bundle.periods.consumed_test_end} foi somente registrado e não foi baixado, carregado ou usado.

## 4. Integridade dos candles

{integrity.candles.count} candles fechados; {integrity.candles.duplicate_count} duplicatas;
{integrity.candles.gap_count} gaps; hash `{integrity.futures_candle_hash}`.

## 5. Integridade do mark

Cobertura {integrity.marks.coverage_percent}%; exact={integrity.marks.exact_match_count};
previous={integrity.marks.previous_match_count}; missing={integrity.marks.missing_count};
future={integrity.marks.future_match_count}. Nunca há busca nearest bidirecional.

## 6. Integridade do funding

{integrity.funding.event_count} eventos; cobertura {integrity.funding.coverage_percent}%;
missing windows={integrity.funding.missing_windows}; hash `{integrity.funding_hash}`.

## 7. Hashes

Combined dataset hash: `{integrity.combined_dataset_hash}`.

## 8. Gaps

Política `WARN`; nenhum candle foi fabricado, interpolado ou preenchido silenciosamente.

## 9. Configurações fixas

As seis variantes em `predefined_futures_variants.json` foram executadas em 1x sem seleção
adaptativa.

## 10. Long

Resultados estão em `segment_results.csv` e `walk_forward_results.csv`.

## 11. Short

O short é a regra espelhada já existente; nenhuma regra foi ajustada após observar validation.

## 12. Long-short

Apenas uma posição isolada por vez, sem hedge simultâneo.

## 13. Walk-forward

Rolling 365/90/90, separado entre development e validation.

## 14. Custos

LOW, BASE, HIGH e STRESS alteram apenas custos de execução; funding real permanece inalterado.

## 15. Funding

`FUNDING_DISABLED_EXPLICITLY` é diagnóstico não elegível para assessment e emite
`FUNDING_DISABLED_DIAGNOSTIC_ONLY`.

## 16. Liquidações

Foram registradas {len(bundle.liquidation_rows)} liquidações. O modelo é aproximado,
`LIQUIDATION_FIRST`, seguido de `STOP_FIRST`.

## 17. Benchmarks

CASH, SPOT_BUY_AND_HOLD, FUTURES_LONG_1X e FUTURES_SHORT_1X são descritivos e não selecionáveis.

## 18. Comparação Spot

O checkpoint Spot conhecido permanece separado em `spot_futures_1x_comparison.*`.

## 19. Critérios

Todos os limites pré-registrados foram avaliados conjuntamente; retorno positivo isolado não basta.

## 20. Classificação

{classifications}

Configurações PROMISING_FOR_FURTHER_VALIDATION: {promising}.

## 21. Limitações

OHLC não contém caminho intrabar, fila, profundidade, impacto, partial fills nem tiers completos de
manutenção. O estudo não demonstra lucratividade nem equivalência com execução real.

As correções necessárias para dados reais foram aceitar `markPrice=""` como campo opcional ausente
e usar `mark.open` conhecido, nunca `mark.close` futuro, quando o evento de funding não fornece
preço. O primeiro relatório que usava close foi invalidado antes da entrega.

As linhas descritivas de benchmark são emitidas uma única vez por período e benchmark.

## 22. Próximos passos

Revisar integridade e consistência temporal. Não congelar candidata, habilitar paper trading,
executar 2x/3x ou consumir 2026 nesta sprint.

## Warnings

{warnings}
"""


def expected_artifact_names() -> tuple[str, ...]:
    return (
        "experiment_manifest.json",
        "futures_candle_integrity.json",
        "futures_candle_gaps.csv",
        "mark_price_integrity.json",
        "mark_price_alignment.csv",
        "funding_integrity.json",
        "funding_events.csv",
        "dataset_hashes.json",
        "predefined_futures_variants.json",
        "segment_results.csv",
        "walk_forward_results.csv",
        "cost_scenarios.csv",
        "funding_impact.csv",
        "liquidation_events.csv",
        "exit_reason_metrics.csv",
        "regime_metrics.csv",
        "benchmarks.csv",
        "spot_futures_1x_comparison.csv",
        "spot_futures_1x_comparison.json",
        "spot_futures_1x_comparison.md",
        "futures_candidate_assessment.json",
        "futures_real_validation_report.md",
    )
