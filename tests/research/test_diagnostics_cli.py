from adaptive_trader.cli.main import _parser


def test_diagnostics_cli_commands_are_declared() -> None:
    diagnose = _parser().parse_args(
        [
            "research",
            "diagnose",
            "run",
            "--symbol",
            "ETHUSDT",
            "--interval",
            "1h",
            "--start",
            "2022-01-01T00:00:00Z",
            "--end",
            "2025-12-31T23:00:00Z",
            "--exclude-start",
            "2026-01-01T00:00:00Z",
            "--exclude-end",
            "2026-07-01T00:00:00Z",
            "--output-dir",
            "reports/research",
        ]
    )
    assert diagnose.research_command == "diagnose"


def test_timeframe_comparison_declares_consumed_test_exclusion() -> None:
    args = _parser().parse_args(
        [
            "research",
            "timeframe",
            "compare",
            "--symbol",
            "ETHUSDT",
            "--intervals",
            "1h",
            "--start",
            "2022-01-01T00:00:00Z",
            "--end",
            "2025-12-31T23:00:00Z",
            "--output-dir",
            "reports/research",
        ]
    )

    assert args.exclude_start == "2026-01-01T00:00:00Z"
    assert args.exclude_end == "2026-07-01T00:00:00Z"
