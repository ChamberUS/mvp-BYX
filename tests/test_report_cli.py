from pathlib import Path

from adaptive_trader.backtest.report import read_json, render_summary, write_json, write_trades_csv
from adaptive_trader.cli.main import main


def test_report_writers_and_cli_do_not_need_network(
    tmp_path: Path, monkeypatch, capsys, candle
) -> None:

    from adaptive_trader.backtest.cli import build_engine
    from adaptive_trader.config.settings import TradingConfig

    config = TradingConfig(short_ema_period=2, long_ema_period=3, atr_period=2, volume_period=2)
    result = build_engine(config).run((candle,))
    json_path = tmp_path / "report.json"
    csv_path = tmp_path / "trades.csv"
    write_json(result, json_path)
    write_trades_csv(result, csv_path)

    assert read_json(json_path)["report_version"] == "2"
    assert "BACKTEST ONLY" in render_summary(result)
    assert "trade_id" in csv_path.read_text(encoding="utf-8")
    monkeypatch.setenv("ADAPTIVE_TRADER_DB_PATH", str(tmp_path / "cli.sqlite3"))
    assert main(["db", "init"]) == 0
    assert main(["db", "status"]) == 0
    assert main(["doctor"]) == 0
    assert "research-only" in capsys.readouterr().out
