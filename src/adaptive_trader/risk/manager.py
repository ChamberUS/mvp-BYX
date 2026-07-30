"""Hard risk gates for the spot-only research system."""

from __future__ import annotations

from decimal import Decimal

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import (
    MarketSignal,
    OrderIntent,
    PortfolioSnapshot,
    RiskDecision,
    SignalDirection,
)


class DefaultRiskManager:
    def __init__(self, *, local_simulation: bool = False) -> None:
        self._local_simulation = local_simulation

    def evaluate(
        self,
        signal: MarketSignal,
        portfolio: PortfolioSnapshot,
        limits: TradingConfig,
    ) -> RiskDecision:
        def reject(reason: str, reason_code: str) -> RiskDecision:
            return RiskDecision(
                decision_id=f"{signal.signal_id}-RISK",
                signal_id=signal.signal_id,
                decided_at=signal.generated_at,
                approved=False,
                reason=reason,
                order_intent=None,
                reason_code=reason_code,
            )

        if not limits.trading_enabled and not self._local_simulation:
            return reject("trading_enabled is false", "TRADING_DISABLED")
        if limits.allow_leverage:
            return reject("leverage is forbidden", "LEVERAGE_FORBIDDEN")
        if limits.allow_margin:
            return reject("margin is forbidden", "MARGIN_FORBIDDEN")
        if limits.allow_futures or limits.market != "SPOT":
            return reject("futures and non-spot markets are forbidden", "FUTURES_FORBIDDEN")
        if limits.allow_average_down:
            return reject("average down is forbidden", "AVERAGE_DOWN_FORBIDDEN")
        if signal.direction is SignalDirection.HOLD:
            return reject("signal is not actionable", "SIGNAL_HOLD")
        if signal.direction is SignalDirection.SELL and not limits.allow_short_selling:
            position = next(
                (item for item in portfolio.positions if item.symbol == signal.symbol), None
            )
            if position is None:
                return reject("spot sell requires an existing position", "NO_POSITION_FOR_SELL")
            if signal.suggested_quantity > position.quantity:
                return reject("sell quantity exceeds existing position", "POSITION_SIZE_LIMIT")
        if (
            signal.direction is SignalDirection.BUY
            and len(portfolio.positions) >= limits.maximum_open_positions
        ):
            return reject("maximum open positions reached", "POSITION_ALREADY_OPEN")
        if signal.direction is SignalDirection.BUY:
            if portfolio.entries_today >= limits.maximum_trades_per_day:
                return reject("maximum daily entries reached", "DAILY_ENTRY_LIMIT")
            max_daily_loss = (
                portfolio.day_start_equity
                * limits.maximum_daily_loss_percent
                / Decimal("100")
            )
            if portfolio.daily_loss >= max_daily_loss:
                return reject("maximum daily loss reached", "DAILY_LOSS_LIMIT")
        requested_value = signal.suggested_quantity * signal.entry_price
        if signal.direction is SignalDirection.BUY:
            max_position_value = portfolio.equity * limits.maximum_position_percent / Decimal("100")
            if requested_value > max_position_value:
                return reject("requested position exceeds configured limit", "POSITION_SIZE_LIMIT")
            if requested_value > portfolio.cash_balance:
                return reject("requested position exceeds available cash", "INSUFFICIENT_CASH")
        downside = signal.entry_price - signal.stop_loss
        upside = signal.take_profit - signal.entry_price
        if signal.direction is SignalDirection.BUY and (
            downside <= 0 or upside / downside < limits.minimum_risk_reward
        ):
            return reject("risk/reward is below configured minimum", "RISK_REWARD_TOO_LOW")
        intent = OrderIntent(
            intent_id=f"{signal.signal_id}-INTENT",
            symbol=signal.symbol,
            direction=signal.direction,
            quantity=signal.suggested_quantity,
            price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            created_at=signal.generated_at,
        )
        return RiskDecision(
            decision_id=f"{signal.signal_id}-RISK",
            signal_id=signal.signal_id,
            decided_at=signal.generated_at,
            approved=True,
            reason="signal passed all configured risk gates",
            order_intent=intent,
            reason_code=(
                "BUY_APPROVED" if signal.direction is SignalDirection.BUY else "EXIT_APPROVED"
            ),
        )
