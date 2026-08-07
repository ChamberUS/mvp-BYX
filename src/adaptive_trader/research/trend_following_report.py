"""Artifact contract and report writer for Sprint 3C.1."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from adaptive_trader.research.trend_following_experiment import (
    TrendFollowingExperimentBundle,
)

TREND_FOLLOWING_ARTIFACT_NAMES = (
    "experiment_manifest.json",
    "aggregation_integrity.json",
    "daily_dataset_hashes.json",
    "hypothesis_catalog.json",
    "trend_following_decision_funnel.csv",
    "trend_following_decision_traces.csv",
    "development_results.csv",
    "development_walk_forward.csv",
    "operational_viability.json",
    "development_selection.json",
    "trend_following_validation_lock.json",
    "validation_results.csv",
    "validation_walk_forward.csv",
    "defensive_risk_comparison.csv",
    "trend_following_cost_scenarios.csv",
    "trend_following_funding_impact.csv",
    "side_contribution.csv",
    "concentration_analysis.csv",
    "bootstrap_uncertainty.json",
    "hypothesis_assessment.json",
    "future_confirmation_plan.json",
    "trend_following_report.md",
)


def expected_trend_following_artifact_names() -> tuple[str, ...]:
    """Return the immutable report contract in presentation order."""

    return TREND_FOLLOWING_ARTIFACT_NAMES


def write_trend_following_report(
    bundle: TrendFollowingExperimentBundle,
    *,
    git_commit: str,
    git_dirty: bool,
) -> Path:
    """Complete an output directory whose validation lock already exists."""

    output_dir = bundle.output_path
    output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / "trend_following_validation_lock.json"
    if lock_path.exists():
        if lock_path.read_bytes() != bundle.validation_lock_bytes:
            raise RuntimeError("validation lock changed after it was persisted")
    else:
        # Primarily supports small unit-created bundles.  The real service always
        # writes this file before it loads validation data.
        lock_path.write_bytes(bundle.validation_lock_bytes)

    manifest = {
        **bundle.manifest,
        "experiment_id": bundle.experiment_id,
        "started_at": bundle.started_at,
        "completed_at": bundle.completed_at,
        "duration_seconds": bundle.duration_seconds,
        "initial_commit": git_commit,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "artifacts": TREND_FOLLOWING_ARTIFACT_NAMES,
    }
    catalog_payload = {
        "path": str(bundle.catalog.path),
        "canonical_hash": bundle.catalog.canonical_hash,
        "file_sha256": bundle.catalog.file_sha256,
        "variants": bundle.catalog.hypotheses,
    }

    _write_json(output_dir / "experiment_manifest.json", manifest)
    _write_json(
        output_dir / "aggregation_integrity.json",
        bundle.aggregation_integrity,
    )
    _write_json(
        output_dir / "daily_dataset_hashes.json",
        bundle.daily_dataset_hashes,
    )
    _write_json(output_dir / "hypothesis_catalog.json", catalog_payload)
    _write_csv(
        output_dir / "trend_following_decision_funnel.csv",
        bundle.decision_funnel,
    )
    _write_csv(
        output_dir / "trend_following_decision_traces.csv",
        bundle.decision_traces,
    )
    _write_csv(
        output_dir / "development_results.csv",
        bundle.development_results,
    )
    _write_csv(
        output_dir / "development_walk_forward.csv",
        bundle.development_walk_forward,
    )
    _write_json(
        output_dir / "operational_viability.json",
        bundle.operational_viability,
    )
    _write_json(
        output_dir / "development_selection.json",
        bundle.development_selection,
    )
    # Do not rewrite the validation lock here.  Its byte identity is part of
    # the experiment evidence.
    _write_csv(
        output_dir / "validation_results.csv",
        bundle.validation_results,
    )
    _write_csv(
        output_dir / "validation_walk_forward.csv",
        bundle.validation_walk_forward,
    )
    _write_csv(
        output_dir / "defensive_risk_comparison.csv",
        bundle.defensive_risk_comparison,
    )
    _write_csv(
        output_dir / "trend_following_cost_scenarios.csv",
        bundle.cost_scenarios,
    )
    _write_csv(
        output_dir / "trend_following_funding_impact.csv",
        bundle.funding_impact,
    )
    _write_csv(
        output_dir / "side_contribution.csv",
        bundle.side_contribution,
    )
    _write_csv(
        output_dir / "concentration_analysis.csv",
        bundle.concentration_analysis,
    )
    _write_json(
        output_dir / "bootstrap_uncertainty.json",
        bundle.bootstrap_uncertainty,
    )
    _write_json(
        output_dir / "hypothesis_assessment.json",
        bundle.assessments,
    )
    _write_json(
        output_dir / "future_confirmation_plan.json",
        bundle.future_confirmation_plan,
    )
    (output_dir / "trend_following_report.md").write_text(
        _markdown(bundle, git_commit),
        encoding="utf-8",
    )
    observed = tuple(sorted(path.name for path in output_dir.iterdir()))
    expected = tuple(sorted(TREND_FOLLOWING_ARTIFACT_NAMES))
    if observed != expected:
        raise RuntimeError("trend-following artifact set differs from contract")
    if lock_path.read_bytes() != bundle.validation_lock_bytes:
        raise RuntimeError("validation lock changed while writing reports")
    return output_dir


def _markdown(
    bundle: TrendFollowingExperimentBundle,
    git_commit: str,
) -> str:
    promising = tuple(
        item
        for item in bundle.assessments
        if item.get("classification") == "PROMISING_FOR_CONFIRMATION"
    )
    selected_lines = tuple(
        (
            f"- {item['market']}/{item['mode']}: "
            f"{item.get('selected_variant_id') or 'none'} "
            f"({item['status']})"
        )
        for item in bundle.development_selection
    )
    incomplete_days = sum(
        value if isinstance(value := item.get("incomplete_day_count"), int) else 0
        for item in bundle.aggregation_integrity
    )
    return f"""# Daily Trend Following — Sprint 3C.1

