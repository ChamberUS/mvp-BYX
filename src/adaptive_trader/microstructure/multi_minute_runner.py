"""Independent multi-minute profit-runner hypothesis; Elastic V0 remains untouched."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.microstructure.models import LiquiditySnapshot

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


class MultiMinuteRunnerVariant(StrEnum):
    RUNNER_10M = "MULTI_MINUTE_RUNNER_10M_V0"
    RUNNER_15M = "MULTI_MINUTE_RUNNER_15M_V0"


class MultiMinuteRunnerState(StrEnum):
    DISARMED = "DISARMED"
    ARMED = "ARMED"
    EXTENDING = "EXTENDING"
    REVERSAL_PENDING = "REVERSAL_PENDING"
    EXIT_REQUESTED = "EXIT_REQUESTED"
    FAILSAFE = "FAILSAFE"


@dataclass(frozen=True, slots=True)
class MultiMinuteProfitRunnerConfig:
    variant: MultiMinuteRunnerVariant
    activation_profit_bps: Decimal = Decimal("5")
    reversal_confirmation_ms: int = 150
    minimum_locked_profit_bps: Decimal = Decimal("1")
    maximum_peak_retrace_bps: Decimal = Decimal("4")
    estimated_entry_fee_bps: Decimal = Decimal("2")
    estimated_exit_fee_bps: Decimal = Decimal("2")
    estimated_slippage_bps: Decimal = Decimal("1")
    maximum_spread_bps: Decimal = Decimal("5")
    maximum_book_age_ms: Decimal = Decimal("250")

    @property
    def maximum_hold_ms(self) -> int:
        return 600_000 if self.variant is MultiMinuteRunnerVariant.RUNNER_10M else 900_000

    def __post_init__(self) -> None:
        if self.reversal_confirmation_ms != 150:
            raise ValueError("runner reversal confirmation is frozen at 150ms")
        if self.minimum_locked_profit_bps >= self.activation_profit_bps:
            raise ValueError("runner hard floor must be below activation")
        for name in (
            "activation_profit_bps",
            "minimum_locked_profit_bps",
            "maximum_peak_retrace_bps",
            "estimated_entry_fee_bps",
            "estimated_exit_fee_bps",
            "estimated_slippage_bps",
            "maximum_spread_bps",
            "maximum_book_age_ms",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < ZERO:
                raise ValueError(f"{name} must be non-negative and finite")


@dataclass(frozen=True, slots=True)
class RunnerReversalEvidence:
    price: bool = False
    ofi: bool = False
    aggressive_flow: bool = False
    depth: bool = False
    microprice: bool = False

    @property
    def fixed_rule_triggered(self) -> bool:
        return self.ofi or self.aggressive_flow or self.depth or self.microprice


@dataclass(frozen=True, slots=True)
class MultiMinuteRunnerObservation:
    timestamp: datetime
    state: MultiMinuteRunnerState
    executable_reference: Decimal | None
    current_net_profit_bps: Decimal | None
    activation_profit_bps: Decimal | None
    peak_net_profit_bps: Decimal | None
    maximum_giveback_bps: Decimal | None
    floor_at_exit_bps: Decimal | None
    activation_time: datetime | None
    maximum_hold_deadline: datetime | None
    reversal_started_at: datetime | None
    exit_reason: str | None
    reversal_evidence: RunnerReversalEvidence
    mark_price_ignored: bool = True


class MultiMinuteProfitRunnerController:
    """Event-time runner with no inherited 300ms no-new-peak timeout."""

    def __init__(
        self,
        *,
        side: PositionSide,
        quantity: Decimal,
        entry_price: Decimal,
        config: MultiMinuteProfitRunnerConfig,
    ) -> None:
        if quantity <= ZERO or entry_price <= ZERO:
            raise ValueError("runner quantity and entry price must be positive")
        self.side = side
        self.quantity = quantity
        self.entry_price = entry_price
        self.config = config
        self.state = MultiMinuteRunnerState.DISARMED
        self.activation_time: datetime | None = None
        self.maximum_hold_deadline: datetime | None = None
        self.reversal_started_at: datetime | None = None
        self.activation_profit: Decimal | None = None
        self.peak_profit: Decimal | None = None
        self.maximum_giveback = ZERO
        self.exit_reason: str | None = None

    def observe(
        self,
        *,
        timestamp: datetime,
        liquidity: LiquiditySnapshot | None,
        reversal: RunnerReversalEvidence | None = None,
        feed_ready: bool = True,
        capture_boundary_valid: bool = True,
        accounting_invariant_valid: bool = True,
        mark_price: Decimal | None = None,
    ) -> MultiMinuteRunnerObservation:
        reversal = reversal or RunnerReversalEvidence()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("runner timestamp must be timezone-aware")
        if self.state in {MultiMinuteRunnerState.EXIT_REQUESTED, MultiMinuteRunnerState.FAILSAFE}:
            return self._observation(timestamp, None, None, reversal)
        if not capture_boundary_valid:
            self.state = MultiMinuteRunnerState.FAILSAFE
            self.exit_reason = "CAPTURE_BOUNDARY_INVALID"
            return self._observation(timestamp, None, None, reversal)
        if not accounting_invariant_valid:
            self.state = MultiMinuteRunnerState.FAILSAFE
            self.exit_reason = "ACCOUNTING_RISK_INVARIANT"
            return self._observation(timestamp, None, None, reversal)
        if (
            liquidity is None
            or not feed_ready
            or not liquidity.synchronized
            or liquidity.book_age_ms > self.config.maximum_book_age_ms
            or liquidity.spread_bps > self.config.maximum_spread_bps
        ):
            self.state = MultiMinuteRunnerState.FAILSAFE
            self.exit_reason = "LIQUIDITY_EXIT_FAILSAFE"
            return self._observation(timestamp, None, None, reversal)
        if liquidity.timestamp > timestamp:
            raise ValueError("runner liquidity cannot come from the future")
        executable = (
            liquidity.executable_sell_price(self.quantity)
            if self.side is PositionSide.LONG
            else liquidity.executable_buy_price(self.quantity)
        )
        if executable is None:
            self.state = MultiMinuteRunnerState.FAILSAFE
            self.exit_reason = "LIQUIDITY_EXIT_FAILSAFE"
            return self._observation(timestamp, None, None, reversal)
        net = self._net_profit_bps(executable)
        if self.state is MultiMinuteRunnerState.DISARMED:
            if net >= self.config.activation_profit_bps:
                self.state = MultiMinuteRunnerState.ARMED
                self.activation_time = timestamp
                self.maximum_hold_deadline = timestamp + timedelta(
                    milliseconds=self.config.maximum_hold_ms
                )
                self.activation_profit = net
                self.peak_profit = net
            return self._observation(timestamp, executable, net, reversal)

        peak = self.peak_profit
        if peak is None:
            raise RuntimeError("armed runner lost peak")
        giveback = peak - net
        self.maximum_giveback = max(self.maximum_giveback, giveback)
        if (
            net <= self.config.minimum_locked_profit_bps
            or giveback >= self.config.maximum_peak_retrace_bps
        ):
            self.state = MultiMinuteRunnerState.EXIT_REQUESTED
            self.exit_reason = "HARD_PROFIT_FLOOR"
            return self._observation(timestamp, executable, net, reversal)
        if self.maximum_hold_deadline is not None and timestamp >= self.maximum_hold_deadline:
            self.state = MultiMinuteRunnerState.EXIT_REQUESTED
            self.exit_reason = (
                "MAX_HOLD_10M"
                if self.config.maximum_hold_ms == 600_000
                else "MAX_HOLD_15M"
            )
            return self._observation(timestamp, executable, net, reversal)
        if net > peak:
            self.peak_profit = net
            self.reversal_started_at = None
            self.state = MultiMinuteRunnerState.EXTENDING
            return self._observation(timestamp, executable, net, reversal)
        if reversal.fixed_rule_triggered:
            if self.reversal_started_at is None:
                self.reversal_started_at = timestamp
                self.state = MultiMinuteRunnerState.REVERSAL_PENDING
            elif timestamp >= self.reversal_started_at + timedelta(
                milliseconds=self.config.reversal_confirmation_ms
            ):
                self.state = MultiMinuteRunnerState.EXIT_REQUESTED
                self.exit_reason = "REVERSAL_CONFIRMED_150MS"
            return self._observation(timestamp, executable, net, reversal)
        self.reversal_started_at = None
        self.state = MultiMinuteRunnerState.EXTENDING
        return self._observation(timestamp, executable, net, reversal)

    def _net_profit_bps(self, executable: Decimal) -> Decimal:
        gross = (
            (executable / self.entry_price - Decimal("1")) * TEN_THOUSAND
            if self.side is PositionSide.LONG
            else (self.entry_price / executable - Decimal("1")) * TEN_THOUSAND
        )
        return gross - (
            self.config.estimated_entry_fee_bps
            + self.config.estimated_exit_fee_bps
            + self.config.estimated_slippage_bps
        )

    def _observation(
        self,
        timestamp: datetime,
        executable: Decimal | None,
        net: Decimal | None,
        reversal: RunnerReversalEvidence,
    ) -> MultiMinuteRunnerObservation:
        return MultiMinuteRunnerObservation(
            timestamp=timestamp,
            state=self.state,
            executable_reference=executable,
            current_net_profit_bps=net,
            activation_profit_bps=self.activation_profit,
            peak_net_profit_bps=self.peak_profit,
            maximum_giveback_bps=self.maximum_giveback if self.peak_profit is not None else None,
            floor_at_exit_bps=(
                self.config.minimum_locked_profit_bps
                if self.state is MultiMinuteRunnerState.EXIT_REQUESTED
                else None
            ),
            activation_time=self.activation_time,
            maximum_hold_deadline=self.maximum_hold_deadline,
            reversal_started_at=self.reversal_started_at,
            exit_reason=self.exit_reason,
            reversal_evidence=reversal,
        )
