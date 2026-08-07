"""Artifact writer for Sprint 3B.2."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from adaptive_trader.research.pullback_calibration_experiment import (
    PullbackCalibrationBundle,
)

CALIBRATION_ARTIFACT_NAMES = (
    "experiment_manifest.json",
    "pullback_logic_audit.json",
    "pullback_sequential_funnel.csv",
    "pullback_sequential_funnel.json",
    "rejected_resumptions.csv",
    "single_rule_ablation.csv",
    "calibration_catalog.json",
    "operational_frequency_results.csv",
    "frequency_selection_decision.json",
    "development_financial_results.csv",
    "pullback_calibration_lock.json",
    "validation_results.csv",
    "resumption_post_event_analysis.csv",
    "bootstrap_uncertainty.json",
    "calibration_assessment.json",
    "pullback_calibration_report.md",
)


def expected_calibration_artifact_names() -> tuple[str, ...]:
    return CALIBRATION_ARTIFACT_NAMES


def write_pullback_calibration_report(
    bundle: PullbackCalibrationBundle,
    output_root: Path,
    *,
    git_commit: str,
    git_dirty: bool,
) -> Path:
    output_dir = output_root / bundle.experiment_id
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        **bundle.manifest,
        "started_at": bundle.started_at,
        "completed_at": bundle.completed_at,
        "duration_seconds": bundle.duration_seconds,
        "initial_commit": git_commit,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "periods": bundle.request.periods,
        "artifacts": CALIBRATION_ARTIFACT_NAMES,
    }
    catalog = {
        "path": str(bundle.catalog.path),
        "canonical_hash": bundle.catalog.canonical_hash,
        "file_sha256": bundle.catalog.file_sha256,
        "variants": bundle.catalog.variants,
    }
    locks = tuple(
        {**item, "commit": git_commit} for item in bundle.validation_locks
    )
    _write_json(output_dir / "experiment_manifest.json", manifest)
    _write_json(
        output_dir / "pullback_logic_audit.json", bundle.logic_audit
    )
    _write_csv(
        output_dir / "pullback_sequential_funnel.csv",
        bundle.sequential_funnel,
    )
    _write_json(
        output_dir / "pullback_sequential_funnel.json",
        bundle.sequential_funnel,
    )
    _write_csv(
        output_dir / "rejected_resumptions.csv",
        bundle.rejected_resumptions,
    )
    _write_csv(
        output_dir / "single_rule_ablation.csv",
        bundle.single_rule_ablation,
    )
    _write_json(output_dir / "calibration_catalog.json", catalog)
    _write_csv(
        output_dir / "operational_frequency_results.csv",
        tuple(asdict(item) for item in bundle.operational_frequency_results),
    )
    _write_json(
        output_dir / "frequency_selection_decision.json",
        bundle.selection_decisions,
    )
    _write_csv(
        output_dir / "development_financial_results.csv",
        bundle.development_financial_results,
    )
    _write_json(output_dir / "pullback_calibration_lock.json", locks)
    _write_csv(
        output_dir / "validation_results.csv", bundle.validation_results
    )
    _write_csv(
        output_dir / "resumption_post_event_analysis.csv",
        bundle.post_event_analysis,
    )
    _write_json(
        output_dir / "bootstrap_uncertainty.json", bundle.bootstraps
    )
    _write_json(
        output_dir / "calibration_assessment.json", bundle.assessments
    )
    (output_dir / "pullback_calibration_report.md").write_text(
        _markdown(bundle, git_commit), encoding="utf-8"
    )
    observed = tuple(sorted(path.name for path in output_dir.iterdir()))
    if observed != tuple(sorted(CALIBRATION_ARTIFACT_NAMES)):
        raise RuntimeError("calibration artifact set differs from contract")
    return output_dir


def _markdown(bundle: PullbackCalibrationBundle, git_commit: str) -> str:
    viable = [
        item
        for item in bundle.operational_frequency_results
        if item.status.value == "OPERATIONALLY_VIABLE"
    ]
    selected: list[str] = []
    for item in bundle.selection_decisions:
        values = item["selected_variant_ids"]
        selected_ids = (
            ",".join(str(value) for value in values)
            if isinstance(values, (tuple, list))
            else str(values)
        )
        selected.append(
            f"{item['market']}/{item['mode']}: {selected_ids or 'none'}"
        )
    dominant: dict[str, int] = {}
    for row in bundle.rejected_resumptions:
        reason = row.get("first_failure_code")
        if reason:
            dominant[str(reason)] = dominant.get(str(reason), 0) + 1
    dominant_reason = (
        max(dominant, key=lambda key: dominant[key]) if dominant else "NONE"
    )
    return f"""# Pullback Frequency Calibration — Sprint 3B.2

## 1. Problema observado

Retomadas detectadas na Sprint 3B.1 não chegavam a sinais. Esta auditoria é
somente pesquisa offline e não declara lucratividade.

## 2. Auditoria lógica

A revalidação do regime no candle de retomada era incompatível com o estado
point-in-time já estabelecido e redundante com persistência. O regime agora é
travado no início do pullback; cruzamento da EMA curta e fechamento direcional
possuem reason codes separados. Auditoria detalhada em `pullback_logic_audit.json`.

## 3. Funil

O funil registra a ordem real, contagens de entrada, aprovação e falha. Nenhuma
etapa posterior excede a anterior.

## 4. Retomadas rejeitadas

Foram registradas {len(bundle.rejected_resumptions)} retomadas rejeitadas. A
primeira falha dominante foi `{dominant_reason}`.

## 5. Ablação

Cada contrafactual remove exatamente uma regra e nunca executa sinal ou trade.

## 6. Catálogo

Hash canônico: `{bundle.catalog.canonical_hash}`.
SHA-256 do arquivo: `{bundle.catalog.file_sha256}`.
Oito definições fixas alteram no máximo uma dimensão em relação à base.

## 7. Suficiência operacional

Definições viáveis: {len(viable)}. Viabilidade foi determinada sem retorno.

## 8. Seleção sem retorno

{chr(10).join(f"- {line}" for line in selected)}

## 9. Development financeiro

Somente definições selecionadas receberam resultados financeiros reportados.

## 10. Lock

O lock foi criado antes de carregar/executar validation e inclui parâmetros,
hashes, commit `{git_commit}`, dataset e métricas de frequência.

## 11. Validation

2024 executou somente baseline e definições bloqueadas.

## 12. Pós-evento

`POST_EVENT_ONLY_NO_STRATEGY_ACCESS`. Retornos, MFE e MAE não foram acessados
pela estratégia e não alteraram o catálogo.

## 13. Classificação

As classificações são descritivas; nenhuma candidata foi congelada.

## 14. Limitações

OHLC, regime aproximado, custos simulados, mark/funding históricos e amostra
limitada não demonstram causalidade nem desempenho futuro.

## 15. Próximo passo

Não ampliar a busca nesta sprint. Qualquer novo teste requer pré-registro
separado.

2025 e 2026 não foram carregados. Leverage permaneceu 1x. Não houve rede,
download, autenticação, Testnet, paper trading ou ordem externa.
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
    return serialized if isinstance(serialized, (str, int, float, bool)) else str(serialized)


def _write_csv(
    path: Path,
    rows: tuple[dict[str, object], ...],
) -> None:
    fields = tuple(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not fields:
            handle.write("status\n")
            return
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fields})


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
