from pathlib import Path

from adaptive_trader.cli.main import main


def command(output: Path) -> list[str]:
    return [
        "research",
        "futures",
        "validate-real",
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
        "--leverage",
        "1",
        "--output-dir",
        str(output),
        "--yes",
    ]


def test_real_validation_cli_rejects_leverage_and_changed_periods(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ADAPTIVE_TRADER_DB_PATH",
        str(tmp_path / "empty.sqlite3"),
    )
    invalid_leverage = command(tmp_path / "leverage")
    invalid_leverage[invalid_leverage.index("1", 2)] = "2"
    assert main(invalid_leverage) == 2
    invalid_period = command(tmp_path / "period")
    invalid_period[invalid_period.index("2025-12-31T23:00:00Z")] = (
        "2026-01-01T00:00:00Z"
    )
    assert main(invalid_period) == 2


def test_real_validation_cli_is_offline_and_show_is_reproducible(
    tmp_path,
    monkeypatch,
    real_fixture_bundle,
) -> None:
    database_path = tmp_path / "empty.sqlite3"
    monkeypatch.setenv("ADAPTIVE_TRADER_DB_PATH", str(database_path))

    class ForbiddenNetwork:
        def __init__(self, *args, **kwargs):
            raise AssertionError("real validation attempted network access")

    class StubService:
        def __init__(self, repository, config):
            self.repository = repository
            self.config = config

        def run(self, **kwargs):
            return real_fixture_bundle

    monkeypatch.setattr(
        "adaptive_trader.cli.main.BinanceFuturesPublicClient",
        ForbiddenNetwork,
    )
    monkeypatch.setattr(
        "adaptive_trader.cli.main.FuturesRealValidationService",
        StubService,
    )
    output_root = tmp_path / "reports"
    assert main(command(output_root)) == 0
    experiment = output_root / real_fixture_bundle.experiment_id
    assert (
        main(
            [
                "research",
                "futures",
                "validation-show",
                "--experiment",
                str(experiment),
            ]
        )
        == 0
    )
