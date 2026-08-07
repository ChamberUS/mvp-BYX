"""Pure position sizing and defensive-risk state for daily trend research."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.strategy.trend_following import TrendFollowingReasonCode


class PositionSizingReasonCode(StrEnum):
    INVALID_INITIAL_STOP = "INVALID_INITIAL_STOP"
    ZERO_RISK_DISTANCE = "ZERO_RISK_DISTANCE"
    POSITION_SIZE_ZERO = "POSITION_SIZE_ZERO"
    CASH_INSUFFICIENT = "CASH_INSUFFICIENT"
    MARGIN_INSUFFICIENT = "MARGIN_INSUFFICIENT"
    NOTIONAL_LIMIT = "NOTIONAL_LIMIT"
    POSITION_SIZE_APPROVED = "POSITION_SIZE_APPROVED"


class PositionSizingCap(StrEnum):
    NOTIONAL = "NOTIONAL"
    CASH = "CASH"
    MARGIN = "MARGIN"


class RiskMode(StrEnum):
    NORMAL = "NORMAL"
    DEFENSIVE = "DEFENSIVE"


class RiskPolicy(StrEnum):
    FIXED = "FIXED"
    DEFENSIVE = "DEFENSIVE"


class RiskWarning(StrEnum):
    UNEXPECTED_LIQUIDATION_AT_1X = "UNEXPECTED_LIQUIDATION_AT_1X"


@dataclass(frozen=True, slots=True)
class PositionSizingRequest:
    market: MarketType
    side: PositionSide
    equity: Decimal
    available_balance: Decimal
    reference_price: Decimal
    initial_stop: Decimal
    risk_percent: Decimal
    maximum_position_percent: Decimal
    taker_fee_bps: Decimal = Decimal("0")
    spread_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    leverage: Decimal = Decimal("1")
    margin_buffer_percent: Decimal = Decimal("0")
    maximum_notional: Decimal | None = None
    minimum_quantity: Decimal = Decimal("0")
    quantity_precision: int = 8

    def __post_init__(self) -> None:
        for name in (
            "equity",
            "available_balance",
            "reference_price",
            "initial_stop",
            "risk_percent",
            "maximum_position_percent",
            "taker_fee_bps",
            "spread_bps",
            "slippage_bps",
            "leverage",
            "margin_buffer_percent",
            "minimum_quantity",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise TypeError(f"{name} must be a finite Decimal")
        if self.maximum_notional is not None and (
            not isinstance(self.maximum_notional, Decimal)
            or not self.maximum_notional.is_finite()
        ):
            raise TypeError("maximum_notional must be a finite Decimal")
        if self.equity <= 0 or self.reference_price <= 0:
            raise ValueError("equity and reference_price must be positive")
        if self.available_balance < 0:
            raise ValueError("available_balance must be non-negative")
        if not Decimal("0") < self.risk_percent <= Decimal("100"):
            raise ValueError("risk_percent must be in (0, 100]")
        if not Decimal("0") < self.maximum_position_percent <= Decimal("100"):
            raise ValueError("maximum_position_percent must be in (0, 100]")
        if self.leverage != Decimal("1"):
            raise ValueError("trend following sizing requires leverage 1x")
        if any(
            value < 0
            for value in (
                self.taker_fee_bps,
                self.spread_bps,
                self.slippage_bps,
                self.margin_buffer_percent,
                self.minimum_quantity,
            )
        ):
            raise ValueError("costs, buffer and minimum quantity must be non-negative")
        if self.spread_bps + self.slippage_bps >= Decimal("10000"):
            raise ValueError("entry execution costs must remain below 10000 bps")
        if self.maximum_notional is not None and self.maximum_notional <= 0:
            raise ValueError("maximum_notional must be positive")
        if self.quantity_precision < 0:
            raise ValueError("quantity_precision must be non-negative")
        if self.market is MarketType.SPOT and self.side is PositionSide.SHORT:
            raise ValueError("Spot position sizing does not allow short positions")


@dataclass(frozen=True, slots=True)
class PositionSizingDecision:
    approved: bool
    reason_code: PositionSizingReasonCode
    quantity: Decimal
    risk_percent: Decimal
    risk_budget: Decimal
    estimated_entry_price: Decimal
    initial_stop: Decimal
    risk_per_unit: Decimal
    raw_risk_quantity: Decimal
    maximum_notional: Decimal
    position_notional: Decimal
    entry_fee: Decimal
    required_cash: Decimal
    required_margin: Decimal
    caps_applied: tuple[PositionSizingCap, ...]


def size_position(request: PositionSizingRequest) -> PositionSizingDecision:
    """Size one entry from structural risk, then apply deterministic hard caps."""

    cost_rate = (request.spread_bps + request.slippage_bps) / Decimal("10000")
    entry_price = (
        request.reference_price * (Decimal("1") + cost_rate)
        if request.side is PositionSide.LONG
        else request.reference_price * (Decimal("1") - cost_rate)
    )
    risk_budget = request.equity * request.risk_percent / Decimal("100")
    if request.initial_stop <= 0:
        return _sizing_rejection(
            request,
            PositionSizingReasonCode.INVALID_INITIAL_STOP,
            entry_price,
            risk_budget,
        )
    stop_wrong_side = (
        request.initial_stop > entry_price
        if request.side is PositionSide.LONG
        else request.initial_stop < entry_price
    )
    if stop_wrong_side:
        return _sizing_rejection(
            request,
            PositionSizingReasonCode.INVALID_INITIAL_STOP,
            entry_price,
            risk_budget,
        )
    if request.initial_stop == entry_price:
        return _sizing_rejection(
            request,
            PositionSizingReasonCode.ZERO_RISK_DISTANCE,
            entry_price,
            risk_budget,
        )

    risk_per_unit = abs(entry_price - request.initial_stop)
    raw_quantity = risk_budget / risk_per_unit
    if raw_quantity <= 0:
        return _sizing_rejection(
            request,
            PositionSizingReasonCode.POSITION_SIZE_ZERO,
            entry_price,
            risk_budget,
            risk_per_unit=risk_per_unit,
        )

    percent_notional = (
        request.equity
        * request.maximum_position_percent
        / Decimal("100")
        * request.leverage
    )
    maximum_notional = (
        min(percent_notional, request.maximum_notional)
        if request.maximum_notional is not None
        else percent_notional
    )
    notional_quantity = maximum_notional / entry_price
    fee_rate = request.taker_fee_bps / Decimal("10000")
    if request.market is MarketType.SPOT:
        balance_quantity = request.available_balance / (entry_price * (Decimal("1") + fee_rate))
        balance_cap = PositionSizingCap.CASH
        balance_rejection = PositionSizingReasonCode.CASH_INSUFFICIENT
    else:
        margin_unit = (
            entry_price / request.leverage
            * (Decimal("1") + request.margin_buffer_percent / Decimal("100"))
            + entry_price * fee_rate
        )
        balance_quantity = request.available_balance / margin_unit
        balance_cap = PositionSizingCap.MARGIN
        balance_rejection = PositionSizingReasonCode.MARGIN_INSUFFICIENT

    limiting_reason = PositionSizingReasonCode.POSITION_SIZE_ZERO
    quantity = raw_quantity
    caps: list[PositionSizingCap] = []
    if notional_quantity < quantity:
        quantity = notional_quantity
        limiting_reason = PositionSizingReasonCode.NOTIONAL_LIMIT
        caps.append(PositionSizingCap.NOTIONAL)
    if balance_quantity < quantity:
        quantity = balance_quantity
        limiting_reason = balance_rejection
        caps.append(balance_cap)

    quantum = Decimal("1").scaleb(-request.quantity_precision)
    quantity = quantity.quantize(quantum, rounding=ROUND_DOWN)
    if quantity <= 0 or quantity < request.minimum_quantity:
        return _sizing_rejection(
            request,
            limiting_reason,
            entry_price,
            risk_budget,
            risk_per_unit=risk_per_unit,
            raw_risk_quantity=raw_quantity,
            maximum_notional=maximum_notional,
            caps_applied=tuple(caps),
        )

    notional = entry_price * quantity
    fee = notional * fee_rate
    required_margin = (
        notional / request.leverage
        if request.market is MarketType.USD_M_FUTURES
        else Decimal("0")
    )
    required_cash = (
        notional + fee
        if request.market is MarketType.SPOT
        else required_margin
        * (Decimal("1") + request.margin_buffer_percent / Decimal("100"))
        + fee
    )
    return PositionSizingDecision(
        approved=True,
        reason_code=PositionSizingReasonCode.POSITION_SIZE_APPROVED,
        quantity=quantity,
        risk_percent=request.risk_percent,
        risk_budget=risk_budget,
        estimated_entry_price=entry_price,
        initial_stop=request.initial_stop,
        risk_per_unit=risk_per_unit,
        raw_risk_quantity=raw_quantity,
        maximum_notional=maximum_notional,
        position_notional=notional,
        entry_fee=fee,
        required_cash=required_cash,
        required_margin=required_margin,
        caps_applied=tuple(caps),
    )


def _sizing_rejection(
    request: PositionSizingRequest,
    reason: PositionSizingReasonCode,
    entry_price: Decimal,
    risk_budget: Decimal,
    *,
    risk_per_unit: Decimal = Decimal("0"),
    raw_risk_quantity: Decimal = Decimal("0"),
    maximum_notional: Decimal = Decimal("0"),
    caps_applied: tuple[PositionSizingCap, ...] = (),
) -> PositionSizingDecision:
    return PositionSizingDecision(
        approved=False,
        reason_code=reason,
        quantity=Decimal("0"),
        risk_percent=request.risk_percent,
        risk_budget=risk_budget,
        estimated_entry_price=entry_price,
        initial_stop=request.initial_stop,
        risk_per_unit=risk_per_unit,
        raw_risk_quantity=raw_risk_quantity,
        maximum_notional=maximum_notional,
        position_notional=Decimal("0"),
        entry_fee=Decimal("0"),
        required_cash=Decimal("0"),
        required_margin=Decimal("0"),
        caps_applied=caps_applied,
    )


@dataclass(frozen=True, slots=True)
class RiskState:
    mode: RiskMode = RiskMode.NORMAL
    consecutive_structural_losses: int = 0
    recovery_target: Decimal | None = None
    loss_sequence_start_equity: Decimal | None = None
    kill_date: date | None = None
    activated_at: datetime | None = None
    recovered_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.consecutive_structural_losses < 0:
            raise ValueError("consecutive losses must not be negative")
        for name in ("recovery_target", "loss_sequence_start_equity"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, Decimal) or not value.is_finite() or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative finite Decimal")
        if self.mode is RiskMode.DEFENSIVE and self.recovery_target is None:
            raise ValueError("defensive mode requires a recovery target")
        if self.mode is RiskMode.NORMAL and self.recovery_target is not None:
            raise ValueError("normal mode cannot retain a recovery target")


@dataclass(frozen=True, slots=True)
class TradeRiskOutcome:
    closed_at: datetime
    exit_reason: TrendFollowingReasonCode
    net_pnl: Decimal
    equity_before: Decimal
    equity_after: Decimal
    leverage: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.closed_at.tzinfo is None or self.closed_at.utcoffset() is None:
            raise ValueError("closed_at must be timezone-aware")
        for name in ("net_pnl", "equity_before", "equity_after", "leverage"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise TypeError(f"{name} must be a finite Decimal")
        if self.equity_before < 0 or self.equity_after < 0:
            raise ValueError("equity must not be negative")
        if self.leverage != Decimal("1"):
            raise ValueError("trend following outcomes require leverage 1x")


@dataclass(frozen=True, slots=True)
class RiskTransition:
    previous: RiskState
    current: RiskState
    risk_percent_before: Decimal
    risk_percent_after: Decimal
    counted_structural_loss: bool
    activated: bool
    recovered: bool
    warnings: tuple[RiskWarning, ...] = ()


class DefensiveRiskStateMachine:
    """Deterministic fixed/defensive risk policy with an explicit recovery target."""

    _STRUCTURAL_EXITS = frozenset(
        {
            TrendFollowingReasonCode.MACRO_FILTER_EXIT,
            TrendFollowingReasonCode.DONCHIAN_EXIT_10,
            TrendFollowingReasonCode.DONCHIAN_EXIT_20,
        }
    )
    _EXCLUDED_EXITS = frozenset(
        {
            TrendFollowingReasonCode.FORCED_END,
            TrendFollowingReasonCode.ADMINISTRATIVE_EXIT,
        }
    )

    def __init__(
        self,
        *,
        policy: RiskPolicy = RiskPolicy.DEFENSIVE,
        normal_risk_percent: Decimal = Decimal("1"),
        defensive_risk_percent: Decimal = Decimal("0.5"),
        state: RiskState | None = None,
    ) -> None:
        for name, value in (
            ("normal_risk_percent", normal_risk_percent),
            ("defensive_risk_percent", defensive_risk_percent),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be a positive finite Decimal")
        if defensive_risk_percent >= normal_risk_percent:
            raise ValueError("defensive risk must be below normal risk")
        self.policy = policy
        self.normal_risk_percent = normal_risk_percent
        self.defensive_risk_percent = defensive_risk_percent
        self._state = state or RiskState()

    @property
    def state(self) -> RiskState:
        return self._state

    @property
    def risk_percent(self) -> Decimal:
        return self._risk_percent(self._state)

    def killed_for_day(self, day: date) -> bool:
        return self._state.kill_date == day

    def begin_day(self, day: date) -> RiskState:
        if self._state.kill_date is not None and self._state.kill_date != day:
            self._state = replace(self._state, kill_date=None)
        return self._state

    def observe_equity(self, equity: Decimal, observed_at: datetime) -> RiskTransition:
        if not isinstance(equity, Decimal) or not equity.is_finite() or equity < 0:
            raise ValueError("equity must be a non-negative finite Decimal")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        previous = self._state
        before = self._risk_percent(previous)
        recovered = (
            previous.mode is RiskMode.DEFENSIVE
            and previous.recovery_target is not None
            and equity >= previous.recovery_target
        )
        if recovered:
            self._state = RiskState(
                mode=RiskMode.NORMAL,
                kill_date=previous.kill_date,
                recovered_at=observed_at,
            )
        return RiskTransition(
            previous=previous,
            current=self._state,
            risk_percent_before=before,
            risk_percent_after=self._risk_percent(self._state),
            counted_structural_loss=False,
            activated=False,
            recovered=recovered,
        )

    def record_trade(self, outcome: TradeRiskOutcome) -> RiskTransition:
        previous = self._state
        before = self._risk_percent(previous)
        warnings: tuple[RiskWarning, ...] = ()
        counted = False
        activated = False

        if outcome.exit_reason is TrendFollowingReasonCode.LIQUIDATION:
            target = (
                previous.loss_sequence_start_equity
                or previous.recovery_target
                or outcome.equity_before
            )
            self._state = RiskState(
                mode=RiskMode.DEFENSIVE,
                consecutive_structural_losses=previous.consecutive_structural_losses,
                recovery_target=target,
                loss_sequence_start_equity=target,
                kill_date=outcome.closed_at.astimezone(UTC).date(),
                activated_at=outcome.closed_at,
            )
            warnings = (RiskWarning.UNEXPECTED_LIQUIDATION_AT_1X,)
            activated = previous.mode is not RiskMode.DEFENSIVE
        elif outcome.exit_reason in self._EXCLUDED_EXITS:
            pass
        elif previous.mode is RiskMode.DEFENSIVE:
            if (
                outcome.exit_reason in self._STRUCTURAL_EXITS
                and outcome.net_pnl < 0
            ):
                counted = True
                self._state = replace(
                    previous,
                    consecutive_structural_losses=(
                        previous.consecutive_structural_losses + 1
                    ),
                )
        elif (
            outcome.exit_reason in self._STRUCTURAL_EXITS
            and outcome.net_pnl < 0
        ):
            counted = True
            sequence_start = (
                previous.loss_sequence_start_equity or outcome.equity_before
            )
            losses = previous.consecutive_structural_losses + 1
            if losses >= 3 and self.policy is RiskPolicy.DEFENSIVE:
                self._state = RiskState(
                    mode=RiskMode.DEFENSIVE,
                    consecutive_structural_losses=losses,
                    recovery_target=sequence_start,
                    loss_sequence_start_equity=sequence_start,
                    kill_date=previous.kill_date,
                    activated_at=outcome.closed_at,
                )
                activated = True
            elif self.policy is RiskPolicy.DEFENSIVE:
                self._state = replace(
                    previous,
                    consecutive_structural_losses=losses,
                    loss_sequence_start_equity=sequence_start,
                    recovered_at=None,
                )
        elif outcome.net_pnl >= 0 and self.policy is RiskPolicy.DEFENSIVE:
            self._state = replace(
                previous,
                consecutive_structural_losses=0,
                loss_sequence_start_equity=None,
                recovered_at=None,
            )

        recovered = False
        if outcome.exit_reason is not TrendFollowingReasonCode.LIQUIDATION:
            recovery = self.observe_equity(outcome.equity_after, outcome.closed_at)
            recovered = recovery.recovered
        return RiskTransition(
            previous=previous,
            current=self._state,
            risk_percent_before=before,
            risk_percent_after=self._risk_percent(self._state),
            counted_structural_loss=counted,
            activated=activated,
            recovered=recovered,
            warnings=warnings,
        )

    def _risk_percent(self, state: RiskState) -> Decimal:
        return (
            self.defensive_risk_percent
            if state.mode is RiskMode.DEFENSIVE
            else self.normal_risk_percent
        )
