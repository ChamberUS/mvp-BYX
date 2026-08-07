"""Artifact writer for Futures 1x temporal robustness diagnostics."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from adaptive_trader.domain.models import serialize_model
from adaptive_trader.futures.temporal_robustness import TemporalRobustnessBundle

_ARTIFACTS = (
    "experiment_manifest.json",
    "predefined_configurations.json",
    "yearly_results.csv",
    "quarterly_results.csv",
    "rolling_window_results.csv",
    "walk_forward_design_comparison.csv",
    "temporal_boundary_results.csv",
    "leave_one_year_out.csv",
    "temporal_regime_results.csv",
    "regime_transition_results.csv",
    "volatility_bucket_results.csv",
    "market_context_results.csv",
    "side_contribution.csv",
    "temporal_funding_impact.csv",
    "temporal_cost_impact.csv",
    "temporal_concentration.csv",
    "bootstrap_uncertainty.json",
    "bootstrap_by_configuration.csv",
    "temporal_stability_scorecard.json",
    "configuration_classification.json",
    "2025_result_explanation.json",
    "temporal_robustness_report.md",
)


def expected_temporal_artifact_names() -> tuple[str, ...]:
    return _ARTIFACTS


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
                    key: (
                        json.dumps(serialized.get(key), sort_keys=True)
                        if isinstance(serialized.get(key), (dict, list))
                        else serialized.get(key, "")
                    )
                    for key in fieldnames
                }
            )


def write_temporal_robustness_report(
    bundle: TemporalRobustnessBundle,
    output_root: Path,
    *,
    git_commit: str,
    git_dirty: bool,
) -> Path:
    output_dir = output_root / bundle.experiment_id
    output_dir.mkdir(parents=True, exist_ok=False)
    integrity = bundle.integrity
    manifest = {
        "experiment_id": bundle.experiment_id,
        "experiment_version": "FUTURES_TEMPORAL_ROBUSTNESS_V1",
        "started_at": bundle.started_at,
        "completed_at": bundle.completed_at,
        "duration_seconds": bundle.duration_seconds,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "schema_version": 4,
        "source": "BINANCE_USD_M_PUBLIC_SQLITE_LOCAL_ONLY",
        "symbol": bundle.request.symbol,
        "interval": bundle.request.interval,
        "start": bundle.request.start,
        "end": bundle.request.end,
        "dataset_hash": integrity.combined_dataset_hash,
        "hashes": {
            "futures_candle_hash": integrity.futures_candle_hash,
            "mark_price_hash": integrity.mark_price_hash,
            "funding_hash": integrity.funding_hash,
            "combined_dataset_hash": integrity.combined_dataset_hash,
        },
        "counts": {
            "candles": integrity.candles.count,
            "mark_prices": integrity.marks.count,
            "funding_events": integrity.funding.event_count,
        },
        "readiness": integrity.readiness,
        "configurations": tuple(item.variant_id for item in bundle.variants),
        "leverage": "1",
        "leverages_executed": ("1",),
        "cost_scenarios": ("LOW_COST", "BASE_COST", "HIGH_COST", "STRESS_COST"),
        "bootstrap": {
            "iterations": bundle.request.bootstrap_iterations,
            "seed": bundle.request.bootstrap_seed,
            "maximum_iterations": 10_000,
            "candles_reordered": False,
            "strategy_input": False,
        },
        "volatility_quantiles": {
            "source_start": bundle.request.start,
            "source_end": "2024-12-31T23:00:00+00:00",
            "includes_2025": False,
            "boundaries": bundle.volatility_boundaries,
        },
        "metric_attribution": {
            "trade_period": "EXIT_TIME",
            "warmup_in_metrics": False,
            "context_point_in_time": True,
        },
        "network_used": False,
        "automatic_download": False,
        "authenticated_api_used": False,
        "api_key_used": False,
        "external_orders_sent": False,
        "paper_trading_enabled": False,
        "consumed_2026_downloaded": False,
        "consumed_2026_loaded": False,
        "consumed_2026_used": False,
        "strategy_parameters_changed": False,
        "automatic_selection": False,
        "candidate_frozen": False,
        "warnings": bundle.warnings,
        "reproducibility_hash": bundle.reproducibility_hash,
        "research_only": True,
    }
    _write_json(output_dir / "experiment_manifest.json", manifest)
    _write_json(
        output_dir / "predefined_configurations.json",
        {
            "fixed_from_sprint_3a5": True,
            "selection_performed": False,
            "variants": bundle.variants,
        },
    )
    csv_outputs = (
        ("yearly_results.csv", bundle.yearly_rows, ("configuration", "period")),
        (
            "quarterly_results.csv",
            bundle.quarterly_rows,
            ("configuration", "period"),
        ),
        (
            "rolling_window_results.csv",
            bundle.rolling_rows,
            ("configuration", "period", "start", "end"),
        ),
        (
            "walk_forward_design_comparison.csv",
            bundle.walk_forward_rows,
            ("configuration", "design", "fold"),
        ),
        (
            "temporal_boundary_results.csv",
            bundle.boundary_rows,
            ("configuration", "boundary", "segment"),
        ),
        (
            "leave_one_year_out.csv",
            bundle.leave_one_year_out_rows,
            ("configuration", "held_out_year"),
        ),
        (
            "temporal_regime_results.csv",
            bundle.regime_rows,
            ("configuration", "regime"),
        ),
        (
            "regime_transition_results.csv",
            bundle.transition_rows,
            ("configuration", "entry_regime", "exit_regime", "transition"),
        ),
        (
            "volatility_bucket_results.csv",
            bundle.volatility_rows,
            ("configuration", "volatility_bucket"),
        ),
        (
            "market_context_results.csv",
            bundle.market_context_rows,
            ("configuration", "metric", "bucket"),
        ),
        (
            "side_contribution.csv",
            bundle.side_rows,
            ("configuration", "dimension", "period", "side"),
        ),
        (
            "temporal_funding_impact.csv",
            bundle.funding_rows,
            ("configuration", "period_type", "period"),
        ),
        (
            "temporal_cost_impact.csv",
            bundle.cost_rows,
            ("configuration", "period_type", "period", "scenario"),
        ),
        (
            "temporal_concentration.csv",
            bundle.concentration_rows,
            ("configuration", "period_type", "period", "side"),
        ),
    )
    for name, rows, fields in csv_outputs:
        _write_csv(output_dir / name, rows, empty_fields=fields)
    _write_json(
        output_dir / "bootstrap_uncertainty.json",
        {
            "post_backtest_only": True,
            "candles_reordered": False,
            "summaries": bundle.bootstrap_summaries,
        },
    )
    bootstrap_rows = tuple(
        {
            "configuration": item.configuration,
            "trade_count": item.trade_count,
            "iterations": item.iterations,
            "seed": item.seed,
            "block_by_month": item.block_by_month,
            "status": item.status,
            "sample_fingerprint": item.sample_fingerprint,
            **{
                f"{metric}_{bound}": value
                for metric, interval in item.intervals.items()
                for bound, value in interval.items()
            },
        }
        for item in bundle.bootstrap_summaries
    )
    _write_csv(
        output_dir / "bootstrap_by_configuration.csv",
        bootstrap_rows,
        empty_fields=("configuration", "trade_count", "iterations", "seed", "status"),
    )
    _write_json(
        output_dir / "temporal_stability_scorecard.json",
        {
            "single_score_used": False,
            "scorecards": bundle.scorecards,
        },
    )
    _write_json(
        output_dir / "configuration_classification.json",
        {
            "candidate_declared": False,
            "candidate_frozen": False,
            "classifications": bundle.classifications,
        },
    )
    _write_json(
        output_dir / "2025_result_explanation.json",
        {
            "causal_claim": False,
            "explanations": bundle.explanations_2025,
        },
    )
    (output_dir / "temporal_robustness_report.md").write_text(
        _markdown(bundle),
        encoding="utf-8",
    )
    produced = {item.name for item in output_dir.iterdir()}
    missing = set(_ARTIFACTS) - produced
    if missing:
        raise RuntimeError(f"temporal report is missing artifacts: {sorted(missing)}")
    return output_dir


def _markdown(bundle: TemporalRobustnessBundle) -> str:
    classifications = "\n".join(
        f"| {item['configuration']} | {item['classification']} | {item['rationale']} |"
        for item in bundle.classifications
    )
    yearly = "\n".join(
        f"| {item['configuration']} | {item['period']} | "
        f"{item['net_return_percent']} | {item['trades']} |"
        for item in bundle.yearly_rows
    )
    scorecards = "\n".join(
        f"| {item['configuration']} | {item['overall']} |"
        for item in bundle.scorecards
    )
    warnings = "\n".join(f"- {item}" for item in bundle.warnings)
    return f"""# Futures 1x temporal robustness

