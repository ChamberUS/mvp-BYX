"""Event-time Elastic Profit Exit hypothesis; never uses mark as executable price."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.microstructure.models import LiquiditySnapshot, ProfitExtensionState

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


@dataclass(frozen=True, slots=True)
class ElasticProfitExitConfig:
    profile_id: str = "ELASTIC_300_150_V0"
    activation_profit_bps: Decimal = Decimal("5")
    continuation_grace_ms: int = 300
    reversal_confirmation_ms: int = 150
    minimum_locked_profit_bps: Decimal = Decimal("1")
    maximum_peak_retrace_bps: Decimal = Decimal("4")
    estimated_entry_fee_bps: Decimal = Decimal("2")
    estimated_exit_fee_bps: Decimal = Decimal("2")
    estimated_slippage_bps: Decimal = Decimal("1")
    maximum_spread_bps: Decimal = Decimal("5")
    maximum_book_age_ms: Decimal = Decimal("250")

    def __post_init__(self) -> None:
        if self.profile_id != "ELASTIC_300_150_V0":
            raise ValueError("only ELASTIC_300_150_V0 is available")
        if self.continuation_grace_ms != 300 or self.reversal_confirmation_ms != 150:
            raise ValueError("the diagnostic Elastic profile is fixed at 300/150ms")
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
            if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO:
                raise ValueError(f"{name} must be a non-negative finite Decimal")
        if self.minimum_locked_profit_bps >= self.activation_profit_bps:
            raise ValueError("locked profit must remain below activation profit")


@dataclass(frozen=True, slots=True)
class ElasticProfitExitObservation:
    timestamp: datetime
    state: ProfitExtensionState
    executable_reference: Decimal | None
    net_executable_profit_bps: Decimal | None
    peak_executable_price: Decimal | None
    peak_net_profit_bps: Decimal | None
    retracement_from_peak_bps: Decimal | None
    activation_time: datetime | None
    peak_time: datetime | None
    continuation_deadline: datetime | None
    reversal_started_at: datetime | None
    exit_reason: str | None
    mark_price_ignored: bool = True


class ElasticProfitExitController:
    """One-position controller driven solely by event timestamps and executable depth."""

    def __init__(
        self,
        *,
        side: PositionSide,
        quantity: Decimal,
        entry_price: Decimal,
        config: ElasticProfitExitConfig | None = None,
    ) -> None:
        if quantity <= ZERO or entry_price <= ZERO:
            raise ValueError("quantity and entry_price must be positive")
        self.side = side
        self.quantity = quantity
        self.entry_price = entry_price
        self.config = config or ElasticProfitExitConfig()
        self.state = ProfitExtensionState.DISARMED
        self.activation_time: datetime | None = None
        self.peak_time: datetime | None = None
        self.peak_price: Decimal | None = None
        self.peak_net_profit_bps: Decimal | None = None
        self.continuation_deadline: datetime | None = None
        self.reversal_started_at: datetime | None = None
        self.exit_reason: str | None = None

    def observe(
        self,
        *,
        timestamp: datetime,
        liquidity: LiquiditySnapshot,
        microstructure_reversal: bool,
        mark_price: Decimal | None = None,
    ) -> ElasticProfitExitObservation:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Elastic timestamp must be timezone-aware")
        if liquidity.timestamp > timestamp:
            raise ValueError("Elastic liquidity cannot come from the future")
        if self.state in {
            ProfitExtensionState.EXIT_REQUESTED,
            ProfitExtensionState.FAILSAFE,
        }:
            return self._observation(timestamp, None, None)
        if (
            not liquidity.synchronized
            or liquidity.book_age_ms > self.config.maximum_book_age_ms
            or liquidity.spread_bps > self.config.maximum_spread_bps
        ):
            self.state = ProfitExtensionState.FAILSAFE
            self.exit_reason = "LIQUIDITY_EXIT_FAILSAFE"
            return self._observation(timestamp, None, None)
        executable = (
            liquidity.executable_sell_price(self.quantity)
            if self.side is PositionSide.LONG
            else liquidity.executable_buy_price(self.quantity)
        )
        if executable is None:
            self.state = ProfitExtensionState.FAILSAFE
            self.exit_reason = "LIQUIDITY_EXIT_FAILSAFE"
            return self._observation(timestamp, None, None)
        net_profit = self._net_profit_bps(executable)
        if self.state is ProfitExtensionState.DISARMED:
            if net_profit >= self.config.activation_profit_bps:
                self.state = ProfitExtensionState.ARMED
                self.activation_time = timestamp
                self._set_peak(timestamp, executable, net_profit)
            return self._observation(timestamp, executable, net_profit)

        peak_profit = self.peak_net_profit_bps
        if peak_profit is None:
            raise RuntimeError("armed Elastic controller lost peak state")
        retracement = peak_profit - net_profit
        if (
            net_profit <= self.config.minimum_locked_profit_bps
            or retracement >= self.config.maximum_peak_retrace_bps
        ):
            self.state = ProfitExtensionState.EXIT_REQUESTED
            self.exit_reason = "HARD_PROFIT_FLOOR"
            return self._observation(timestamp, executable, net_profit)

        if net_profit > peak_profit:
            self.state = ProfitExtensionState.EXTENDING
            self.reversal_started_at = None
            self._set_peak(timestamp, executable, net_profit)
            return self._observation(timestamp, executable, net_profit)

        if microstructure_reversal:
            if self.reversal_started_at is None:
                self.reversal_started_at = timestamp
                self.state = ProfitExtensionState.REVERSAL_PENDING
            elif timestamp >= self.reversal_started_at + timedelta(
                milliseconds=self.config.reversal_confirmation_ms
            ):
                self.state = ProfitExtensionState.EXIT_REQUESTED
                self.exit_reason = "REVERSAL_CONFIRMED_150MS"
            return self._observation(timestamp, executable, net_profit)

        if self.reversal_started_at is not None:
            self.reversal_started_at = None
            self.state = ProfitExtensionState.EXTENDING
        if self.continuation_deadline is not None and timestamp >= self.continuation_deadline:
            self.state = ProfitExtensionState.EXIT_REQUESTED
            self.exit_reason = "NO_NEW_PEAK_300MS"
        elif self.state is ProfitExtensionState.ARMED:
            self.state = ProfitExtensionState.EXTENDING
        return self._observation(timestamp, executable, net_profit)

    def _set_peak(
        self,
        timestamp: datetime,
        executable: Decimal,
        net_profit: Decimal,
    ) -> None:
        self.peak_time = timestamp
        self.peak_price = executable
        self.peak_net_profit_bps = net_profit
        self.continuation_deadline = timestamp + timedelta(
            milliseconds=self.config.continuation_grace_ms
        )

    def _net_profit_bps(self, executable: Decimal) -> Decimal:
        gross = (
            (executable / self.entry_price - Decimal("1")) * TEN_THOUSAND
            if self.side is PositionSide.LONG
            else (self.entry_price / executable - Decimal("1")) * TEN_THOUSAND
        )
        costs = (
            self.config.estimated_entry_fee_bps
            + self.config.estimated_exit_fee_bps
            + self.config.estimated_slippage_bps
        )
        return gross - costs

    def _observation(
        self,
        timestamp: datetime,
        executable: Decimal | None,
        net_profit: Decimal | None,
    ) -> ElasticProfitExitObservation:
        retracement = (
            self.peak_net_profit_bps - net_profit
            if self.peak_net_profit_bps is not None and net_profit is not None
            else None
        )
        return ElasticProfitExitObservation(
            timestamp=timestamp,
            state=self.state,
            executable_reference=executable,
            net_executable_profit_bps=net_profit,
            peak_executable_price=self.peak_price,
            peak_net_profit_bps=self.peak_net_profit_bps,
            retracement_from_peak_bps=retracement,
            activation_time=self.activation_time,
            peak_time=self.peak_time,
            continuation_deadline=self.continuation_deadline,
            reversal_started_at=self.reversal_started_at,
            exit_reason=self.exit_reason,
        )
