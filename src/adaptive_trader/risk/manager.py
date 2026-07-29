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
    def evaluate(
        self,
        signal: MarketSignal,
        portfolio: PortfolioSnapshot,
        limits: TradingConfig,
    ) -> RiskDecision:
        def reject(reason: str) -> RiskDecision:
            return RiskDecision(
                decision_id=f"{signal.signal_id}-RISK",
                signal_id=signal.signal_id,
                decided_at=signal.generated_at,
                approved=False,
                reason=reason,
                order_intent=None,
            )

        if not limits.trading_enabled:
            return reject("trading_enabled is false")
        if limits.allow_leverage:
            return reject("leverage is forbidden")
        if limits.allow_margin:
            return reject("margin is forbidden")
        if limits.allow_futures or limits.market != "SPOT":
            return reject("futures and non-spot markets are forbidden")
        if limits.allow_average_down:
            return reject("average down is forbidden")
        if signal.direction is SignalDirection.HOLD:
            return reject("signal is not actionable")
        if signal.direction is SignalDirection.SELL:
            return reject("short selling is not supported by spot-only execution")
        if len(portfolio.positions) >= limits.maximum_open_positions:
            return reject("maximum open positions reached")
        if portfolio.trades_today >= limits.maximum_trades_per_day:
            return reject("maximum daily trades reached")
        max_daily_loss = portfolio.equity * limits.maximum_daily_loss_percent / Decimal("100")
        if portfolio.daily_loss >= max_daily_loss:
            return reject("maximum daily loss reached")
        max_position_value = portfolio.equity * limits.maximum_position_percent / Decimal("100")
        requested_value = signal.suggested_quantity * signal.entry_price
        if requested_value > max_position_value:
            return reject("requested position exceeds configured limit")
        if requested_value > portfolio.cash_balance:
            return reject("requested position exceeds available cash")
        downside = signal.entry_price - signal.stop_loss
        upside = signal.take_profit - signal.entry_price
        if downside <= 0 or upside / downside < limits.minimum_risk_reward:
            return reject("risk/reward is below configured minimum")
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
        )