## 1. Objetivo

Diagnosticar não-estacionariedade nas seis configurações fixadas na Sprint 3A.5. Nenhuma
configuração é selecionada, congelada ou aprovada.

## 2. Dataset

ETHUSDT USD-M Futures 1h local, de {bundle.request.start.isoformat()} a
{bundle.request.end.isoformat()}, readiness `{bundle.integrity.readiness}`.

## 3. Hashes

Combined dataset hash: `{bundle.integrity.combined_dataset_hash}`.

## 4. Configurações fixas

Somente as seis variantes 1x pré-registradas foram executadas. Estratégia, indicadores, stops,
funding, custos base e prioridade de eventos não foram alterados.

## 5. Decomposição anual

| Configuração | Ano | Retorno % | Trades |
|---|---:|---:|---:|
{yearly}

## 6. Decomposição trimestral

Trades são atribuídos pelo timestamp de saída. Warmup e contexto anterior não entram nas métricas.

## 7. Janelas móveis

Foram avaliadas janelas 90/30, 180/60 e 365/90 sem uso de candles futuros.

## 8. Desenhos walk-forward

Rolling, expanding, rolling 730 dias e validation 180 dias usam parâmetros fixos e não ranqueiam
desenhos.

## 9. Fronteiras

As quatro fronteiras são descritivas. `BOUNDARY_SENSITIVE` indica mudança de padrão de sinal.

