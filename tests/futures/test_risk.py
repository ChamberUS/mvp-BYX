from dataclasses import replace
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import TradingMode
from adaptive_trader.domain.models import MarketRegime
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FuturesPortfolioState,
    FuturesRiskReasonCode,
    FuturesSignal,
    FuturesSignalDirection,
)
from adaptive_trader.futures.risk import FuturesRiskManager


def signal(start_time, direction=FuturesSignalDirection.ENTER_LONG) -> FuturesSignal:
    short = direction is FuturesSignalDirection.ENTER_SHORT
    return FuturesSignal(
        signal_id="signal-1",
        symbol="ETHUSDT",
        generated_at=start_time,
        direction=direction,
        regime=MarketRegime.TRENDING_DOWN if short else MarketRegime.TRENDING_UP,
        entry_price=Decimal("100"),
        stop_loss=Decimal("110") if short else Decimal("90"),
        take_profit=Decimal("80") if short else Decimal("120"),
        rationale="fixture",
        reason_code="FIXTURE",
    )


def portfolio(**changes: object) -> FuturesPortfolioState:
    values: dict[str, object] = {
        "wallet_balance": Decimal("10000"),
        "day_start_equity": Decimal("10000"),
        "entries_today": 0,
        "daily_loss": Decimal("0"),
        "position_open": False,
    }
    values.update(changes)
    return FuturesPortfolioState(**values)


def evaluate(futures_config, start_time, **kwargs):
    return FuturesRiskManager().evaluate(
        kwargs.pop("signal", signal(start_time)),
        kwargs.pop("portfolio", portfolio()),
        kwargs.pop("config", futures_config),
        execution_price=Decimal("100"),
        decided_at=start_time,
        **kwargs,
    )


def test_risk_approves_decimal_isolated_intent(futures_config, start_time) -> None:
    decision = evaluate(futures_config, start_time)
    assert decision.approved
    assert decision.reason_code is FuturesRiskReasonCode.APPROVED
    assert decision.intent is not None
    assert decision.intent.quantity == Decimal("10.00000000")


@pytest.mark.parametrize(
    ("state", "code"),
    [
        (portfolio(position_open=True), FuturesRiskReasonCode.POSITION_ALREADY_OPEN),
        (
            portfolio(daily_loss=Decimal("100")),
            FuturesRiskReasonCode.DAILY_LOSS_LIMIT,
        ),
        (
            portfolio(entries_today=5),
            FuturesRiskReasonCode.DAILY_LOSS_LIMIT,
        ),
        (
            portfolio(wallet_balance=Decimal("0.5")),
            FuturesRiskReasonCode.MINIMUM_BALANCE,
        ),
        (
            portfolio(
                liquidated_today=True,
                candles_since_liquidation=3,
            ),
            FuturesRiskReasonCode.POST_LIQUIDATION_COOLDOWN,
        ),
        (
            portfolio(
                liquidated_today=False,
                candles_since_liquidation=3,
            ),
            FuturesRiskReasonCode.POST_LIQUIDATION_COOLDOWN,
        ),
        (
            portfolio(kill_state=True),
            FuturesRiskReasonCode.KILL_STATE,
        ),
    ],
)
def test_risk_rejects_portfolio_limits(futures_config, start_time, state, code) -> None:
    decision = evaluate(futures_config, start_time, portfolio=state)
    assert not decision.approved
    assert decision.reason_code is code


def test_risk_rejects_stop_short_mode_and_funding(futures_config, start_time) -> None:
    no_stop = replace(signal(start_time), stop_loss=None)
    assert (
        evaluate(futures_config, start_time, signal=no_stop).reason_code
        is FuturesRiskReasonCode.STOP_REQUIRED
    )
    long_only = replace(futures_config, trading_mode=TradingMode.FUTURES_LONG_ONLY)
    short = signal(start_time, FuturesSignalDirection.ENTER_SHORT)
    assert (
        evaluate(futures_config, start_time, signal=short, config=long_only).reason_code
        is FuturesRiskReasonCode.SHORT_NOT_ALLOWED
    )
    assert (
        evaluate(futures_config, start_time, funding_available=False).reason_code
        is FuturesRiskReasonCode.APPROVED
    )
    funding_config = replace(
        futures_config,
        funding_enabled=True,
        funding_missing_policy=FundingMissingPolicy.FAIL,
    )
    assert (
        evaluate(
            funding_config,
            start_time,
            funding_available=False,
        ).reason_code
        is FuturesRiskReasonCode.FUNDING_DATA_MISSING
    )


def test_risk_rejects_notional_margin_and_maintenance(futures_config, start_time) -> None:
    excessive = evaluate(
        futures_config,
        start_time,
        requested_quantity=Decimal("26"),
    )
    assert excessive.reason_code is FuturesRiskReasonCode.NOTIONAL_LIMIT
    full_wallet = replace(
        futures_config,
        maximum_position_notional_percent=Decimal("100"),
    )
    insufficient = evaluate(
        futures_config,
        start_time,
        config=full_wallet,
        requested_quantity=Decimal("100"),
    )
    assert insufficient.reason_code is FuturesRiskReasonCode.MARGIN_INSUFFICIENT
    unsafe = replace(
        futures_config,
        leverage=Decimal("2"),
        maintenance_margin_rate=Decimal("0.9"),
    )
    assert (
        evaluate(unsafe, start_time).reason_code
        is FuturesRiskReasonCode.MAINTENANCE_MARGIN_UNSAFE
    )
