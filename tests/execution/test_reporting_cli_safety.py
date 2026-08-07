from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaptive_trader.cli.main import _parser, main
from adaptive_trader.domain.market import MarketType
from adaptive_trader.execution import ARTIFACT_NAMES, ExecutionResearchService
from adaptive_trader.microstructure.live import PublicMicrostructureRecorder
from tests.microstructure.helpers import write_session


def test_synthetic_report_has_exact_artifacts_all_scenarios_and_invariants(
    tmp_path: Path,
) -> None:
    report = ExecutionResearchService().run_synthetic(output_dir=tmp_path)
    assert tuple(sorted(path.name for path in report.iterdir())) == tuple(
        sorted(ARTIFACT_NAMES)
    )
    assert len(ARTIFACT_NAMES) == 15
    manifest = json.loads((report / "experiment_manifest.json").read_text(encoding="utf-8"))
    scenarios = json.loads(
        (report / "synthetic_execution_results.json").read_text(encoding="utf-8")
    )
    invariants = json.loads(
        (report / "accounting_invariants.json").read_text(encoding="utf-8")
    )
    determinism = json.loads(
        (report / "replay_determinism.json").read_text(encoding="utf-8")
    )
    assert manifest["sprint"] == "4A.2"
    assert manifest["research_only"] is True
    assert manifest["authentication_used"] is False
    assert manifest["orders_sent_externally"] is False
    assert manifest["alpha_calibrated_by_pnl"] is False
    assert manifest["queue_position_exact"] is False
    assert [item["code"] for item in scenarios] == list("ABCDEFGHIJKLMNOPQ")
    assert all(item["profitability_evaluated"] is False for item in scenarios)
    assert invariants["all_passed"] is True
    assert determinism["orders_fills_positions_pnl_risk_deterministic"] is True


def test_single_scenario_validation_and_show_contract(tmp_path: Path) -> None:
    report = ExecutionResearchService().run_synthetic(output_dir=tmp_path, scenario="e")
    scenarios = json.loads(
        (report / "synthetic_execution_results.json").read_text(encoding="utf-8")
    )
    assert scenarios[0]["code"] == "E"
    shown = ExecutionResearchService.show(report)
    assert shown["manifest"]["research_only"] is True
    with pytest.raises(ValueError, match="A through Q"):
        ExecutionResearchService().run_synthetic(output_dir=tmp_path, scenario="Z")
    (report / "execution_quality.json").unlink()
    with pytest.raises(ValueError, match="incomplete"):
        ExecutionResearchService.show(report)


def test_recorded_spot_session_execution_replay_is_deterministic(tmp_path: Path) -> None:
    session = write_session(tmp_path / "capture")
    report = ExecutionResearchService().run_session(
        session_path=session,
        output_dir=tmp_path / "reports",
    )
    replay = json.loads((report / "replay_determinism.json").read_text(encoding="utf-8"))
    manifest = json.loads((report / "experiment_manifest.json").read_text(encoding="utf-8"))
    assert replay["first_execution_hash"] == replay["second_execution_hash"]
    assert replay["real_sleep_used"] is False
    assert manifest["source_session"] == str(session)


def test_execution_cli_synthetic_simulate_and_show_are_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ADAPTIVE_TRADER_DB_PATH", str(tmp_path / "cli.sqlite3"))
    assert (
        main(
            [
                "research",
                "execution",
                "synthetic",
                "--scenario",
                "all",
                "--output-dir",
                str(tmp_path / "synthetic"),
            ]
        )
        == 0
    )
    synthetic = json.loads(capsys.readouterr().out)
    assert synthetic["research_only"] is True
    experiment = Path(synthetic["experiment"])
    assert main(["research", "execution", "show", "--experiment", str(experiment)]) == 0
    assert json.loads(capsys.readouterr().out)["manifest"]["authentication_used"] is False

    session = write_session(tmp_path / "capture")
    assert (
        main(
            [
                "research",
                "execution",
                "simulate",
                "--session",
                str(session),
                "--policy",
                "taker-only",
                "--latency-profile",
                "normal",
                "--output-dir",
                str(tmp_path / "simulation"),
            ]
        )
        == 0
    )
    simulation = json.loads(capsys.readouterr().out)
    assert simulation["authentication_used"] is False
    assert simulation["external_orders_sent"] is False


def test_cli_has_duration_alias_and_no_execution_authentication_options() -> None:
    parser = _parser()
    args = parser.parse_args(
        [
            "market",
            "microstructure",
            "record",
            "--market",
            "futures",
            "--streams",
            "depth,markPrice",
            "--output-dir",
            "data/microstructure",
            "--duration",
            "3600",
        ]
    )
    assert args.duration_seconds == 3600
    indefinite = parser.parse_args(
        [
            "market",
            "microstructure",
            "record",
            "--market",
            "spot",
            "--streams",
            "depth",
            "--output-dir",
            "data/microstructure",
            "--duration",
            "0",
        ]
    )
    assert indefinite.duration_seconds == 0
    options = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--api-key" not in options
    assert "--secret" not in options


def test_duration_zero_can_be_cleanly_stopped_before_network(tmp_path: Path) -> None:
    recorder = PublicMicrostructureRecorder(
        market_type=MarketType.SPOT,
        symbol="ETHUSDT",
        streams=("depth",),
        output_dir=tmp_path,
        duration_seconds=0,
    )
    recorder.request_stop()
    import asyncio

    result = asyncio.run(recorder.run())
    assert result.session.completeness == "COMPLETE"
    assert result.session.event_count == 0
    assert (result.session.session_path / "manifest.json").is_file()
