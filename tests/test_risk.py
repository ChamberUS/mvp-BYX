from dataclasses import replace
from decimal import Decimal

import pytest

from adaptive_trader.config.settings import ConfigError, TradingConfig
from adaptive_trader.domain.models import Position, SignalDirection
from adaptive_trader.risk.manager import DefaultRiskManager


def enabled_config() -> TradingConfig:
    return TradingConfig(trading_enabled=True)


def test_trading_is_blocked_when_disabled(buy_signal, empty_portfolio) -> None:
    decision = DefaultRiskManager().evaluate(buy_signal, empty_portfolio, TradingConfig())

    assert decision.approved is False
    assert "trading_enabled" in decision.reason


def test_position_above_limit_is_rejected(buy_signal, empty_portfolio, analysis_time) -> None:
    position = Position(
        position_id="position-1",
        symbol="ETHUSDT",
        quantity=Decimal("0.1"),
        average_entry_price=Decimal("2000"),
        current_price=Decimal("2000"),
        opened_at=analysis_time,
    )
    portfolio = replace(empty_portfolio, positions=(position,))

    decision = DefaultRiskManager().evaluate(buy_signal, portfolio, enabled_config())

    assert decision.approved is False
    assert "maximum open positions" in decision.reason


def test_position_value_above_percent_limit_is_rejected(buy_signal, empty_portfolio) -> None:
    oversized = replace(buy_signal, suggested_quantity=Decimal("0.3"))

    decision = DefaultRiskManager().evaluate(oversized, empty_portfolio, enabled_config())

    assert decision.approved is False
    assert "position exceeds" in decision.reason


def test_approved_risk_decision_contains_intent(buy_signal, empty_portfolio) -> None:
    decision = DefaultRiskManager().evaluate(buy_signal, empty_portfolio, enabled_config())

    assert decision.approved is True
    assert decision.order_intent is not None
    assert decision.order_intent.quantity == Decimal("0.2")


def test_futures_and_leverage_configuration_are_rejected() -> None:
    with pytest.raises(ConfigError):
        TradingConfig(allow_futures=True)
    with pytest.raises(ConfigError):
        TradingConfig(allow_leverage=True)


def test_daily_entry_limit_is_buy_only(buy_signal, empty_portfolio) -> None:
    limited = replace(empty_portfolio, entries_today=5)

    decision = DefaultRiskManager().evaluate(buy_signal, limited, enabled_config())

    assert decision.approved is False
    assert "daily entries" in decision.reason


def test_daily_loss_uses_day_start_equity(buy_signal, empty_portfolio) -> None:
    losing = replace(
        empty_portfolio,
        equity=Decimal("9900"),
        cash_balance=Decimal("9900"),
        day_start_equity=Decimal("10000"),
        daily_loss=Decimal("100"),
    )

    decision = DefaultRiskManager().evaluate(buy_signal, losing, enabled_config())

    assert decision.approved is False
    assert "daily loss" in decision.reason


def test_new_day_allows_entries_after_counters_reset(buy_signal, empty_portfolio) -> None:
    new_day = replace(
        empty_portfolio,
        day_start_equity=Decimal("11000"),
        equity=Decimal("10900"),
        cash_balance=Decimal("10900"),
        daily_loss=Decimal("100"),
        entries_today=0,
    )

    decision = DefaultRiskManager().evaluate(buy_signal, new_day, enabled_config())

    assert decision.approved is True


def test_sell_exit_is_allowed_after_daily_entry_and_loss_limits(
    buy_signal, empty_portfolio, analysis_time
) -> None:
    position = Position(
        position_id="position-1",
        symbol="ETHUSDT",
        quantity=Decimal("0.2"),
        average_entry_price=Decimal("2000"),
        current_price=Decimal("2000"),
        opened_at=analysis_time,
    )
    sell = replace(buy_signal, direction=SignalDirection.SELL)
    limited = replace(
        empty_portfolio,
        equity=Decimal("9900"),
        cash_balance=Decimal("9500"),
        day_start_equity=Decimal("10000"),
        daily_loss=Decimal("100"),
        entries_today=5,
        positions=(position,),
    )

    decision = DefaultRiskManager().evaluate(sell, limited, enabled_config())

    assert decision.approved is True
