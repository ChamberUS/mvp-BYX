from decimal import Decimal

from adaptive_trader.research.trend_following_engine import (
    TrendFollowingEngine,
    TrendFollowingEngineConfig,
    TrendFollowingExitReason,
)
from tests.trend_following_engine_helpers import (
    HourSpec,
    PriceSpec,
    daily_series,
    spot_hours,
)


def test_spot_macro_and_donchian_exit_is_confirmed_then_filled_next_open() -> None:
    daily = daily_series(
        {
            199: PriceSpec(Decimal("110"), Decimal("111"), Decimal("99")),
            200: PriceSpec(Decimal("110"), Decimal("111"), Decimal("99")),
            201: PriceSpec(Decimal("90"), Decimal("110"), Decimal("89")),
            202: PriceSpec(Decimal("90"), Decimal("91"), Decimal("89")),
        },
        total_days=203,
    )
    hourly = spot_hours(
        {
            199: HourSpec(Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
            200: HourSpec(Decimal("105"), Decimal("106"), Decimal("90"), Decimal("105")),
            201: HourSpec(Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
            202: HourSpec(Decimal("95"), Decimal("96"), Decimal("94"), Decimal("95")),
        }
    )
    result = TrendFollowingEngine().run(
        config=TrendFollowingEngineConfig(
            market="spot",
            mode="long",
            variant_id="TEST",
            period="DEVELOPMENT",
            scenario="BASE",
            evaluation_start=daily[199].open_time,
            evaluation_end=daily[202].close_time or daily[202].open_time,
            exit_period=10,
            defensive_risk=False,
            fee_bps=Decimal("0"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        ),
        daily_candles=daily,
        hourly_candles=hourly,
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_time == hourly[1].open_time
    assert trade.exit_time == hourly[3].open_time
    assert trade.exit_reason == TrendFollowingExitReason.MACRO_FILTER_EXIT.value
    assert trade.macro_exit_true is True
    assert trade.donchian_exit_true is True
    entry_trace = next(trace for trace in result.traces if trace.date == daily[199].open_time)
    assert entry_trace.quantity == trade.quantity
    assert entry_trace.risk_budget == trade.risk_budget
    assert entry_trace.exit_channel == entry_trace.initial_stop
    assert entry_trace.risk_per_unit == abs(trade.entry_price - trade.initial_stop)
    exit_trace = next(trace for trace in result.traces if trace.date == daily[201].open_time)
    assert exit_trace.reason_code == TrendFollowingExitReason.MACRO_FILTER_EXIT.value
    assert exit_trace.macro_exit_true is True
    assert exit_trace.donchian_exit_true is True
