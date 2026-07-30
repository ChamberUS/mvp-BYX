"""Conservative benchmark calculations using the same simulated costs."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import OrderIntent, SignalDirection
from adaptive_trader.execution.backtest import BacktestExecutionConfig, BacktestOrderExecutor
from adaptive_trader.research.models import BenchmarkResult, DatasetSegment


def _execution_config(config: TradingConfig) -> BacktestExecutionConfig:
    return BacktestExecutionConfig(
        maker_fee_bps=config.maker_fee_bps,
        taker_fee_bps=config.taker_fee_bps,
        slippage_bps=config.slippage_bps,
        spread_bps=config.spread_bps,
    )


def _drawdown_percent(curve: tuple[Decimal, ...]) -> Decimal:
    peak = Decimal("0")
    maximum = Decimal("0")
    for value in curve:
        peak = max(peak, value)
        if peak:
            maximum = max(maximum, (peak - value) / peak * Decimal("100"))
    return maximum


def _volatility_percent(curve: tuple[Decimal, ...]) -> Decimal:
    if len(curve) < 2:
        return Decimal("0")
    returns = tuple(
        (current - previous) / previous
        for previous, current in zip(curve, curve[1:], strict=False)
        if previous
    )
    if len(returns) < 2:
        return Decimal("0")
    mean = sum(returns, Decimal("0")) / Decimal(len(returns))
    variance = sum((value - mean) ** 2 for value in returns) / Decimal(len(returns))
    return variance.sqrt() * Decimal("100")


def _intent(
    symbol: str,
    direction: SignalDirection,
    quantity: Decimal,
    price: Decimal,
    created_at: datetime,
) -> OrderIntent:
    return OrderIntent(
        intent_id=f"benchmark-{direction.value}-{created_at.isoformat()}",
        symbol=symbol,
        direction=direction,
        quantity=quantity,
        price=price,
        stop_loss=price,
        take_profit=price,
        created_at=created_at,
    )


def buy_and_hold(segment: DatasetSegment, config: TradingConfig) -> BenchmarkResult:
    candles = segment.evaluation_candles
    initial = config.initial_balance
    first = candles[0]
    last = candles[-1]
    executor = BacktestOrderExecutor(_execution_config(config))
    reference_cost_bps = config.spread_bps + config.slippage_bps
    effective_buy = (first.open * (Decimal("1") + reference_cost_bps / Decimal("10000"))).quantize(
        Decimal("0.00000001")
    )
    fee_rate = config.taker_fee_bps / Decimal("10000")
    quantity = (initial / (effective_buy * (Decimal("1") + fee_rate))).quantize(
        Decimal("0.00000001"), rounding=ROUND_DOWN
    )
    buy_intent = _intent(first.symbol, SignalDirection.BUY, quantity, first.open, first.open_time)
    executor.set_reference_price(first.open)
    buy_order = executor.execute(buy_intent)
    cash = initial - buy_order.price * buy_order.quantity - buy_order.fee
    sell_intent = _intent(
        last.symbol, SignalDirection.SELL, buy_order.quantity, last.close, last.open_time
    )
    executor.set_reference_price(last.close)
    sell_order = executor.execute(sell_intent)
    final = cash + sell_order.price * sell_order.quantity - sell_order.fee
    gross_final = initial * last.close / first.open
    curve = tuple(cash + buy_order.quantity * candle.close for candle in candles)
    exposures = tuple(
        buy_order.quantity * candle.close / equity * Decimal("100")
        for candle, equity in zip(candles, curve, strict=True)
        if equity
    )
    total_costs = buy_order.fee + sell_order.fee + buy_order.spread_cost + buy_order.slippage_cost
    total_costs += sell_order.spread_cost + sell_order.slippage_cost
    return BenchmarkResult(
        name="BUY_AND_HOLD",
        initial_capital=initial,
        final_capital=final,
        gross_return_percent=(gross_final - initial) / initial * Decimal("100"),
        net_return_percent=(final - initial) / initial * Decimal("100"),
        maximum_drawdown_percent=_drawdown_percent(curve),
        volatility_percent=_volatility_percent(curve),
        exposure_percent=(
            sum(exposures, Decimal("0")) / Decimal(len(exposures))
            if exposures
            else Decimal("0")
        ),
        total_costs=total_costs,
    )


def cash_benchmark(config: TradingConfig) -> BenchmarkResult:
    return BenchmarkResult(
        name="CASH",
        initial_capital=config.initial_balance,
        final_capital=config.initial_balance,
        gross_return_percent=Decimal("0"),
        net_return_percent=Decimal("0"),
        maximum_drawdown_percent=Decimal("0"),
        volatility_percent=Decimal("0"),
        exposure_percent=Decimal("0"),
        total_costs=Decimal("0"),
    )


def calculate_benchmarks(
    segment: DatasetSegment, config: TradingConfig
) -> tuple[BenchmarkResult, ...]:
    return (buy_and_hold(segment, config), cash_benchmark(config))
