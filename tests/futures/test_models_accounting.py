from dataclasses import replace
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import (
    ContractType,
    MarginMode,
    MarketType,
    PositionSide,
    TradingMode,
)
from adaptive_trader.futures.accounting import (
    approximate_liquidation_price,
    funding_cash_flow,
    initial_margin,
    maintenance_margin,
    position_notional,
    unrealized_pnl,
)
from adaptive_trader.futures.models import FuturesBacktestConfig, FuturesPosition


def test_market_enums_are_explicit() -> None:
    assert MarketType.SPOT.value == "SPOT"
    assert MarketType.USD_M_FUTURES.value == "USD_M_FUTURES"
    assert ContractType.PERPETUAL.value == "PERPETUAL"
    assert MarginMode.ISOLATED.value == "ISOLATED"
    assert TradingMode.FUTURES_LONG_SHORT.value == "FUTURES_LONG_SHORT"


def test_decimal_long_short_pnl_margin_and_funding() -> None:
    assert unrealized_pnl(
        PositionSide.LONG, Decimal("100"), Decimal("110"), Decimal("2")
    ) == Decimal("20")
    assert unrealized_pnl(
        PositionSide.SHORT, Decimal("100"), Decimal("90"), Decimal("2")
    ) == Decimal("20")
    notional = position_notional(Decimal("100"), Decimal("2"))
    assert notional == Decimal("200")
    assert initial_margin(notional, Decimal("2")) == Decimal("100")
    assert maintenance_margin(notional, Decimal("0.005")) == Decimal("1.000")
    assert funding_cash_flow(
        PositionSide.LONG, notional, Decimal("0.0001")
    ) == Decimal("-0.0200")
    assert funding_cash_flow(
        PositionSide.SHORT, notional, Decimal("0.0001")
    ) == Decimal("0.0200")
    assert funding_cash_flow(
        PositionSide.LONG, notional, Decimal("-0.0001")
    ) == Decimal("0.0200")


def test_liquidation_prices_are_side_specific() -> None:
    long_price = approximate_liquidation_price(
        PositionSide.LONG,
        Decimal("100"),
        Decimal("3"),
        Decimal("0.005"),
    )
    short_price = approximate_liquidation_price(
        PositionSide.SHORT,
        Decimal("100"),
        Decimal("3"),
        Decimal("0.005"),
    )
    assert long_price < Decimal("100") < short_price


def test_futures_config_rejects_cross_margin_and_leverage_above_three() -> None:
    with pytest.raises(ValueError, match="ISOLATED"):
        FuturesBacktestConfig(margin_mode=MarginMode.NONE)
    with pytest.raises(ValueError, match="maximum"):
        FuturesBacktestConfig(leverage=Decimal("4"))
    with pytest.raises(TypeError, match="Decimal"):
        FuturesBacktestConfig(leverage="2")


def test_futures_position_is_separate_model(futures_config, start_time) -> None:
    position = FuturesPosition(
        position_id="p1",
        symbol="ETHUSDT",
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        mark_price=Decimal("101"),
        notional=Decimal("101"),
        leverage=Decimal("1"),
        isolated_margin=Decimal("100"),
        free_balance_after_entry=Decimal("9900"),
        maintenance_margin=Decimal("0.505"),
        liquidation_price=Decimal("0.5"),
        unrealized_pnl=Decimal("1"),
        realized_pnl=Decimal("0"),
        accumulated_funding=Decimal("0"),
        entry_fee=Decimal("0.05"),
        opened_at=start_time,
        stop_loss=Decimal("90"),
        take_profit=Decimal("120"),
        initial_risk=Decimal("10"),
    )
    assert position.side is PositionSide.LONG
    assert replace(futures_config, leverage=Decimal("3")).leverage == Decimal("3")
