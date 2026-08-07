from pathlib import Path

from adaptive_trader.cli.main import main


def _args(output: Path) -> list[str]:
    return [
        "research", "pullback", "calibrate",
        "--symbol", "ETHUSDT", "--interval", "1h",
        "--development-start", "2022-01-01T00:00:00Z",
        "--development-end", "2023-12-31T23:00:00Z",
        "--validation-start", "2024-01-01T00:00:00Z",
        "--validation-end", "2024-12-31T23:00:00Z",
        "--consumed-start", "2025-01-01T00:00:00Z",
        "--consumed-end", "2026-07-01T00:00:00Z",
        "--leverage", "2", "--output-dir", str(output), "--yes",
    ]


def test_cli_rejects_leverage_above_one_before_data_access(
    tmp_path: Path,
) -> None:
    assert main(_args(tmp_path)) == 2
