from decimal import Decimal

from adaptive_trader.research.datasets import holdout_split, validate_dataset
from adaptive_trader.research.models import SelectionMode, WalkForwardMode
from adaptive_trader.research.service import run_holdout_experiment, run_walk_forward_experiment
from adaptive_trader.research.splits import build_walk_forward_plan


def test_holdout_service_writes_complete_report(tmp_path, daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )

    result = run_holdout_experiment(
        dataset=dataset,
        split=split,
        config=research_config,
        experiment_name="local holdout",
        output_root=tmp_path,
        gap_policy="WARN",
    )
    report_dir = tmp_path / result.experiment_id

    assert result.manifest.reproducibility_hash
    assert (report_dir / "manifest.json").exists()
    assert (report_dir / "summary.csv").exists()
    assert (report_dir / "sensitivity.csv").exists()
    assert (report_dir / "cost_scenarios.csv").exists()
    assert (report_dir / "report.md").exists()


def test_walk_forward_service_writes_validation_report(
    tmp_path, daily_candles, research_config
) -> None:
    dataset = validate_dataset(daily_candles)
    plan = build_walk_forward_plan(
        dataset,
        train_days=3,
        validation_days=2,
        step_days=2,
        warmup_candles=1,
        mode=WalkForwardMode.EXPANDING,
    )

    results = run_walk_forward_experiment(
        dataset=dataset,
        plan=plan,
        config=research_config,
        experiment_name="local walk",
        output_root=tmp_path,
        gap_policy="WARN",
        selection_mode=SelectionMode.FIXED_PARAMETERS,
    )

    assert len(results) == len(plan.folds)
    report_dirs = tuple(tmp_path.glob("*-walk"))
    assert report_dirs
    assert (report_dirs[0] / "folds.csv").exists()
