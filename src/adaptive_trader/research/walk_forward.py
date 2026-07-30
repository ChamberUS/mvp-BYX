"""Walk-forward execution with fixed or explicitly bounded parameter selection."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from adaptive_trader.backtest.models import BacktestResult
from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.research.experiment import ResearchExperimentRunner
from adaptive_trader.research.models import (
    SelectionCriterion,
    SelectionMode,
    WalkForwardFoldResult,
    WalkForwardPlan,
)
from adaptive_trader.research.sensitivity import (
    ParameterGridError,
    apply_parameters,
    parameter_combinations,
    select_from_results,
)


class WalkForwardRunner:
    def __init__(self, runner: ResearchExperimentRunner | None = None) -> None:
        self._runner = runner or ResearchExperimentRunner()

    def run(
        self,
        plan: WalkForwardPlan,
        config: TradingConfig,
        *,
        selection_mode: SelectionMode = SelectionMode.FIXED_PARAMETERS,
        parameter_grid: dict[str, tuple[Any, ...]] | None = None,
        criterion: SelectionCriterion = SelectionCriterion.RETURN_TO_DRAWDOWN,
        maximum_parameter_combinations: int = 100,
        minimum_closed_trades: int = 0,
        maximum_allowed_drawdown_percent: Decimal | None = None,
        minimum_profit_factor: Decimal | None = None,
    ) -> tuple[WalkForwardFoldResult, ...]:
        if selection_mode is SelectionMode.SELECT_FROM_PREDEFINED_GRID and not parameter_grid:
            raise ParameterGridError("parameter_grid is required for grid selection")
        combinations = (
            parameter_combinations(
                parameter_grid or {}, maximum_combinations=maximum_parameter_combinations
            )
            if selection_mode is SelectionMode.SELECT_FROM_PREDEFINED_GRID
            else ()
        )
        results: list[WalkForwardFoldResult] = []
        for fold in plan.folds:
            warnings: list[str] = list(fold.warnings)
            if selection_mode is SelectionMode.FIXED_PARAMETERS:
                selected = config
                train = self._runner.run_segment(fold.train, selected)
                validation = self._runner.run_segment(fold.validation, selected)
                status = SelectionMode.FIXED_PARAMETERS.value
            else:
                candidates: list[tuple[dict[str, Any], BacktestResult]] = []
                for parameters in combinations:
                    candidate = apply_parameters(config, parameters)
                    train_run = self._runner.run_segment(fold.train, candidate)
                    if train_run.result is not None:
                        candidates.append((parameters, train_run.result))
                selected_pair = select_from_results(
                    tuple(candidates),
                    criterion=criterion,
                    minimum_closed_trades=minimum_closed_trades,
                    maximum_allowed_drawdown_percent=maximum_allowed_drawdown_percent,
                    minimum_profit_factor=minimum_profit_factor,
                )
                if selected_pair is None:
                    warnings.append("NO_VALID_CONFIGURATION: base configuration used")
                    selected = config
                    status = "NO_VALID_CONFIGURATION"
                else:
                    selected = apply_parameters(config, selected_pair[0])
                    status = "SELECTED_FROM_TRAIN"
                train = self._runner.run_segment(fold.train, selected)
                validation = self._runner.run_segment(fold.validation, selected)
            results.append(
                WalkForwardFoldResult(
                    fold=fold,
                    selected_parameters=selected.as_dict(),
                    train=train,
                    validation=validation,
                    warnings=tuple(warnings),
                    selection_status=status,
                )
            )
        return tuple(results)
