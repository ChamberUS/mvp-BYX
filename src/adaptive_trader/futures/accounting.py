"""Pure USD-M Futures accounting formulas."""

from decimal import Decimal

from adaptive_trader.domain.market import PositionSide


def unrealized_pnl(
    side: PositionSide,
    entry_price: Decimal,
    mark_price: Decimal,
    quantity: Decimal,
) -> Decimal:
    if side is PositionSide.LONG:
        return (mark_price - entry_price) * quantity
    return (entry_price - mark_price) * quantity


def position_notional(price: Decimal, quantity: Decimal) -> Decimal:
    return price * quantity


def initial_margin(notional: Decimal, leverage: Decimal) -> Decimal:
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    return notional / leverage


def maintenance_margin(notional: Decimal, maintenance_margin_rate: Decimal) -> Decimal:
    if maintenance_margin_rate < 0:
        raise ValueError("maintenance_margin_rate must not be negative")
    return notional * maintenance_margin_rate


def approximate_liquidation_price(
    side: PositionSide,
    entry_price: Decimal,
    leverage: Decimal,
    maintenance_margin_rate: Decimal,
) -> Decimal:
    if leverage < 1:
        raise ValueError("leverage must be at least 1")
    if side is PositionSide.LONG:
        return max(
            Decimal("0"),
            entry_price
            * (Decimal("1") - Decimal("1") / leverage + maintenance_margin_rate),
        )
    return entry_price * (
        Decimal("1") + Decimal("1") / leverage - maintenance_margin_rate
    )


def funding_cash_flow(
    side: PositionSide,
    notional_at_mark_price: Decimal,
    funding_rate: Decimal,
) -> Decimal:
    payment = notional_at_mark_price * funding_rate
    return -payment if side is PositionSide.LONG else payment
