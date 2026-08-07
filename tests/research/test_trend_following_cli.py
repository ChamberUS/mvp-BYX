from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_trader.cli.main import main


def _valid_args(output_dir: Path) -> list[str]:
    return [
        "research",
        "trend-following",
        "run",
        "--symbol",
        "ETHUSDT",
        "--source-interval",
        "1h",
        "--strategy-interval",
        "1d",
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


@pytest.mark.parametrize(
    ("option", "replacement"),
    (
        ("--source-interval", "4h"),
        ("--strategy-interval", "4h"),
        ("--leverage", "2"),
        ("--development-start", "2021-01-01T00:00:00Z"),
        ("--development-end", "2025-01-01T00:00:00Z"),
        ("--validation-start", "2025-01-01T00:00:00Z"),
        ("--validation-end", "2026-01-01T00:00:00Z"),
    ),
)
def test_invalid_request_is_rejected_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    option: str,
    replacement: str,
) -> None:
    class ForbiddenRepository:
        def __init__(self, path: Path) -> None:
            raise AssertionError(f"database opened for invalid request: {path}")

    monkeypatch.setattr(
        "adaptive_trader.cli.main.DatabaseRepository",
        ForbiddenRepository,
    )
    args = _valid_args(tmp_path)
    args[args.index(option) + 1] = replacement

    assert main(args) == 2


def test_run_is_offline_and_cannot_execute_external_orders(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = {"network": 0, "orders": 0, "service": 0}

    class Repository:
        def __init__(self, path: Path) -> None:
            self.path = path

        def close(self) -> None:
            pass

    class Service:
        def __init__(self, repository: object, config: object) -> None:
            pass

        def run(
            self,
            request: object,
            *,
            git_commit: str,
            git_dirty: bool,
        ) -> object:
            calls["service"] += 1
            return SimpleNamespace(
                experiment_id="trend-following-fixture",
                catalog=SimpleNamespace(canonical_hash="catalog-hash"),
                development_selection=(
                    {
                        "market": "SPOT",
                        "mode": "LONG",
                        "selected_variant_id": None,
                    },
                ),
                assessments=(
                    {
                        "market": "SPOT",
                        "mode": "LONG",
                        "variant_id": None,
                        "classification": "NO_DEVELOPMENT_HYPOTHESIS",
                    },
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
        *,
        git_commit: str,
        git_dirty: bool,
    ) -> Path:
        return tmp_path / "trend-following-fixture"

    monkeypatch.setattr("adaptive_trader.cli.main.DatabaseRepository", Repository)
    monkeypatch.setattr(
        "adaptive_trader.cli.main.TrendFollowingExperimentService",
        Service,
    )
    monkeypatch.setattr(
        "adaptive_trader.cli.main.write_trend_following_report",
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

    assert main(_valid_args(tmp_path)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["consumed_2025_used"] is False
    assert payload["consumed_2026_used"] is False
    assert payload["leverages_executed"] == ["1"]
    assert payload["network_used"] is False
    assert payload["external_orders_sent"] is False
    assert calls == {"network": 0, "orders": 0, "service": 1}


def test_show_reads_only_registered_local_summaries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    object_names = (
        "experiment_manifest.json",
        "hypothesis_catalog.json",
        "trend_following_validation_lock.json",
        "future_confirmation_plan.json",
    )
    array_names = (
        "development_selection.json",
        "hypothesis_assessment.json",
    )
    for name in object_names:
        (experiment / name).write_text(
            json.dumps({"name": name}),
            encoding="utf-8",
        )
    for name in array_names:
        (experiment / name).write_text(
            json.dumps([{"name": name}]),
            encoding="utf-8",
        )

    assert main(
        [
            "research",
            "trend-following",
            "show",
            "--experiment",
            str(experiment),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert all(name in output for name in object_names + array_names)
