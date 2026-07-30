"""Small, explicit parameter grids and local sensitivity analysis."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from itertools import product
from typing import Any

from adaptive_trader.backtest.models import BacktestResult
from adaptive_trader.config.settings import ConfigError, TradingConfig
from adaptive_trader.domain.models import SerializedValue
from adaptive_trader.research.models import SelectionCriterion


class ParameterGridError(ValueError):
    """Raised when a research grid is invalid or too large."""


_ALLOWED_PARAMETERS = {
    "short_ema_period",
    "long_ema_period",
    "stop_atr_multiple",
    "target_r_multiple",
    "minimum_volume_ratio",
    "maximum_atr_relative",
}
_FORBIDDEN_PARAMETERS = {"maker_fee_bps", "taker_fee_bps", "spread_bps", "slippage_bps"}


def parameter_combinations(
    grid: dict[str, tuple[Any, ...]], *, maximum_combinations: int = 100
) -> tuple[dict[str, Any], ...]:
    if maximum_combinations < 1:
        raise ParameterGridError("maximum_parameter_combinations must be positive")
    if any(name not in _ALLOWED_PARAMETERS for name in grid):
        forbidden = sorted(set(grid) - _ALLOWED_PARAMETERS)
        raise ParameterGridError(f"unsupported or forbidden parameters: {forbidden}")
    names = tuple(sorted(grid))
    values = tuple(dict.fromkeys(grid[name]) for name in names)
    count = 1
    for options in values:
        count *= len(options)
    if count > maximum_combinations:
        raise ParameterGridError(
            f"parameter grid has {count} combinations; maximum is {maximum_combinations}"
        )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for combination in product(*values):
        item = dict(zip(names, combination, strict=True))
        key = tuple((name, str(item[name])) for name in names)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)


def apply_parameters(config: TradingConfig, parameters: dict[str, Any]) -> TradingConfig:
    if set(parameters) - _ALLOWED_PARAMETERS:
        raise ParameterGridError("only strategy parameters may be selected")
    try:
        return replace(config, **parameters)
    except (ConfigError, TypeError, ValueError) as exc:
        raise ParameterGridError(f"invalid strategy parameter combination: {parameters}") from exc


def return_to_drawdown(result: BacktestResult) -> Decimal:
    drawdown = result.metrics.maximum_drawdown_percent
    net_return = result.metrics.net_return / result.metrics.initial_capital * Decimal("100")
    if drawdown == 0:
        return net_return if net_return > 0 else Decimal("0")
    return net_return / drawdown


def criterion_value(result: BacktestResult, criterion: SelectionCriterion) -> Decimal:
    metrics = result.metrics
    if criterion is SelectionCriterion.NET_RETURN:
        return metrics.net_return
    if criterion is SelectionCriterion.PROFIT_FACTOR:
        return metrics.profit_factor or Decimal("0")
    if criterion is SelectionCriterion.EXPECTANCY:
        return metrics.expectancy_per_trade or Decimal("0")
    if criterion is SelectionCriterion.MAXIMUM_DRAWDOWN_PERCENT:
        return -metrics.maximum_drawdown_percent
    if criterion is SelectionCriterion.RETURN_TO_DRAWDOWN:
        return return_to_drawdown(result)
    return (return_to_drawdown(result) + (metrics.profit_factor or Decimal("0"))) / Decimal("2")


def select_from_results(
    candidates: tuple[tuple[dict[str, Any], BacktestResult], ...],
    *,
    criterion: SelectionCriterion,
    minimum_closed_trades: int = 0,
    maximum_allowed_drawdown_percent: Decimal | None = None,
    minimum_profit_factor: Decimal | None = None,
) -> tuple[dict[str, Any], BacktestResult] | None:
    valid: list[tuple[dict[str, Any], BacktestResult]] = []
    for parameters, result in candidates:
        metrics = result.metrics
        if metrics.closed_trade_count < minimum_closed_trades:
            continue
        if (
            maximum_allowed_drawdown_percent is not None
            and metrics.maximum_drawdown_percent > maximum_allowed_drawdown_percent
        ):
            continue
        if minimum_profit_factor is not None and (
            metrics.profit_factor is None or metrics.profit_factor < minimum_profit_factor
        ):
            continue
        valid.append((parameters, result))
    if not valid:
        return None
    return max(valid, key=lambda pair: criterion_value(pair[1], criterion))


def local_variations(
    config: TradingConfig, *, maximum_combinations: int = 100
) -> tuple[TradingConfig, ...]:
    variations: list[dict[str, Any]] = [{}]
    variations.extend(
        {
            "short_ema_period": value,
        }
        for value in (
            max(1, int(config.short_ema_period * Decimal("0.8"))),
            int(config.short_ema_period * Decimal("1.2")),
        )
    )
    variations.extend(
        {
            "long_ema_period": value,
        }
        for value in (
            max(2, int(config.long_ema_period * Decimal("0.8"))),
            int(config.long_ema_period * Decimal("1.2")),
        )
    )
    for name, value, percentage in (
        ("stop_atr_multiple", config.stop_atr_multiple, Decimal("0.25")),
        ("target_r_multiple", config.target_r_multiple, Decimal("0.25")),
        ("minimum_volume_ratio", config.minimum_volume_ratio, Decimal("0.2")),
    ):
        variations.extend(
            {name: value * (Decimal("1") + direction * percentage)}
            for direction in (Decimal("-1"), Decimal("1"))
        )
    if len(variations) > maximum_combinations:
        raise ParameterGridError("local sensitivity exceeds maximum_parameter_combinations")
    results: list[TradingConfig] = []
    seen: set[str] = set()
    for parameters in variations:
        short_period = int(parameters.get("short_ema_period", config.short_ema_period))
        long_period = int(parameters.get("long_ema_period", config.long_ema_period))
        if long_period <= short_period:
            continue
        try:
            candidate = apply_parameters(config, parameters)
        except ParameterGridError:
            continue
        identity = str(candidate.as_dict())
        if identity not in seen:
            seen.add(identity)
            results.append(candidate)
    return tuple(results)


def parameters_to_dict(config: TradingConfig) -> dict[str, SerializedValue]:
    values = config.as_dict()
    return {
        key: values[key]
        for key in (
            "short_ema_period",
            "long_ema_period",
            "stop_atr_multiple",
            "target_r_multiple",
            "minimum_volume_ratio",
            "maximum_atr_relative",
        )
    }