## 10. Leave-one-year-out

`SINGLE_YEAR_DEPENDENCE` indica mudança de sinal ao remover um ano.

## 11. Regimes

Regimes são point-in-time. `HIGH_VOLATILITY` usa o filtro ATR relativo já existente, sem
classificador treinado.

## 12. Transições

MFE, MAE, holding e saída são diagnósticos pós-evento e nunca alimentam a estratégia.

## 13. Volatilidade

Quantis foram calculados exclusivamente em 2022-2024 e aplicados sem recalibração a 2025.

## 14. Contexto

Retornos 24h/7d/30d, distância e slope da EMA longa e persistência são agrupamentos
pós-backtest, não filtros de entrada.

## 15. Long versus short

Contribuições brutas, custos, funding e PnL líquido permanecem separadas por lado.

## 16. Funding

Funding real permanece habilitado. Funding-off é somente diagnóstico e não participa de seleção.

## 17. Custos

LOW, BASE, HIGH e STRESS são os cenários fixos da Sprint 3A.5.

## 18. Concentração

São reportados best/top 3/top 5 e resultados após remover esses trades.

## 19. Bootstrap

Bootstrap pós-backtest usa seed {bundle.request.bootstrap_seed} e
{bundle.request.bootstrap_iterations} iterações. Candles não são reordenados.

## 20. Scorecard

| Configuração | Estabilidade |
|---|---|
{scorecards}

## 21. Explicação de 2025

As associações são quantitativas e não causais. O artefato específico distingue padrões repetidos
de resultados não observados anteriormente.

## 22. Classificação final

| Configuração | Classificação | Fundamentação |
|---|---|---|
{classifications}

Nenhuma classificação habilita leverage, paper trading, Testnet ou produção.

## 23. Limitações

OHLC não contém caminho intrabar, livro, fila, impacto, partial fills ou causalidade. Agregação
temporal por saída pode atribuir a um período trades iniciados anteriormente.

## 24. Próximo passo

Revisar os diagnósticos sem ajustar parâmetros ou consumir 2026. Uma hipótese futura exige nova
pré-especificação; não é candidata desta sprint.

## Warnings

{warnings}
"""