## 1. Hipótese

Pesquisa offline e pré-registrada de trend following diário em ETHUSDT. Os
resultados não autorizam produção e não são declaração de lucratividade.

## 2. SMA 200

O filtro macro usa exatamente 200 fechamentos diários terminando no dia da
decisão. Os primeiros 199 candles são somente warmup.

## 3. Donchian 20

Entradas usam fechamento confirmado além do canal de 20 dias anteriores.

## 4. Saídas 10 e 20

Somente os dois canais pré-registrados foram executados. O candle corrente é
excluído de todos os canais.

## 5. Agregação diária

Candles 1h locais foram agregados em UTC sem preenchimento. Dias incompletos
observados: {incomplete_days}; a política de pesquisa foi `WARN_AND_EXCLUDE`.

## 6. Point-in-time

Sinais são confirmados no fechamento diário e executados apenas na primeira
abertura 1h elegível do dia UTC seguinte.

## 7. Risco de 1%

O orçamento máximo normal é 1% do equity; o stop Donchian inicial é referência
de sizing, não stop intraday.

## 8. Risco defensivo

Três perdas estruturais consecutivas reduzem o orçamento para 0,5%. O modo
normal só retorna quando o equity alcança o nível anterior à sequência.

## 9. Funil

O funil e os traces preservam candle diário, warmup, filtro macro, breakout,
sizing, aprovação, execução, posição e saída.

## 10. Spot

Spot foi executado estritamente long-only, com caixa e custos explícitos.

## 11. Futures long

Futures long permaneceu isolado, 1x, com mark e funding horários.

## 12. Futures short

Short é uma direção Futures explícita e nunca reutiliza `SELL` Spot.

## 13. Futures long-short

O modo long-short mantém no máximo uma posição e não faz hedge simultâneo.

## 14. Development

Somente 2022–2023 participou da avaliação e da seleção.

## 15. Seleção

{chr(10).join(selected_lines) if selected_lines else "- Nenhuma configuração selecionada."}

## 16. Lock

O lock foi gravado antes de consultar 2024 e permaneceu byte a byte imutável.
Commit inicial registrado: `{git_commit}`.

## 17. Validation

2024 executou somente benchmarks e configurações bloqueadas; não selecionou nem
alterou parâmetros.

## 18. Custos

LOW, BASE, HIGH e STRESS foram reportados. BASE foi o único cenário elegível
para seleção.

## 19. Funding

Funding histórico foi aplicado nos timestamps reais e sua fonte permaneceu
igual entre cenários.

## 20. Drawdown

Drawdown, retorno/drawdown, maior perda e tempo defensivo foram registrados sem
assumir benefício da redução de risco.

## 21. Comparação defensiva

Pares equivalentes de saída 10 e 20 com risco fixo e defensivo foram comparados
separadamente.

## 22. Bootstrap

Somente trades fechados foram reamostrados, seed 42, 2.000 iterações e intervalo
percentil de 95%.

## 23. Classificação

Configurações promissoras apenas para confirmação futura: {len(promising)}.
Nenhuma candidata foi congelada e nenhuma classificação habilita produção.

## 24. Limitações

OHLC diário, custos simulados, mark/funding históricos e amostra pequena não
demonstram causalidade nem desempenho futuro.

## 25. Próximo passo

O plano de confirmação, quando aplicável, é apenas documental e começa depois
de 2026-07-01, por no mínimo 180 dias e 10 trades fechados, sem ajuste.

2025 e 2026 não foram carregados. Leverage permaneceu 1x. Não houve rede,
download, autenticação, Testnet, paper trading, API privada ou ordem externa.
"""


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(
            _jsonable(payload),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_csv(
    path: Path,
    rows: tuple[dict[str, object], ...],
) -> None:
    fields = tuple(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not fields:
            handle.write("status\n")
            return
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fields})


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (tuple, list, dict)):
        return json.dumps(_jsonable(value), sort_keys=True)
    serialized = _jsonable(value)
    if isinstance(serialized, (str, int, float, bool)):
        return serialized
    return str(serialized)


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    raise TypeError(f"unsupported report value: {type(value).__name__}")
