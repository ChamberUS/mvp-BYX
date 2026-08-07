"""Mechanical intraday risk governor and research-only 1x presets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from adaptive_trader.microstructure.models import IntradayRiskConfig

ZERO = Decimal("0")


class RiskPreset(StrEnum):
    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    MODERATE = "MODERATE"


class GovernorState(StrEnum):
    ACTIVE = "ACTIVE"
    REDUCED = "REDUCED"
    COOLDOWN = "COOLDOWN"
    DAILY_KILLED = "DAILY_KILLED"
    DATA_KILLED = "DATA_KILLED"


class RiskReason(StrEnum):
    LIQUIDITY_CAP_EXCEEDED = "LIQUIDITY_CAP_EXCEEDED"
    LOSS_STREAK = "LOSS_STREAK"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    ABNORMAL_SLIPPAGE = "ABNORMAL_SLIPPAGE"
    REPEATED_DATA_GAP = "REPEATED_DATA_GAP"
    REPEATED_BOOK_DESYNC = "REPEATED_BOOK_DESYNC"
    LIQUIDITY_COLLAPSE = "LIQUIDITY_COLLAPSE"
    INVALID_ORDER_BOOK = "INVALID_ORDER_BOOK"
    IMPOSSIBLE_POSITION_STATE = "IMPOSSIBLE_POSITION_STATE"
    NEGATIVE_QUANTITY = "NEGATIVE_QUANTITY"
    ACCOUNTING_MISMATCH = "ACCOUNTING_MISMATCH"
    SEQUENCE_CORRUPTION = "SEQUENCE_CORRUPTION"
    EXECUTION_INVARIANT_FAILURE = "EXECUTION_INVARIANT_FAILURE"


@dataclass(frozen=True, slots=True)
class RiskGovernorEvent:
    timestamp: datetime
    previous_state: GovernorState
    state: GovernorState
    reason: RiskReason
    critical: bool


def research_risk_preset(preset: RiskPreset) -> IntradayRiskConfig:
    risk = {
        RiskPreset.VERY_LOW: Decimal("0.02"),
        RiskPreset.LOW: Decimal("0.05"),
        RiskPreset.MODERATE: Decimal("0.10"),
    }[preset]
    return IntradayRiskConfig(
        risk_per_trade_percent=risk,
        maximum_daily_loss_percent=risk * Decimal("5"),
        maximum_weekly_loss_percent=risk * Decimal("10"),
        maximum_consecutive_losses=3,
        cooldown_ms=60_000,
        maximum_open_positions=1,
        maximum_orders_per_minute=10,
        maximum_notional=Decimal("1000"),
        maximum_visible_depth_fraction=Decimal("0.10"),
        maximum_slippage_bps=Decimal("10"),
        kill_switch_enabled=True,
        leverage=Decimal("1"),
    )


class PortfolioRiskGovernor:
    def __init__(self, config: IntradayRiskConfig) -> None:
        if config.leverage != Decimal("1"):
            raise ValueError("execution research permits leverage 1x only")
        self.config = config
        self.state = GovernorState.ACTIVE
        self.events: list[RiskGovernorEvent] = []
        self.consecutive_losses = 0
        self.daily_realized_pnl = ZERO
        self.data_gaps = 0
        self.book_desyncs = 0
        self._cooldown_until: datetime | None = None
        self._day: date | None = None

    def approve_liquidity(self, quantity: Decimal, visible_quantity: Decimal) -> bool:
        if quantity <= ZERO or visible_quantity < ZERO:
            raise ValueError("liquidity check quantities are invalid")
        if self.state not in {GovernorState.ACTIVE, GovernorState.REDUCED}:
            return False
        return (
            visible_quantity > ZERO
            and quantity / visible_quantity <= self.config.maximum_visible_depth_fraction
        )

    def record_trade(self, pnl: Decimal, timestamp: datetime) -> GovernorState:
        self.reset_boundary(timestamp)
        self.daily_realized_pnl += pnl
        self.consecutive_losses = self.consecutive_losses + 1 if pnl < ZERO else 0
        if self.daily_realized_pnl <= -self.config.maximum_daily_loss_percent:
            self._transition(GovernorState.DAILY_KILLED, RiskReason.DAILY_LOSS_LIMIT, timestamp)
        elif self.consecutive_losses >= self.config.maximum_consecutive_losses:
            self._transition(GovernorState.COOLDOWN, RiskReason.LOSS_STREAK, timestamp)
            self._cooldown_until = timestamp + timedelta(milliseconds=self.config.cooldown_ms)
        elif pnl < ZERO:
            self._transition(GovernorState.REDUCED, RiskReason.LOSS_STREAK, timestamp)
        return self.state

    def observe_slippage(self, slippage_bps: Decimal, timestamp: datetime) -> GovernorState:
        if slippage_bps > self.config.maximum_slippage_bps:
            self._transition(GovernorState.REDUCED, RiskReason.ABNORMAL_SLIPPAGE, timestamp)
        return self.state

    def observe_data_gap(self, timestamp: datetime, *, desync: bool = False) -> GovernorState:
        if desync:
            self.book_desyncs += 1
            reason = RiskReason.REPEATED_BOOK_DESYNC
            count = self.book_desyncs
        else:
            self.data_gaps += 1
            reason = RiskReason.REPEATED_DATA_GAP
            count = self.data_gaps
        if count >= 3:
            self._transition(GovernorState.DATA_KILLED, reason, timestamp)
        return self.state

    def kill(self, reason: RiskReason, timestamp: datetime) -> GovernorState:
        self._transition(GovernorState.DATA_KILLED, reason, timestamp, critical=True)
        return self.state

    def reset_boundary(self, timestamp: datetime) -> GovernorState:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("risk timestamp must be timezone-aware")
        current_day = timestamp.astimezone(UTC).date()
        if self._day is None:
            self._day = current_day
        elif current_day != self._day:
            self._day = current_day
            self.daily_realized_pnl = ZERO
            self.consecutive_losses = 0
            self.state = GovernorState.ACTIVE
            self.data_gaps = 0
            self.book_desyncs = 0
        elif (
            self.state is GovernorState.COOLDOWN
            and self._cooldown_until is not None
            and timestamp >= self._cooldown_until
        ):
            self.state = GovernorState.REDUCED
        return self.state

    def _transition(
        self,
        state: GovernorState,
        reason: RiskReason,
        timestamp: datetime,
        *,
        critical: bool = False,
    ) -> None:
        previous = self.state
        self.state = state
        self.events.append(RiskGovernorEvent(timestamp, previous, state, reason, critical))
