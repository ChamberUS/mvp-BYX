from decimal import Decimal

from adaptive_trader.research.benchmarks import buy_and_hold, cash_benchmark
from adaptive_trader.research.costs import run_cost_scenarios
from adaptive_trader.research.datasets import holdout_split, validate_dataset
from adaptive_trader.research.experiment import ResearchExperimentRunner
from adaptive_trader.research.models import SelectionMode, WalkForwardMode
from adaptive_trader.research.sensitivity import (
    ParameterGridError,
    apply_parameters,
    parameter_combinations,
)
from adaptive_trader.research.splits import build_walk_forward_plan
from adaptive_trader.research.walk_forward import WalkForwardRunner


def test_experiment_runner_is_deterministic_and_respects_warmup(
    daily_candles, research_config
) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=2,
    )
    runner = ResearchExperimentRunner(clock=lambda: daily_candles[-1].open_time)

    first = runner.run_segments((split.validation,), research_config)[0]
    second = runner.run_segments((split.validation,), research_config)[0]

    assert first.result is not None
    assert second.result is not None
    assert first.result.metrics == second.result.metrics
    assert all(
        trade.entry_time >= split.validation.evaluation_start_time
        for trade in first.result.trades
    )


def test_experiment_runner_records_segment_failure(daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )

    def failing_factory(config):
        raise ValueError("factory failure")

    run = ResearchExperimentRunner(component_factory=failing_factory).run_segment(
        split.test, research_config
    )

    assert run.failed is True
    assert run.result is None
    assert "factory failure" in (run.error or "")


def test_benchmarks_apply_costs_and_cash_is_flat(daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )

    buy_hold = buy_and_hold(split.test, research_config)
    cash = cash_benchmark(research_config)

    assert buy_hold.total_costs > 0
    assert buy_hold.net_return_percent < buy_hold.gross_return_percent
    assert cash.net_return_percent == Decimal("0")


def test_cost_scenarios_are_conservative(daily_candles, research_config) -> None:
    dataset = validate_dataset(daily_candles)
    split = holdout_split(
        dataset,
        train_percent=Decimal("50"),
        validation_percent=Decimal("25"),
        test_percent=Decimal("25"),
        warmup_candles=1,
    )

    rows = run_cost_scenarios(split.test, research_config, ResearchExperimentRunner())
    returns = {str(row["scenario"]): row["net_return"] for row in rows}

    assert returns["LOW_COST"] >= returns["BASE_COST"]
    assert returns["BASE_COST"] >= returns["HIGH_COST"]


def test_parameter_grid_is_small_explicit_and_forbids_cost_optimization(
    research_config,
) -> None:
    combinations = parameter_combinations(
        {"short_ema_period": (15, 20), "long_ema_period": (40, 50)},
        maximum_combinations=4,
    )

    assert len(combinations) == 4
    assert apply_parameters(research_config, combinations[0]).short_ema_period in {15, 20}
    try:
        parameter_combinations({"taker_fee_bps": (Decimal("0"),)}, maximum_combinations=1)
    except ParameterGridError:
        pass
    else:
        raise AssertionError("cost parameters must not be optimized")


def test_walk_forward_grid_selects_only_from_training_results(
    daily_candles, research_config
) -> None:
    dataset = validate_dataset(daily_candles)
    plan = build_walk_forward_plan(
        dataset,
        train_days=3,
        validation_days=2,
        step_days=2,
        warmup_candles=1,
        mode=WalkForwardMode.ROLLING,
    )

    folds = WalkForwardRunner().run(
        plan,
        research_config,
        selection_mode=SelectionMode.SELECT_FROM_PREDEFINED_GRID,
        parameter_grid={"short_ema_period": (2,), "long_ema_period": (3,)},
        minimum_closed_trades=0,
    )

    assert folds
    assert all(item.selection_status == "SELECTED_FROM_TRAIN" for item in folds)
    assert all(item.validation is not None for item in folds)
