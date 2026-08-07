from pathlib import Path
from types import SimpleNamespace

from adaptive_trader.cli.main import main

_HASH = "b4c9674c45ef10c96b68a72d84790aedfe6b93f638f23c63d4612ec61b6c570a"


def command(output: Path) -> list[str]:
    return [
        "research",
        "futures",
        "temporal-robustness",
        "--symbol",
        "ETHUSDT",
        "--interval",
        "1h",
        "--start",
        "2022-01-01T00:00:00Z",
        "--end",
        "2025-12-31T23:00:00Z",
        "--dataset-hash",
        _HASH,
        "--leverage",
        "1",
        "--bootstrap-iterations",
        "2000",
        "--bootstrap-seed",
        "42",
        "--output-dir",
        str(output),
        "--yes",
    ]


def test_temporal_cli_rejects_leverage_2026_and_bad_hash(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADAPTIVE_TRADER_DB_PATH", str(tmp_path / "empty.sqlite3"))
    leverage = command(tmp_path / "leverage")
    leverage[leverage.index("1", 2)] = "2"
    assert main(leverage) == 2
    future = command(tmp_path / "future")
    future[future.index("2025-12-31T23:00:00Z")] = "2026-01-01T00:00:00Z"
    assert main(future) == 2
    bad_hash = command(tmp_path / "hash")
    bad_hash[bad_hash.index(_HASH)] = "bad"
    assert main(bad_hash) == 2


def test_temporal_show_is_offline(tmp_path, monkeypatch) -> None:
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    for name in (
        "experiment_manifest.json",
        "temporal_stability_scorecard.json",
        "configuration_classification.json",
        "2025_result_explanation.json",
        "bootstrap_uncertainty.json",
    ):
        (experiment / name).write_text("{}", encoding="utf-8")

    class ForbiddenNetwork:
        def __init__(self, *args, **kwargs):
            raise AssertionError("temporal diagnostics attempted network access")

    monkeypatch.setattr(
        "adaptive_trader.cli.main.BinanceFuturesPublicClient",
        ForbiddenNetwork,
    )
    assert (
        main(
            [
                "research",
                "futures",
                "temporal-show",
                "--experiment",
                str(experiment),
            ]
        )
        == 0
    )


def test_temporal_run_uses_service_without_network_or_orders(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ADAPTIVE_TRADER_DB_PATH",
        str(tmp_path / "empty.sqlite3"),
    )

    class ForbiddenNetwork:
        def __init__(self, *args, **kwargs):
            raise AssertionError("temporal diagnostics attempted network access")

    class StubService:
        def __init__(self, repository, config):
            self.repository = repository
            self.config = config

        def run(self, request):
            assert request.leverage == 1
            assert request.end.year == 2025
            return SimpleNamespace(
                experiment_id="temporal-fixture",
                integrity=SimpleNamespace(combined_dataset_hash=_HASH),
                classifications=(
                    {
                        "configuration": "FIXED",
                        "classification": "INCONCLUSIVE",
                    },
                ),
                duration_seconds=0,
            )

    monkeypatch.setattr(
        "adaptive_trader.cli.main.BinanceFuturesPublicClient",
        ForbiddenNetwork,
    )
    monkeypatch.setattr(
        "adaptive_trader.cli.main.FuturesTemporalRobustnessService",
        StubService,
    )
    monkeypatch.setattr(
        "adaptive_trader.cli.main.write_temporal_robustness_report",
        lambda bundle, output, **kwargs: output / bundle.experiment_id,
    )
    assert main(command(tmp_path / "reports")) == 0
