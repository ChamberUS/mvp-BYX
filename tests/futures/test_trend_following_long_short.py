from decimal import Decimal

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.research.trend_following_engine import (
    TrendFollowingEngine,
    TrendFollowingEngineConfig,
)
from tests.trend_following_engine_helpers import (
    HourSpec,
    PriceSpec,
    daily_series,
    futures_hours,
)


def test_long_short_mode_executes_mirrored_signals_without_overlapping_positions() -> None:
    daily = daily_series(
        {
            199: PriceSpec(Decimal("110"), Decimal("111"), Decimal("99")),
            200: PriceSpec(Decimal("110"), Decimal("111"), Decimal("99")),
            201: PriceSpec(Decimal("90"), Decimal("110"), Decimal("89")),
            202: PriceSpec(Decimal("80"), Decimal("91"), Decimal("79")),
            203: PriceSpec(Decimal("80"), Decimal("91"), Decimal("79")),
            204: PriceSpec(Decimal("120"), Decimal("121"), Decimal("79")),
            205: PriceSpec(Decimal("120"), Decimal("121"), Decimal("119")),
        },
        total_days=206,
    )
    hourly, marks = futures_hours(
        {
            199: HourSpec(Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
            200: HourSpec(Decimal("105"), Decimal("106"), Decimal("104"), Decimal("105")),
            201: HourSpec(Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
            202: HourSpec(Decimal("90"), Decimal("91"), Decimal("89"), Decimal("90")),
            203: HourSpec(Decimal("80"), Decimal("81"), Decimal("79"), Decimal("80")),
            204: HourSpec(Decimal("80"), Decimal("81"), Decimal("79"), Decimal("80")),
            205: HourSpec(Decimal("120"), Decimal("121"), Decimal("119"), Decimal("120")),
        }
    )
    result = TrendFollowingEngine().run(
        config=TrendFollowingEngineConfig(
            market="futures",
            mode="long-short",
            variant_id="TEST",
            period="DEVELOPMENT",
            scenario="BASE",
            evaluation_start=daily[199].open_time,
            evaluation_end=daily[205].close_time or daily[205].open_time,
            exit_period=20,
            defensive_risk=False,
            maximum_position_percent=Decimal("25"),
            fee_bps=Decimal("0"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        ),
        daily_candles=daily,
        hourly_candles=hourly,
        marks=marks,
    )

    assert len(result.trades) == 2
    long_trade, short_trade = result.trades
    assert long_trade.side == PositionSide.LONG.value
    assert short_trade.side == PositionSide.SHORT.value
    assert long_trade.exit_time <= short_trade.entry_time
    assert long_trade.entry_time == hourly[1].open_time
    assert long_trade.exit_time == hourly[3].open_time
    assert short_trade.entry_time == hourly[4].open_time
    assert short_trade.exit_time == hourly[6].open_time
    assert result.long_trades == 1
    assert result.short_trades == 1
