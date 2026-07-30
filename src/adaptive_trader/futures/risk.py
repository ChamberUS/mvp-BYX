"""Independent risk gate for isolated USD-M Futures simulations."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.futures.accounting import initial_margin, maintenance_margin
from adaptive_trader.futures.models import (
    FuturesBacktestConfig,
    FuturesOrderIntent,
    FuturesPortfolioState,
    FuturesRiskDecision,
    FuturesRiskReasonCode,
    FuturesSignal,
    FuturesSignalDirection,
)


class FuturesRiskManager:
    def evaluate(
        self,
        signal: FuturesSignal,
        portfolio: FuturesPortfolioState,
        limits: FuturesBacktestConfig,
        *,
        execution_price: Decimal,
        requested_quantity: Decimal | None = None,
        funding_available: bool = True,
        decided_at: datetime,
    ) -> FuturesRiskDecision:
        if limits.leverage > limits.maximum_leverage or limits.maximum_leverage > Decimal("3"):
            return self._reject(FuturesRiskReasonCode.LEVERAGE_LIMIT)
        if portfolio.kill_state:
            return self._reject(FuturesRiskReasonCode.KILL_STATE)
        side = self._side(signal.direction)
        if side is None:
            return self._reject(FuturesRiskReasonCode.STOP_REQUIRED, "signal is not an entry")
        if side is PositionSide.SHORT and limits.trading_mode is TradingMode.FUTURES_LONG_ONLY:
            return self._reject(FuturesRiskReasonCode.SHORT_NOT_ALLOWED)
        if side is PositionSide.LONG and limits.trading_mode is TradingMode.FUTURES_SHORT_ONLY:
            return self._reject(FuturesRiskReasonCode.LONG_NOT_ALLOWED)
        if portfolio.position_open:
            return self._reject(FuturesRiskReasonCode.POSITION_ALREADY_OPEN)
        if signal.stop_loss is None or signal.take_profit is None:
            return self._reject(FuturesRiskReasonCode.STOP_REQUIRED)
        if not self._valid_stop(side, execution_price, signal.stop_loss, signal.take_profit):
            return self._reject(FuturesRiskReasonCode.STOP_REQUIRED, "invalid protective prices")
        if limits.funding_enabled and not funding_available:
            return self._reject(FuturesRiskReasonCode.FUNDING_DATA_MISSING)
        if portfolio.wallet_balance < limits.minimum_wallet_balance:
            return self._reject(FuturesRiskReasonCode.MINIMUM_BALANCE)
        if (
            portfolio.day_start_equity > 0
            and portfolio.daily_loss / portfolio.day_start_equity * Decimal("100")
            >= limits.maximum_daily_loss_percent
        ):
            return self._reject(FuturesRiskReasonCode.DAILY_LOSS_LIMIT)
        if portfolio.entries_today >= limits.maximum_entries_per_day:
            return self._reject(FuturesRiskReasonCode.DAILY_LOSS_LIMIT, "daily entry limit")
        cooldown = portfolio.candles_since_liquidation
        if portfolio.liquidated_today or (
            cooldown is not None and cooldown < limits.post_liquidation_cooldown_candles
        ):
            return self._reject(FuturesRiskReasonCode.POST_LIQUIDATION_COOLDOWN)
        stop_distance = abs(execution_price - signal.stop_loss)
        risk_budget = portfolio.wallet_balance * limits.risk_per_trade_percent / Decimal("100")
        risk_quantity = risk_budget / stop_distance
        notional_cap = (
            portfolio.wallet_balance
            * limits.maximum_position_notional_percent
            / Decimal("100")
            * limits.leverage
        )
        cap_quantity = notional_cap / execution_price
        quantity = requested_quantity if requested_quantity is not None else min(
            risk_quantity, cap_quantity
        )
        quantity = quantity.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        if quantity <= 0:
            return self._reject(FuturesRiskReasonCode.NOTIONAL_LIMIT)
        notional = execution_price * quantity
        if notional > notional_cap:
            return self._reject(FuturesRiskReasonCode.NOTIONAL_LIMIT)
        entry_fee = notional * limits.taker_fee_bps / Decimal("10000")
        required_margin = initial_margin(notional, limits.leverage)
        buffer = required_margin * limits.margin_buffer_percent / Decimal("100")
        if required_margin + entry_fee + buffer > portfolio.wallet_balance:
            return self._reject(FuturesRiskReasonCode.MARGIN_INSUFFICIENT)
        if maintenance_margin(notional, limits.maintenance_margin_rate) >= required_margin:
            return self._reject(FuturesRiskReasonCode.MAINTENANCE_MARGIN_UNSAFE)
        return FuturesRiskDecision(
            approved=True,
            reason_code=FuturesRiskReasonCode.APPROVED,
            reason="isolated margin and configured limits approved",
            intent=FuturesOrderIntent(
                intent_id=f"intent-{signal.signal_id}",
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                side=side,
                quantity=quantity,
                reference_price=execution_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                leverage=limits.leverage,
                created_at=decided_at,
            ),
        )

    @staticmethod
    def _side(direction: FuturesSignalDirection) -> PositionSide | None:
        if direction is FuturesSignalDirection.ENTER_LONG:
            return PositionSide.LONG
        if direction is FuturesSignalDirection.ENTER_SHORT:
            return PositionSide.SHORT
        return None

    @staticmethod
    def _valid_stop(
        side: PositionSide,
        entry: Decimal,
        stop: Decimal,
        target: Decimal,
    ) -> bool:
        if side is PositionSide.LONG:
            return stop < entry < target
        return target < entry < stop

    @staticmethod
    def _reject(
        code: FuturesRiskReasonCode,
        reason: str | None = None,
    ) -> FuturesRiskDecision:
        return FuturesRiskDecision(
            approved=False,
            reason_code=code,
            reason=reason or code.value,
            intent=None,
        )
