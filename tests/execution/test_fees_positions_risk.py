from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.execution import (
    ExecutionConfig,
    ExecutionSimulator,
    FeeConfig,
    FeeModel,
    GovernorState,
    LiquidityRole,
    MarketFeeRates,
    OrderSide,
    OrderType,
    PortfolioRiskGovernor,
    PositionEffect,
    RiskPreset,
    RiskReason,
    research_risk_preset,
)
from tests.execution.helpers import BASE, at, book


def test_fee_model_separates_spot_futures_maker_taker_and_applies_per_fill() -> None:
    config = FeeConfig(
        spot=MarketFeeRates(Decimal("0.001"), Decimal("0.002")),
        futures=MarketFeeRates(Decimal("0.0001"), Decimal("0.0004")),
    )
    model = FeeModel(config)
    assert model.calculate(
        MarketType.SPOT, LiquidityRole.MAKER, Decimal("100"), Decimal("2")
    ) == Decimal("0.200")
    assert model.calculate(
        MarketType.SPOT, LiquidityRole.TAKER, Decimal("100"), Decimal("2")
    ) == Decimal("0.400")
    assert model.calculate(
        MarketType.USD_M_FUTURES, LiquidityRole.MAKER, Decimal("100"), Decimal("2")
    ) == Decimal("0.0200")
    assert model.calculate(
        MarketType.USD_M_FUTURES, LiquidityRole.TAKER, Decimal("100"), Decimal("2")
    ) == Decimal("0.0800")
    with pytest.raises(ValueError, match="positive"):
        model.calculate(MarketType.SPOT, LiquidityRole.MAKER, Decimal("0"), Decimal("1"))
    with pytest.raises(ValueError, match="non-negative"):
        MarketFeeRates(Decimal("-0.1"), Decimal("0"))


def test_spot_position_average_partial_close_realized_fees_and_cash() -> None:
    venue = ExecutionSimulator()
    open_result = venue.submit(
        client_intent_id="long",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.OPEN_LONG,
        order_type=OrderType.MARKET,
        quantity=Decimal("3"),
        decision_time=BASE,
        books=(book(),),
        reference_price=Decimal("100.10"),
        maximum_slippage_bps=Decimal("30"),
    )
    opened = venue.position_ledger.snapshot(
        MarketType.SPOT,
        "ETHUSDT",
        at(40),
        mark_price=Decimal("101"),
        book=book(40, bids=(("100.50", "3"),), asks=(("100.60", "3"),)),
    )
    assert opened.side is PositionSide.LONG
    assert opened.quantity == Decimal("3")
    assert opened.average_entry == open_result.order.vwap
    assert opened.unrealized_mark_pnl > opened.unrealized_executable_pnl > 0
    assert opened.fees == open_result.order.total_fee

    venue.submit(
        client_intent_id="close-part",
        market=MarketType.SPOT,
        symbol="ETHUSDT",
        side=OrderSide.SELL,
        position_effect=PositionEffect.CLOSE_LONG,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        decision_time=at(50),
        books=(book(80, bids=(("100.50", "3"),), asks=(("100.60", "3"),)),),
        reference_price=Decimal("100.50"),
        maximum_slippage_bps=Decimal("10"),
    )
    reduced = venue.position_ledger.snapshot(MarketType.SPOT, "ETHUSDT", at(90))
    assert reduced.quantity == Decimal("2")
    assert reduced.realized_pnl > 0
    assert venue.position_ledger.cash < venue.position_ledger.initial_cash


def test_futures_short_close_mark_executable_and_funding_hook() -> None:
    venue = ExecutionSimulator()
    futures_book = book(30, market=MarketType.USD_M_FUTURES)
    venue.submit(
        client_intent_id="short",
        market=MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        side=OrderSide.SELL,
        position_effect=PositionEffect.OPEN_SHORT,
        order_type=OrderType.MARKET,
        quantity=Decimal("2"),
        decision_time=BASE,
        books=(futures_book,),
        reference_price=Decimal("100"),
        maximum_slippage_bps=Decimal("20"),
    )
    venue.position_ledger.apply_funding(
        MarketType.USD_M_FUTURES,
        "ETHUSDT",
        Decimal("-0.05"),
    )
    favorable = book(
        60,
        market=MarketType.USD_M_FUTURES,
        bids=(("98.90", "3"),),
        asks=(("99.00", "3"),),
    )
    short = venue.position_ledger.snapshot(
        MarketType.USD_M_FUTURES,
        "ETHUSDT",
        at(60),
        mark_price=Decimal("98.50"),
        book=favorable,
    )
    assert short.side is PositionSide.SHORT
    assert short.unrealized_mark_pnl > short.unrealized_executable_pnl > 0
    assert short.funding == Decimal("-0.05")
    venue.submit(
        client_intent_id="cover",
        market=MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        position_effect=PositionEffect.CLOSE_SHORT,
        order_type=OrderType.MARKET,
        quantity=Decimal("2"),
        decision_time=at(40),
        books=(favorable,),
        reference_price=Decimal("99"),
        maximum_slippage_bps=Decimal("20"),
    )
    closed = venue.position_ledger.snapshot(MarketType.USD_M_FUTURES, "ETHUSDT", at(70))
    assert closed.side is None
    assert closed.quantity == 0
    assert closed.realized_pnl > 0


