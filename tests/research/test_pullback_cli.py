from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from adaptive_trader.cli.main import main
from adaptive_trader.research.pullback_analysis import PullbackClassification
from adaptive_trader.research.pullback_catalog import (
    PullbackExperimentPeriods,
    load_pullback_catalog,
)
from adaptive_trader.research.pullback_experiment import (
    PullbackExperimentBundle,
    PullbackExperimentRequest,
)
from adaptive_trader.research.pullback_report import (
    expected_pullback_artifact_names,
    write_pullback_experiment_report,
)


def valid_args(output_dir: Path) -> list[str]:
    return [
        "research",
        "pullback",
        "run",
        "--symbol",
        "ETHUSDT",
        "--interval",
        "1h",
        "--development-start",
        "2022-01-01T00:00:00Z",
        "--development-end",
        "2023-12-31T23:00:00Z",
        "--validation-start",
        "2024-01-01T00:00:00Z",
        "--validation-end",
        "2024-12-31T23:00:00Z",
        "--consumed-start",
        "2025-01-01T00:00:00Z",
        "--consumed-end",
        "2026-07-01T00:00:00Z",
        "--markets",
        "spot,futures",
        "--futures-modes",
        "long,short,long-short",
        "--leverage",
        "1",
        "--output-dir",
        str(output_dir),
        "--yes",
    ]


def test_cli_rejects_leverage_and_unregistered_2025_2026_periods(
    tmp_path: Path,
) -> None:
    without_confirmation = valid_args(tmp_path)
    without_confirmation.remove("--yes")
    assert main(without_confirmation) == 2

    leverage = valid_args(tmp_path)
    leverage[leverage.index("1", leverage.index("--leverage"))] = "2"
    assert main(leverage) == 2

    year_2025 = valid_args(tmp_path)
    year_2025[year_2025.index("2024-12-31T23:00:00Z")] = (
        "2025-01-01T00:00:00Z"
    )
    assert main(year_2025) == 2

    year_2026 = valid_args(tmp_path)
    year_2026[year_2026.index("2024-01-01T00:00:00Z")] = (
        "2026-01-01T00:00:00Z"
    )
    assert main(year_2026) == 2


def test_pullback_run_is_offline_and_never_executes_an_order(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = {
        "network": 0,
        "download": 0,
        "orders": 0,
    }

    class Repository:
        def __init__(self, path: Path) -> None:
            self.path = path

        def close(self) -> None:
            pass

    class Service:
        def __init__(self, repository: object, config: object) -> None:
            pass

        def run(self, request: object) -> object:
            return SimpleNamespace(
                experiment_id="pullback-fixture",
                catalog=SimpleNamespace(content_hash="catalog-hash"),
                assessments=(
                    SimpleNamespace(
                        market="SPOT",
                        mode="LONG",
                        variant_id=None,
                        classification=(
                            PullbackClassification.NO_DEVELOPMENT_HYPOTHESIS
                        ),
                    ),
                ),
                duration_seconds="1",
            )

    class ForbiddenNetwork:
        def __init__(self, *args: object, **kwargs: object) -> None:
            calls["network"] += 1
            raise AssertionError("network must remain unused")

    def forbidden_order(*args: object, **kwargs: object) -> object:
        calls["orders"] += 1
        raise AssertionError("orders must remain unused")

    def report(
        bundle: object,
        output_root: Path,
        *,
        git_commit: str,
        git_dirty: bool,
    ) -> Path:
        return output_root / "pullback-fixture"

    monkeypatch.setattr("adaptive_trader.cli.main.DatabaseRepository", Repository)
    monkeypatch.setattr("adaptive_trader.cli.main.PullbackExperimentService", Service)
    monkeypatch.setattr(
        "adaptive_trader.cli.main.write_pullback_experiment_report",
        report,
    )
    monkeypatch.setattr(
        "adaptive_trader.cli.main._git_metadata",
        lambda: ("commit", True),
    )
    monkeypatch.setattr(
        "adaptive_trader.cli.main.BinancePublicClient",
        ForbiddenNetwork,
    )
    monkeypatch.setattr(
        "adaptive_trader.cli.main.BinanceFuturesPublicClient",
        ForbiddenNetwork,
    )
    monkeypatch.setattr(
        "adaptive_trader.execution.backtest.BacktestOrderExecutor.execute",
        forbidden_order,
    )

    assert main(valid_args(tmp_path)) == 0
    assert calls == {"network": 0, "download": 0, "orders": 0}


def test_show_reads_only_the_four_local_json_summaries(
    tmp_path: Path,
    capsys,
) -> None:
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    for name in (
        "experiment_manifest.json",
        "hypothesis_catalog.json",
        "hypothesis_assessment.json",
        "future_holdout_plan.json",
    ):
        (experiment / name).write_text(
            json.dumps({"name": name}),
            encoding="utf-8",
        )

    assert main(
        [
            "research",
            "pullback",
            "show",
            "--experiment",
            str(experiment),
        ]
    ) == 0
    assert "hypothesis_assessment.json" in capsys.readouterr().out
    assert len(expected_pullback_artifact_names()) == 18


def test_report_writer_creates_exactly_the_18_registered_artifacts(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    request = PullbackExperimentRequest(
        symbol="ETHUSDT",
        interval="1h",
        periods=PullbackExperimentPeriods.pre_registered(),
        markets=("spot",),
        futures_modes=(),
        leverage=Decimal("1"),
        output_dir=tmp_path,
    )
    bundle = PullbackExperimentBundle(
        experiment_id="pullback-report-fixture",
        started_at=now,
        completed_at=now,
        duration_seconds=Decimal("0"),
        request=request,
        catalog=load_pullback_catalog(),
        catalog_file_sha256="file-hash",
        dataset_manifest={},
        selections=(),
        validation_locks=(),
        development_results=(),
        development_walk_forward=(),
        validation_results=(),
        validation_walk_forward=(),
        market_comparison=(),
        side_contribution=(),
        cost_scenarios=(),
        pullback_decision_funnel=(),
        pullback_reason_codes=(),
        pullback_entry_diagnostics=(),
        regime_loss_exit_diagnostics=(),
        concentration_analysis=(),
        bootstrap_uncertainty=(),
        assessments=(),
        future_holdout_plan={"status": "NO_HOLDOUT_PLAN"},
        warnings=(),
    )

    output = write_pullback_experiment_report(
        bundle,
        tmp_path,
        git_commit="commit",
        git_dirty=True,
    )

    assert tuple(sorted(path.name for path in output.iterdir())) == tuple(
        sorted(expected_pullback_artifact_names())
    )
