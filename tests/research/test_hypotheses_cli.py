import json
from pathlib import Path
from types import SimpleNamespace

from adaptive_trader.cli.main import main
from adaptive_trader.research.spot_hypotheses import SpotExperimentPeriods

VALID_ARGS = [
    "research",
    "hypotheses",
    "spot",
    "run",
    "--symbol",
    "ETHUSDT",
    "--interval",
    "1h",
    "--development-start",
    "2022-01-01T00:00:00Z",
    "--development-end",
    "2024-12-31T23:00:00Z",
    "--validation-start",
    "2025-01-01T00:00:00Z",
    "--validation-end",
    "2025-12-31T23:00:00Z",
    "--consumed-test-start",
    "2026-01-01T00:00:00Z",
    "--consumed-test-end",
    "2026-07-01T00:00:00Z",
    "--output-dir",
    "reports/research",
    "--yes",
]


def test_periods_are_pre_registered_and_overlap_is_rejected() -> None:
    SpotExperimentPeriods.pre_registered().assert_pre_registered()
    overlapping = VALID_ARGS.copy()
    index = overlapping.index("2025-12-31T23:00:00Z")
    overlapping[index] = "2026-01-01T00:00:00Z"

    assert main(overlapping) == 2


def test_consumed_period_cannot_be_moved_into_selection() -> None:
    changed = VALID_ARGS.copy()
    index = changed.index("2024-12-31T23:00:00Z")
    changed[index] = "2026-02-01T00:00:00Z"

    assert main(changed) == 2


def test_spot_run_uses_local_repository_without_network(monkeypatch, tmp_path: Path) -> None:
    class Repository:
        def __init__(self, _: Path) -> None:
            pass

        def get_candles(self, *args, **kwargs):
            return ("local",)

        def close(self) -> None:
            pass

    class Experiment:
        def __init__(self, **kwargs) -> None:
            pass

        def run(self):
            return SimpleNamespace(
                experiment_id="experiment",
                output_path=tmp_path,
                stage_one_selection=SimpleNamespace(selected_variant_id="BASE"),
                final_selection=SimpleNamespace(
                    selected_variant_id="BASE",
                    selected_regime_mode=None,
                ),
                candidate_status="NOT_CANDIDATE",
                duration_seconds="1",
            )

    monkeypatch.setattr("adaptive_trader.cli.main.DatabaseRepository", Repository)
    monkeypatch.setattr(
        "adaptive_trader.cli.main.validate_dataset",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("adaptive_trader.cli.main.SpotHypothesisExperiment", Experiment)
    monkeypatch.setattr(
        "adaptive_trader.cli.main.BinancePublicClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )

    assert main(VALID_ARGS) == 0


def test_show_and_verify_commands_use_only_local_files(
    tmp_path: Path,
    capsys,
) -> None:
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    for name in (
        "experiment_manifest.json",
        "candidate_criteria.json",
        "candidate_freeze_decision.json",
    ):
        (experiment / name).write_text(json.dumps({"file": name}), encoding="utf-8")

    assert main(
        ["research", "hypotheses", "spot", "show", "--experiment", str(experiment)]
    ) == 0
    assert "experiment_manifest.json" in capsys.readouterr().out
