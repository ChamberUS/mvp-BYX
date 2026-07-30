from pathlib import Path

from adaptive_trader.cli.main import main
from adaptive_trader.storage.sqlite import DatabaseRepository


def seed(path: Path, futures_candles, mark_prices) -> None:
    repository = DatabaseRepository(path)
    try:
        repository.upsert_futures_candles(futures_candles)
        repository.upsert_mark_prices(mark_prices)
    finally:
        repository.close()


def test_futures_status_and_inspect_are_offline(
    tmp_path,
    monkeypatch,
    futures_candles,
    mark_prices,
    capsys,
) -> None:
    path = tmp_path / "cli.sqlite3"
    seed(path, futures_candles, mark_prices)
    monkeypatch.setenv("ADAPTIVE_TRADER_DB_PATH", str(path))
    assert (
        main(
            [
                "market",
                "futures",
                "status",
                "--symbol",
                "ETHUSDT",
                "--interval",
                "1h",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "research",
                "futures",
                "inspect",
                "--symbol",
                "ETHUSDT",
                "--interval",
                "1h",
                "--start",
                futures_candles[0].open_time.isoformat(),
                "--end",
                futures_candles[-1].open_time.isoformat(),
                "--disable-funding",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "USD_M_FUTURES" in output
    assert '"end_is_inclusive": true' in output


def test_futures_backtest_cli_and_invalid_leverage(
    tmp_path,
    monkeypatch,
    futures_candles,
    mark_prices,
) -> None:
    path = tmp_path / "cli.sqlite3"
    seed(path, futures_candles, mark_prices)
    monkeypatch.setenv("ADAPTIVE_TRADER_DB_PATH", str(path))
    output = tmp_path / "report"
    base = [
        "research",
        "futures",
        "backtest",
        "--symbol",
        "ETHUSDT",
        "--interval",
        "1h",
        "--start",
        futures_candles[0].open_time.isoformat(),
        "--end",
        futures_candles[-1].open_time.isoformat(),
        "--warmup-candles",
        "1",
        "--disable-funding",
        "--output-dir",
        str(output),
    ]
    assert main(base) == 0
    assert (output / "summary.json").exists()
    invalid = [*base, "--leverage", "4"]
    assert main(invalid) == 2


def test_futures_research_never_downloads_missing_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADAPTIVE_TRADER_DB_PATH", str(tmp_path / "empty.sqlite3"))

    class ForbiddenNetwork:
        def __init__(self, *args, **kwargs):
            raise AssertionError("research attempted network access")

    monkeypatch.setattr(
        "adaptive_trader.cli.main.BinanceFuturesPublicClient",
        ForbiddenNetwork,
    )
    assert (
        main(
            [
                "research",
                "futures",
                "inspect",
                "--symbol",
                "ETHUSDT",
                "--interval",
                "1h",
                "--start",
                "2025-01-01T00:00:00Z",
                "--end",
                "2025-01-02T00:00:00Z",
                "--disable-funding",
            ]
        )
        == 2
    )