def test_position_ledger_rejects_negative_cash_spot_short_and_invalid_funding() -> None:
    venue = ExecutionSimulator()
    venue.position_ledger.cash = Decimal("1")
    with pytest.raises(ValueError, match="cash"):
        venue.submit(
            client_intent_id="too-large",
            market=MarketType.SPOT,
            symbol="ETHUSDT",
            side=OrderSide.BUY,
            position_effect=PositionEffect.OPEN_LONG,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            decision_time=BASE,
            books=(book(),),
            reference_price=Decimal("100"),
            maximum_slippage_bps=Decimal("20"),
        )
    with pytest.raises(ValueError, match="funding"):
        venue.position_ledger.apply_funding(MarketType.SPOT, "ETHUSDT", Decimal("1"))


@pytest.mark.parametrize("preset", list(RiskPreset))
def test_intraday_risk_presets_are_small_research_only_and_one_x(preset: RiskPreset) -> None:
    config = research_risk_preset(preset)
    assert config.risk_per_trade_percent <= Decimal("0.10")
    assert config.leverage == Decimal("1")


def test_governor_liquidity_cap_loss_streak_cooldown_and_reset() -> None:
    config = research_risk_preset(RiskPreset.LOW)
    governor = PortfolioRiskGovernor(config)
    assert governor.state is GovernorState.ACTIVE
    assert governor.approve_liquidity(Decimal("1"), Decimal("20"))
    assert not governor.approve_liquidity(Decimal("3"), Decimal("20"))
    governor.record_trade(Decimal("-0.01"), BASE)
    assert governor.state is GovernorState.REDUCED
    governor.record_trade(Decimal("-0.01"), at(1))
    governor.record_trade(Decimal("-0.01"), at(2))
    assert governor.state is GovernorState.COOLDOWN
    assert not governor.approve_liquidity(Decimal("1"), Decimal("20"))
    governor.reset_boundary(at(config.cooldown_ms + 3))
    assert governor.state is GovernorState.REDUCED
    governor.reset_boundary(BASE + timedelta(days=1))
    assert governor.state is GovernorState.ACTIVE


def test_governor_daily_data_slippage_and_critical_kill_states() -> None:
    base = research_risk_preset(RiskPreset.LOW)
    config = replace(base, maximum_daily_loss_percent=Decimal("0.02"))
    daily = PortfolioRiskGovernor(config)
    daily.record_trade(Decimal("-0.03"), BASE)
    assert daily.state is GovernorState.DAILY_KILLED
    assert daily.events[-1].reason is RiskReason.DAILY_LOSS_LIMIT

    data = PortfolioRiskGovernor(base)
    assert data.observe_slippage(Decimal("11"), BASE) is GovernorState.REDUCED
    for offset in range(3):
        data.observe_data_gap(at(offset), desync=True)
    assert data.state is GovernorState.DATA_KILLED
    assert data.events[-1].reason is RiskReason.REPEATED_BOOK_DESYNC

    killed = PortfolioRiskGovernor(base)
    killed.kill(RiskReason.ACCOUNTING_MISMATCH, BASE)
    assert killed.state is GovernorState.DATA_KILLED
    assert killed.events[-1].critical


def test_leverage_above_one_is_rejected_by_config_and_governor() -> None:
    base = research_risk_preset(RiskPreset.VERY_LOW)
    with pytest.raises(ValueError, match="locked"):
        replace(base, leverage=Decimal("2"))
    with pytest.raises(ValueError, match="leverage"):
        ExecutionConfig(leverage=Decimal("2"))
