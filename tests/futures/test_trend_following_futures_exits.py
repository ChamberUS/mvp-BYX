from datetime import timedelta
from decimal import Decimal

from adaptive_trader.futures.models import FundingRate
from adaptive_trader.research.trend_following_engine import (
    TrendFollowingEngine,
    TrendFollowingEngineConfig,
    TrendFollowingExitReason,
)
from tests.trend_following_engine_helpers import (
    START,
    HourSpec,
    PriceSpec,
    daily_series,
    futures_hours,
)


def _config(
    daily,
    *,
    end_index: int,
    defensive_risk: bool = False,
    initial_capital: Decimal = Decimal("10000"),
    fee_bps: Decimal = Decimal("0"),
    margin_buffer_percent: Decimal = Decimal("1"),
) -> TrendFollowingEngineConfig:
    return TrendFollowingEngineConfig(
        market="futures",
        mode="long",
        variant_id="TEST",
        period="DEVELOPMENT",
        scenario="BASE",
        evaluation_start=daily[199].open_time,
        evaluation_end=daily[end_index].close_time or daily[end_index].open_time,
        exit_period=10,
        defensive_risk=defensive_risk,
        initial_capital=initial_capital,
        maximum_position_percent=Decimal("100"),
        fee_bps=fee_bps,
        spread_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        margin_buffer_percent=margin_buffer_percent,
    )


def test_intraday_move_through_initial_stop_does_not_create_donchian_exit() -> None:
    daily = daily_series(
        {
            199: PriceSpec(Decimal("110"), Decimal("111"), Decimal("99")),
            200: PriceSpec(Decimal("110"), Decimal("111"), Decimal("99")),
            201: PriceSpec(Decimal("90"), Decimal("110"), Decimal("89")),
            202: PriceSpec(Decimal("90"), Decimal("91"), Decimal("89")),
        },
        total_days=203,
    )
    hourly, marks = futures_hours(
        {
            199: HourSpec(Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
            200: HourSpec(Decimal("105"), Decimal("110"), Decimal("90"), Decimal("105")),
            201: HourSpec(Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
            202: HourSpec(Decimal("95"), Decimal("96"), Decimal("94"), Decimal("95")),
        }
    )

    result = TrendFollowingEngine().run(
        config=_config(daily, end_index=202),
        daily_candles=daily,
        hourly_candles=hourly,
        marks=marks,
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert hourly[1].low < trade.initial_stop
    assert trade.exit_reason == TrendFollowingExitReason.MACRO_FILTER_EXIT.value
    assert trade.exit_time == hourly[3].open_time
    assert result.liquidation_count == 0


def test_funding_and_mark_liquidation_precede_pending_daily_exit_even_when_fixed() -> None:
    daily = daily_series(
        {
            199: PriceSpec(Decimal("110"), Decimal("111"), Decimal("99")),
            200: PriceSpec(Decimal("90"), Decimal("110"), Decimal("89")),
            201: PriceSpec(Decimal("90"), Decimal("91"), Decimal("89")),
        },
        total_days=202,
    )
    hourly, marks = futures_hours(
        {
            199: HourSpec(Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
            200: HourSpec(Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
            201: HourSpec(
                Decimal("100"),
                Decimal("101"),
                Decimal("0.1"),
                Decimal("100"),
                mark_open=Decimal("0.1"),
                mark_high=Decimal("0.2"),
                mark_low=Decimal("0.05"),
                mark_close=Decimal("0.1"),
            ),
        }
    )
    funding = (
        FundingRate(
            symbol="ETHUSDT",
            funding_time=START + timedelta(days=201),
            funding_rate=Decimal("0.001"),
            mark_price=Decimal("100"),
        ),
    )

    result = TrendFollowingEngine().run(
        config=_config(daily, end_index=201, defensive_risk=False),
        daily_candles=daily,
        hourly_candles=hourly,
        marks=marks,
        funding=funding,
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == TrendFollowingExitReason.LIQUIDATION.value
    assert trade.funding_paid > 0
    assert result.liquidation_count == 1
    assert result.defensive_mode_activations == 1
    assert "UNEXPECTED_LIQUIDATION_AT_1X" in result.warnings
    assert trade.defensive_activated_at == hourly[2].open_time


def test_futures_fee_is_in_margin_cap_and_trade_net_pnl_includes_entry_fee() -> None:
    daily = daily_series(
        {
            199: PriceSpec(Decimal("101"), Decimal("101"), Decimal("99.9")),
            200: PriceSpec(Decimal("101"), Decimal("101"), Decimal("99.9")),
        },
        total_days=201,
        baseline_high=Decimal("100"),
        baseline_low=Decimal("99.9"),
    )
    hourly, marks = futures_hours(
        {
            199: HourSpec(Decimal("100"), Decimal("100"), Decimal("99"), Decimal("100")),
            200: HourSpec(Decimal("100"), Decimal("100"), Decimal("99"), Decimal("100")),
        }
    )

    result = TrendFollowingEngine().run(
        config=_config(
            daily,
            end_index=200,
            initial_capital=Decimal("100"),
            fee_bps=Decimal("5"),
            margin_buffer_percent=Decimal("0"),
        ),
        daily_candles=daily,
        hourly_candles=hourly,
        marks=marks,
    )

    assert result.executions == 1
    trade = result.trades[0]
    assert trade.quantity == Decimal("0.999500")
    entry_fee = trade.entry_price * trade.quantity * Decimal("5") / Decimal("10000")
    exit_fee = trade.exit_price * trade.quantity * Decimal("5") / Decimal("10000")
    assert trade.entry_price * trade.quantity + entry_fee <= Decimal("100")
    assert trade.gross_pnl == 0
    assert trade.net_pnl == -(entry_fee + exit_fee)
    assert result.net_pnl == trade.net_pnl
