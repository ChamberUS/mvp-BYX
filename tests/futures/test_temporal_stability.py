from datetime import UTC, datetime
from types import SimpleNamespace

from adaptive_trader.futures.real_validation import predefined_futures_variants
from adaptive_trader.futures.temporal_robustness import StabilityStatus
from adaptive_trader.futures.temporal_robustness_report import (
    expected_temporal_artifact_names,
    write_temporal_robustness_report,
)


def test_temporal_stability_has_no_numeric_single_score() -> None:
    assert {item.value for item in StabilityStatus} == {
        "STABLE",
        "MIXED",
        "UNSTABLE",
        "INCONCLUSIVE",
    }


def test_temporal_report_writes_every_required_artifact(tmp_path) -> None:
    request = SimpleNamespace(
        symbol="ETHUSDT",
        interval="1h",
        start=datetime(2022, 1, 1, tzinfo=UTC),
        end=datetime(2025, 12, 31, 23, tzinfo=UTC),
        bootstrap_iterations=20,
        bootstrap_seed=42,
    )
    integrity = SimpleNamespace(
        readiness="READY",
        combined_dataset_hash="a" * 64,
        futures_candle_hash="b" * 64,
        mark_price_hash="c" * 64,
        funding_hash="d" * 64,
        candles=SimpleNamespace(count=1),
        marks=SimpleNamespace(count=1),
        funding=SimpleNamespace(event_count=1),
    )
    bundle = SimpleNamespace(
        experiment_id="temporal-fixture",
        started_at=request.start,
        completed_at=request.end,
        duration_seconds=0,
        request=request,
        integrity=integrity,
        variants=predefined_futures_variants(),
        volatility_boundaries=(0, 0, 0),
        yearly_rows=(),
        quarterly_rows=(),
        rolling_rows=(),
        walk_forward_rows=(),
        boundary_rows=(),
        leave_one_year_out_rows=(),
        regime_rows=(),
        transition_rows=(),
        volatility_rows=(),
        market_context_rows=(),
        side_rows=(),
        funding_rows=(),
        cost_rows=(),
        concentration_rows=(),
        bootstrap_summaries=(),
        scorecards=(),
        classifications=(),
        explanations_2025=(),
        warnings=(),
        reproducibility_hash="e" * 64,
    )
    output = write_temporal_robustness_report(
        bundle,
        tmp_path,
        git_commit="a889362",
        git_dirty=True,
    )
    assert {item.name for item in output.iterdir()} == set(
        expected_temporal_artifact_names()
    )
