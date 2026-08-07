from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.market import (
    ContractType,
    MarketType,
    PositionSide,
    TradingMode,
)
from adaptive_trader.futures.models import (
    FuturesBacktestResult,
    FuturesExitReason,
    FuturesMetrics,
    FuturesTrade,
)
from adaptive_trader.futures.temporal_robustness import TemporalCandleContext


def make_trade(
    exit_time: datetime,
    net_pnl: str,
    *,
    side: PositionSide = PositionSide.LONG,
    funding: str = "0",
    fees: str = "1",
    holding_hours: int = 24,
) -> FuturesTrade:
    net = Decimal(net_pnl)
    funding_value = Decimal(funding)
    fee_value = Decimal(fees)
    gross = net + fee_value - funding_value
    entry_time = exit_time - timedelta(hours=holding_hours)
    return FuturesTrade(
        trade_id=f"{exit_time.isoformat()}-{side.value}",
        symbol="ETHUSDT",
        side=side,
        quantity=Decimal("1"),
        leverage=Decimal("1"),
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=Decimal("100"),
        exit_price=Decimal("101"),
        entry_notional=Decimal("100"),
        initial_margin=Decimal("100"),
        free_balance_after_entry=Decimal("9900"),
        gross_pnl=gross,
        net_pnl=net,
        trading_fees=fee_value,
        liquidation_fee=Decimal("0"),
        funding_paid=max(Decimal("0"), -funding_value),
        funding_received=max(Decimal("0"), funding_value),
        net_funding=funding_value,
        exit_reason=FuturesExitReason.TAKE_PROFIT if net > 0 else FuturesExitReason.STOP_LOSS,
        holding_candles=holding_hours,
        intrabar_ambiguous=False,
    )


def make_result(
    trades: tuple[FuturesTrade, ...],
    *,
    start: datetime = datetime(2022, 1, 5, tzinfo=UTC),
    end: datetime = datetime(2025, 12, 31, 23, tzinfo=UTC),
) -> FuturesBacktestResult:
    net = sum((item.net_pnl for item in trades), Decimal("0"))
    gross = sum((item.gross_pnl for item in trades), Decimal("0"))
    long_trades = tuple(item for item in trades if item.side is PositionSide.LONG)
    short_trades = tuple(item for item in trades if item.side is PositionSide.SHORT)
    metrics = FuturesMetrics(
        initial_wallet=Decimal("10000"),
        final_wallet=Decimal("10000") + net,
        gross_pnl=gross,
        net_pnl=net,
        long_pnl=sum((item.net_pnl for item in long_trades), Decimal("0")),
        short_pnl=sum((item.net_pnl for item in short_trades), Decimal("0")),
        funding_paid=sum((item.funding_paid for item in trades), Decimal("0")),
        funding_received=sum((item.funding_received for item in trades), Decimal("0")),
        net_funding=sum((item.net_funding for item in trades), Decimal("0")),
        funding_event_count=0,
        trading_fees=sum((item.trading_fees for item in trades), Decimal("0")),
        liquidation_fees=Decimal("0"),
        liquidation_count=0,
        trade_count=len(trades),
        long_trade_count=len(long_trades),
        short_trade_count=len(short_trades),
        long_win_rate=None,
        short_win_rate=None,
        average_margin_utilization=Decimal("0"),
        maximum_margin_utilization=Decimal("0"),
        average_effective_leverage=Decimal("0"),
        maximum_effective_leverage=Decimal("0"),
        maximum_position_notional=Decimal("100"),
        average_initial_margin=Decimal("100"),
        minimum_free_balance=Decimal("9900"),
        return_on_wallet=net / Decimal("100"),
        return_on_notional=net / Decimal("100"),
        maximum_drawdown=Decimal("0"),
        minimum_margin_ratio=None,
        margin_call_count=0,
        bankrupt=False,
        depleted=False,
        exposure_long_percent=Decimal("0"),
        exposure_short_percent=Decimal("0"),
        fees_as_percent_of_margin=Decimal("0"),
        funding_as_percent_of_margin=Decimal("0"),
    )
    return FuturesBacktestResult(
        report_version="test",
        strategy_version="fixed",
        market_type=MarketType.USD_M_FUTURES,
        contract_type=ContractType.PERPETUAL,
        trading_mode=TradingMode.FUTURES_LONG_SHORT,
        leverage=Decimal("1"),
        symbol="ETHUSDT",
        interval="1h",
        start_time=start,
        end_time=end,
        input_candle_count=100,
        warmup_candle_count=100,
        evaluated_candle_count=100,
        metrics=metrics,
        trades=trades,
        warnings=(),
        equity_curve=(),
        margin_utilization_curve=(),
        effective_leverage_curve=(),
    )


def make_context(
    open_time: datetime,
    *,
    regime: str = "TRENDING_UP",
    volatility_bucket: str = "MEDIUM",
) -> TemporalCandleContext:
    return TemporalCandleContext(
        open_time=open_time,
        regime=regime,
        volatility_bucket=volatility_bucket,
        atr_relative=Decimal("0.01"),
        return_24h=Decimal("1"),
        return_7d=Decimal("2"),
        return_30d=Decimal("3"),
        long_ema_distance_percent=Decimal("1"),
        long_ema_slope_percent=Decimal("0.1"),
        directional_persistence=Decimal("0.5"),
    )
