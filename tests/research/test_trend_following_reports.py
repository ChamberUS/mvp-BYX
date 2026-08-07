from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_trader.research.trend_following_catalog import (
    TrendFollowingPeriods,
    load_trend_following_catalog,
)
from adaptive_trader.research.trend_following_experiment import (
    TrendFollowingExperimentBundle,
    TrendFollowingExperimentRequest,
)
from adaptive_trader.research.trend_following_report import (
    expected_trend_following_artifact_names,
    write_trend_following_report,
)


def _bundle(tmp_path: Path) -> TrendFollowingExperimentBundle:
    now = datetime(2026, 8, 6, tzinfo=UTC)
    output = tmp_path / "trend-following-report-fixture"
    output.mkdir()
    lock_bytes = b'{"locked_before_validation": true}\n'
    (output / "trend_following_validation_lock.json").write_bytes(lock_bytes)
    request = TrendFollowingExperimentRequest(
        symbol="ETHUSDT",
        source_interval="1h",
        strategy_interval="1d",
        periods=TrendFollowingPeriods.pre_registered(),
        markets=("spot",),
        futures_modes=(),
        leverage=Decimal("1"),
        output_dir=tmp_path,
    )
    return TrendFollowingExperimentBundle(
        experiment_id=output.name,
        output_path=output,
        started_at=now,
        completed_at=now,
        duration_seconds=ZERO,
        request=request,
        catalog=load_trend_following_catalog(),
        aggregation_integrity=(),
        daily_dataset_hashes=(),
        decision_funnel=(),
        decision_traces=(),
        development_results=(),
        development_walk_forward=(),
        operational_viability=(),
        development_selection=(),
        validation_lock={"locked_before_validation": True},
        validation_lock_bytes=lock_bytes,
        validation_results=(),
        validation_walk_forward=(),
        defensive_risk_comparison=(),
        cost_scenarios=(),
        funding_impact=(),
        side_contribution=(),
        concentration_analysis=(),
        bootstrap_uncertainty=(),
        assessments=(),
        future_confirmation_plan={"status": "NO_CONFIRMATION_PLAN"},
        manifest={"validation_lock_preserved": True},
    )


ZERO = Decimal("0")


def test_writer_creates_exactly_the_22_registered_artifacts(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)

    output = write_trend_following_report(
        bundle,
        git_commit="commit",
        git_dirty=True,
    )

    expected = expected_trend_following_artifact_names()
    assert len(expected) == 22
    assert tuple(sorted(path.name for path in output.iterdir())) == tuple(
        sorted(expected)
    )
    assert (
        output / "trend_following_validation_lock.json"
    ).read_bytes() == bundle.validation_lock_bytes
    assert "2025 e 2026 não foram carregados" in (
        output / "trend_following_report.md"
    ).read_text(encoding="utf-8")


def test_writer_rejects_a_changed_validation_lock(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    lock_path = bundle.output_path / "trend_following_validation_lock.json"
    lock_path.write_text('{"tampered": true}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="validation lock changed"):
        write_trend_following_report(
            bundle,
            git_commit="commit",
            git_dirty=True,
        )

