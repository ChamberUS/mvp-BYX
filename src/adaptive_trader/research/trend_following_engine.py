"""Research-only daily trend-following simulator.

Signals are evaluated on closed UTC daily candles.  Orders caused by a daily
decision can only execute on the first persisted 1h open of the following UTC
day.  Futures financing, mark-to-market and liquidation remain on the 1h
financial clock; a Donchian level is an entry-sizing reference and is never an
intraday stop in this engine.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Final

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.domain.models import Candle
from adaptive_trader.futures.accounting import (
    approximate_liquidation_price,
    funding_cash_flow,
    unrealized_pnl,
)
from adaptive_trader.futures.models import (
    FundingRate,
    FuturesCandle,
    MarkPriceCandle,
)
from adaptive_trader.research.trend_following_risk import (
    DefensiveRiskStateMachine,
    PositionSizingRequest,
    RiskPolicy,
    TradeRiskOutcome,
    size_position,
)
from adaptive_trader.strategy.trend_following import (
    MacroTrendSide,
    TrendFollowingDecision,
    TrendFollowingDecisionEngine,
    TrendFollowingDirection,
    TrendFollowingParameters,
    TrendFollowingReasonCode,
)

ZERO: Final = Decimal("0")
ONE: Final = Decimal("1")
HUNDRED: Final = Decimal("100")
TEN_THOUSAND: Final = Decimal("10000")
DAY: Final = timedelta(days=1)
HOUR: Final = timedelta(hours=1)


class TrendFollowingExitReason(StrEnum):
    """Closed-trade reasons used by the pre-registered experiment."""

    MACRO_FILTER_EXIT = "MACRO_FILTER_EXIT"
    DONCHIAN_EXIT_10 = "DONCHIAN_EXIT_10"
    DONCHIAN_EXIT_20 = "DONCHIAN_EXIT_20"
    FORCED_END = "FORCED_END"
    LIQUIDATION = "LIQUIDATION"


class EngineRiskMode(StrEnum):
    """Risk state persisted by each trade and trace."""

    NORMAL = "NORMAL"
    DEFENSIVE = "DEFENSIVE"


@dataclass(frozen=True, slots=True)
class TrendFollowingEngineConfig:
    market: str
    mode: str
    variant_id: str
    period: str
    scenario: str
    evaluation_start: datetime
    evaluation_end: datetime
    exit_period: int
    defensive_risk: bool
    initial_capital: Decimal = Decimal("10000")
    normal_risk_percent: Decimal = Decimal("1")
    defensive_risk_percent: Decimal = Decimal("0.5")
    activation_losses: int = 3
    maximum_position_percent: Decimal = Decimal("100")
    maximum_notional: Decimal | None = None
    leverage: Decimal = Decimal("1")
    fee_bps: Decimal = Decimal("5")
    spread_bps: Decimal = Decimal("2")
    slippage_bps: Decimal = Decimal("5")
    minimum_quantity: Decimal = Decimal("0.000001")
    quantity_step: Decimal = Decimal("0.000001")
    maintenance_margin_rate: Decimal = Decimal("0.005")
    liquidation_fee_rate: Decimal = Decimal("0.005")
    margin_buffer_percent: Decimal = Decimal("1")
    funding_enabled: bool = True

    def __post_init__(self) -> None:
        if self.market not in {"spot", "futures"}:
            raise ValueError("trend-following market must be spot or futures")
        allowed_modes = {"long"} if self.market == "spot" else {"long", "short", "long-short"}
        if self.mode not in allowed_modes:
            raise ValueError("trend-following mode is incompatible with market")
        if self.period not in {"DEVELOPMENT", "VALIDATION"}:
            raise ValueError("period must be DEVELOPMENT or VALIDATION")
        if self.scenario not in {"LOW", "BASE", "HIGH", "STRESS"}:
            raise ValueError("unsupported cost scenario")
        if self.exit_period not in {10, 20}:
            raise ValueError("exit period must be 10 or 20")
        if self.leverage != ONE:
            raise ValueError("Sprint 3C.1 permits leverage 1 only")
        if (
            self.evaluation_start.tzinfo is None
            or self.evaluation_end.tzinfo is None
            or self.evaluation_start.utcoffset() != timedelta(0)
            or self.evaluation_end.utcoffset() != timedelta(0)
        ):
            raise ValueError("evaluation boundaries must be aware UTC")
        if self.evaluation_start > self.evaluation_end:
            raise ValueError("evaluation start must not exceed end")
        for name in (
            "initial_capital",
            "normal_risk_percent",
            "defensive_risk_percent",
            "maximum_position_percent",
            "leverage",
            "minimum_quantity",
            "quantity_step",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= ZERO:
                raise ValueError(f"{name} must be a positive finite Decimal")
        for name in (
            "fee_bps",
            "spread_bps",
            "slippage_bps",
            "maintenance_margin_rate",
            "liquidation_fee_rate",
            "margin_buffer_percent",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO:
                raise ValueError(f"{name} must be a non-negative finite Decimal")
        if self.maximum_notional is not None and self.maximum_notional <= ZERO:
            raise ValueError("maximum_notional must be positive when supplied")
        if self.activation_losses != 3:
            raise ValueError("defensive activation is pre-registered at three losses")


@dataclass(frozen=True, slots=True)
class TrendFollowingDecisionTrace:
    market: str
    mode: str
    variant_id: str
    period: str
    scenario: str
    date: datetime
    close: Decimal
    sma_200: Decimal | None
    previous_20_high: Decimal | None
    previous_20_low: Decimal | None
    exit_channel: Decimal | None
    macro_side: str
    breakout_long: bool
    breakout_short: bool
    position_side: str
    risk_mode: str
    risk_percent: Decimal
    risk_budget: Decimal | None
    initial_stop: Decimal | None
    risk_per_unit: Decimal | None
    quantity: Decimal | None
    macro_exit_true: bool
    donchian_exit_true: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class TrendFollowingClosedTrade:
    market: str
    mode: str
    variant_id: str
    period: str
    scenario: str
    side: str
    entry_time: datetime
    exit_time: datetime
    entry_reference_price: Decimal
    exit_reference_price: Decimal
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    initial_stop: Decimal
    risk_mode_at_entry: str
    risk_percent: Decimal
    risk_budget: Decimal
    consecutive_losses_before: int
    consecutive_losses_after: int
    recovery_target_before: Decimal | None
    recovery_target_after: Decimal | None
    equity_before: Decimal
    equity_after: Decimal
    defensive_activated_at: datetime | None
    defensive_recovered_at: datetime | None
    gross_pnl: Decimal
    fees: Decimal
    execution_costs: Decimal
    funding_paid: Decimal
    funding_received: Decimal
    net_funding: Decimal
    liquidation_fee: Decimal
    net_pnl: Decimal
    holding_hours: int
    exit_reason: str
    macro_exit_true: bool
    donchian_exit_true: bool
    liquidated: bool


@dataclass(frozen=True, slots=True)
class TrendFollowingRun:
    market: str
    mode: str
    variant_id: str
    period: str
    scenario: str
    evaluation_start: datetime
    evaluation_end: datetime
    effective_evaluation_start: datetime
    initial_capital: Decimal
    final_capital: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    gross_return_percent: Decimal
    net_return_percent: Decimal
    win_rate_percent: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal
    median_trade_pnl: Decimal
    maximum_drawdown_percent: Decimal
    return_to_drawdown: Decimal | None
    exposure_percent: Decimal
    fees: Decimal
    execution_costs: Decimal
    funding_paid: Decimal
    funding_received: Decimal
    net_funding: Decimal
    liquidation_count: int
    evaluated_daily_candles: int
    entry_signals: int
    risk_approvals: int
    executions: int
    long_trades: int
    short_trades: int
    defensive_mode_activations: int
    candles_in_defensive_mode: int
    trades_in_defensive_mode: int
    risk_reduction_duration_days: int
    net_pnl_without_best_trade: Decimal
    net_pnl_without_top_three: Decimal
    best_trade_concentration_percent: Decimal
    top_three_concentration_percent: Decimal
    reason_counts: tuple[tuple[str, int], ...]
    warnings: tuple[str, ...]
    trades: tuple[TrendFollowingClosedTrade, ...]
    traces: tuple[TrendFollowingDecisionTrace, ...]


@dataclass(slots=True)
class _PendingAction:
    execute_date: date
    action: str
    side: PositionSide | None
    initial_stop: Decimal | None
    reason: TrendFollowingExitReason | None
    macro_exit_true: bool = False
    donchian_exit_true: bool = False


@dataclass(slots=True)
class _Position:
    side: PositionSide
    entry_time: datetime
    entry_reference_price: Decimal
    entry_price: Decimal
    quantity: Decimal
    initial_stop: Decimal
    entry_fee: Decimal
    entry_execution_cost: Decimal
    equity_before: Decimal
    risk_mode_at_entry: EngineRiskMode
    risk_percent: Decimal
    risk_budget: Decimal
    consecutive_losses_before: int
    recovery_target_before: Decimal | None
    liquidation_price: Decimal | None
    funding: Decimal = ZERO
    holding_hours: int = 0


@dataclass(slots=True)
class _DefensiveState:
    enabled: bool
    activations: int = 0
    days_defensive: int = 0
    _machine: DefensiveRiskStateMachine = field(init=False, repr=False)
    _last_activated_at: datetime | None = field(default=None, init=False)
    _last_recovered_at: datetime | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._machine = DefensiveRiskStateMachine(
            policy=RiskPolicy.DEFENSIVE if self.enabled else RiskPolicy.FIXED
        )

    @property
    def mode(self) -> EngineRiskMode:
        return EngineRiskMode(self._machine.state.mode.value)

    @property
    def consecutive_losses(self) -> int:
        return self._machine.state.consecutive_structural_losses

    @property
    def sequence_start_equity(self) -> Decimal | None:
        return self._machine.state.loss_sequence_start_equity

    @property
    def recovery_target(self) -> Decimal | None:
        return self._machine.state.recovery_target

    @property
    def activated_at(self) -> datetime | None:
        return self._last_activated_at

    @property
    def recovered_at(self) -> datetime | None:
        return self._last_recovered_at

    @property
    def risk_percent(self) -> Decimal:
        return self._machine.risk_percent

    def observe_equity(self, equity: Decimal, observed_at: datetime) -> None:
        transition = self._machine.observe_equity(equity, observed_at)
        if transition.recovered:
            self._last_recovered_at = observed_at

    def record_close(
        self,
        *,
        reason: TrendFollowingExitReason,
        net_pnl: Decimal,
        equity_before_trade: Decimal,
        equity_after: Decimal,
        closed_at: datetime,
    ) -> None:
        transition = self._machine.record_trade(
            TradeRiskOutcome(
                closed_at=closed_at,
                exit_reason=TrendFollowingReasonCode(reason.value),
                net_pnl=net_pnl,
                equity_before=equity_before_trade,
                equity_after=equity_after,
            )
        )
        if transition.activated:
            self.activations += 1
            self._last_activated_at = closed_at
        if transition.recovered:
            self._last_recovered_at = closed_at


def _median(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        return ZERO
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _day_open(value: datetime) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _effective_price(
    reference: Decimal,
    *,
    buying: bool,
    spread_bps: Decimal,
    slippage_bps: Decimal,
) -> Decimal:
    direction = ONE if buying else -ONE
    return reference * (ONE + direction * (spread_bps + slippage_bps) / TEN_THOUSAND)


def _quantity_floor(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


def _maximum_drawdown(equity_curve: tuple[Decimal, ...]) -> Decimal:
    if not equity_curve:
        return ZERO
    peak = equity_curve[0]
    maximum = ZERO
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > ZERO:
            maximum = max(maximum, (peak - equity) / peak * HUNDRED)
    return maximum


def _concentration(
    trades: tuple[TrendFollowingClosedTrade, ...],
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    ranked = tuple(sorted((trade.net_pnl for trade in trades), reverse=True))
    positive_total = sum((item for item in ranked if item > ZERO), ZERO)

    def percent(count: int) -> Decimal:
        if positive_total <= ZERO:
            return ZERO
        return sum((max(value, ZERO) for value in ranked[:count]), ZERO) / positive_total * HUNDRED

    return (
        percent(1),
        percent(3),
        sum(ranked[1:], ZERO),
        sum(ranked[3:], ZERO),
    )


class TrendFollowingEngine:
    """Deterministic simulator dedicated to Sprint 3C.1."""

    def run(
        self,
        *,
        config: TrendFollowingEngineConfig,
        daily_candles: tuple[Candle, ...],
        hourly_candles: tuple[Candle | FuturesCandle, ...],
        marks: tuple[MarkPriceCandle, ...] = (),
        funding: tuple[FundingRate, ...] = (),
    ) -> TrendFollowingRun:
        self._validate_inputs(config, daily_candles, hourly_candles, marks, funding)
        daily_by_date = {candle.open_time.date(): candle for candle in daily_candles}
        daily_index = {candle.open_time.date(): index for index, candle in enumerate(daily_candles)}
        hourly_by_date: dict[date, list[Candle | FuturesCandle]] = {}
        for candle in hourly_candles:
            hourly_by_date.setdefault(candle.open_time.date(), []).append(candle)
        for values in hourly_by_date.values():
            values.sort(key=lambda value: value.open_time)
        marks_by_hour = {item.open_time: item for item in marks}
        funding_by_hour: dict[datetime, list[FundingRate]] = {}
        for item in funding:
            hour = item.funding_time.replace(minute=0, second=0, microsecond=0)
            funding_by_hour.setdefault(hour, []).append(item)
        decision_engine = TrendFollowingDecisionEngine(
            TrendFollowingParameters(
                sma_period=200,
                entry_period=20,
                exit_period=config.exit_period,
                allow_long=self._allows_long(config),
                allow_short=self._allows_short(config),
            )
        )

        cash_or_wallet = config.initial_capital
        position: _Position | None = None
        pending: _PendingAction | None = None
        state = _DefensiveState(enabled=config.defensive_risk)
        trades: list[TrendFollowingClosedTrade] = []
        traces: list[TrendFollowingDecisionTrace] = []
        reasons: Counter[str] = Counter()
        warnings: list[str] = []
        equity_curve: list[Decimal] = [config.initial_capital]
        evaluated = 0
        entry_signals = 0
        risk_approvals = 0
        executions = 0
        exposed_days = 0
        defensive_trades = 0
        last_mark: Decimal | None = None
        kill_date: date | None = None
        effective_start: datetime | None = None

        cursor = _day_open(config.evaluation_start)
        final_day = _day_open(config.evaluation_end)
        while cursor <= final_day:
            current_date = cursor.date()
            hours = tuple(hourly_by_date.get(current_date, ()))
            if pending is not None and pending.execute_date < current_date:
                reasons["NO_ELIGIBLE_NEXT_DAY_OPEN"] += 1
                pending = None

            for hour_index, hourly in enumerate(hours):
                if config.market == "futures":
                    mark = marks_by_hour.get(hourly.open_time)
                    if mark is None:
                        raise ValueError("Futures trend-following requires exact 1h mark alignment")
                    hour_funding = tuple(funding_by_hour.get(hourly.open_time, ()))
                    cash_or_wallet = self._apply_funding(
                        config=config,
                        position=position,
                        events=tuple(
                            rate for rate in hour_funding if rate.funding_time == hourly.open_time
                        ),
                        mark_open=mark.open,
                        cash_or_wallet=cash_or_wallet,
                    )
                    last_mark = mark.open
                    if position is not None and self._liquidated_at_open(position, mark.open):
                        cash_or_wallet, closed = self._close_position(
                            config=config,
                            position=position,
                            reference_price=mark.open,
                            closed_at=hourly.open_time,
                            reason=TrendFollowingExitReason.LIQUIDATION,
                            cash_or_wallet=cash_or_wallet,
                            state=state,
                            macro_exit_true=False,
                            donchian_exit_true=False,
                        )
                        trades.append(closed)
                        position = None
                        pending = None
                        kill_date = current_date
                        warnings.append("UNEXPECTED_LIQUIDATION_AT_1X")
                        reasons[TrendFollowingExitReason.LIQUIDATION.value] += 1

                if hour_index == 0 and pending is not None and pending.execute_date == current_date:
                    if pending.action == "EXIT" and position is not None:
                        reason = pending.reason
                        if reason is None:
                            raise RuntimeError("pending exit requires a reason")
                        cash_or_wallet, closed = self._close_position(
                            config=config,
                            position=position,
                            reference_price=hourly.open,
                            closed_at=hourly.open_time,
                            reason=reason,
                            cash_or_wallet=cash_or_wallet,
                            state=state,
                            macro_exit_true=pending.macro_exit_true,
                            donchian_exit_true=pending.donchian_exit_true,
                        )
                        trades.append(closed)
                        position = None
                    elif (
                        pending.action == "ENTRY"
                        and position is None
                        and pending.side is not None
                        and pending.initial_stop is not None
                        and kill_date != current_date
                    ):
                        (
                            position,
                            cash_or_wallet,
                            sizing_reason,
                        ) = self._open_position(
                            config=config,
                            side=pending.side,
                            initial_stop=pending.initial_stop,
                            reference_price=hourly.open,
                            opened_at=hourly.open_time,
                            cash_or_wallet=cash_or_wallet,
                            state=state,
                        )
                        reasons[sizing_reason] += 1
                        if traces:
                            previous_trace = traces[-1]
                            if position is None:
                                traces[-1] = replace(
                                    previous_trace,
                                    quantity=ZERO,
                                    reason_code=sizing_reason,
                                )
                                reasons[TrendFollowingReasonCode.RISK_REJECTED.value] += 1
                            else:
                                traces[-1] = replace(
                                    previous_trace,
                                    risk_mode=position.risk_mode_at_entry.value,
                                    risk_percent=position.risk_percent,
                                    risk_budget=position.risk_budget,
                                    initial_stop=position.initial_stop,
                                    risk_per_unit=abs(position.entry_price - position.initial_stop),
                                    quantity=position.quantity,
                                )
                        if position is not None:
                            risk_approvals += 1
                            executions += 1
                            if position.risk_mode_at_entry is EngineRiskMode.DEFENSIVE:
                                defensive_trades += 1
                    pending = None

                if config.market == "futures":
                    mark = marks_by_hour[hourly.open_time]
                    hour_end = hourly.close_time or hourly.open_time + HOUR
                    cash_or_wallet = self._apply_funding(
                        config=config,
                        position=position,
                        events=tuple(
                            rate
                            for rate in hour_funding
                            if hourly.open_time < rate.funding_time <= hour_end
                        ),
                        mark_open=mark.open,
                        cash_or_wallet=cash_or_wallet,
                    )
                    last_mark = mark.close
                    if position is not None:
                        position.holding_hours += 1
                        if self._liquidated_intrahour(position, mark):
                            trigger = (
                                min(mark.open, position.liquidation_price or mark.low)
                                if position.side is PositionSide.LONG
                                else max(
                                    mark.open,
                                    position.liquidation_price or mark.high,
                                )
                            )
                            cash_or_wallet, closed = self._close_position(
                                config=config,
                                position=position,
                                reference_price=trigger,
                                closed_at=mark.close_time,
                                reason=TrendFollowingExitReason.LIQUIDATION,
                                cash_or_wallet=cash_or_wallet,
                                state=state,
                                macro_exit_true=False,
                                donchian_exit_true=False,
                            )
                            trades.append(closed)
                            position = None
                            pending = None
                            kill_date = current_date
                            warnings.append("UNEXPECTED_LIQUIDATION_AT_1X")
                            reasons[TrendFollowingExitReason.LIQUIDATION.value] += 1
                elif position is not None:
                    position.holding_hours += 1

            if pending is not None and pending.execute_date == current_date and not hours:
                reasons["NO_ELIGIBLE_NEXT_DAY_OPEN"] += 1
                pending = None

            daily = daily_by_date.get(current_date)
            if daily is not None:
                index = daily_index[current_date]
                decision = decision_engine.evaluate(
                    daily_candles[: index + 1],
                    position_side=position.side if position is not None else None,
                )
                if decision.reason_code is not TrendFollowingReasonCode.WARMUP_INCOMPLETE:
                    if effective_start is None:
                        effective_start = daily.open_time
                    evaluated += 1
                    if position is not None:
                        exposed_days += 1
                    if state.mode is EngineRiskMode.DEFENSIVE:
                        state.days_defensive += 1

                    (
                        pending,
                        trace,
                        signal_created,
                    ) = self._daily_decision(
                        config=config,
                        candle=daily,
                        position=position,
                        state=state,
                        equity=self._equity(config, cash_or_wallet, position, daily.close),
                        decision=decision,
                    )
                    traces.append(trace)
                    reasons[trace.reason_code] += 1
                    entry_signals += int(signal_created)
                else:
                    reasons["WARMUP_INCOMPLETE"] += 1

                current_equity = self._equity(
                    config,
                    cash_or_wallet,
                    position,
                    last_mark if config.market == "futures" and last_mark else daily.close,
                )
                state.observe_equity(current_equity, daily.close_time or daily.open_time)
                equity_curve.append(current_equity)

            if cursor == final_day and position is not None:
                reference = (
                    hours[-1].close
                    if hours
                    else (daily.close if daily is not None else position.entry_reference_price)
                )
                closed_at = (
                    daily.close_time
                    if daily is not None and daily.close_time is not None
                    else (
                        (hours[-1].close_time or hours[-1].open_time)
                        if hours
                        else config.evaluation_end
                    )
                )
                cash_or_wallet, closed = self._close_position(
                    config=config,
                    position=position,
                    reference_price=reference,
                    closed_at=closed_at,
                    reason=TrendFollowingExitReason.FORCED_END,
                    cash_or_wallet=cash_or_wallet,
                    state=state,
                    macro_exit_true=False,
                    donchian_exit_true=False,
                )
                trades.append(closed)
                position = None
                pending = None
                reasons[TrendFollowingExitReason.FORCED_END.value] += 1
                equity_curve.append(cash_or_wallet)
            cursor += DAY

        return self._result(
            config=config,
            effective_start=effective_start or config.evaluation_start,
            final_capital=cash_or_wallet,
            evaluated=evaluated,
            entry_signals=entry_signals,
            risk_approvals=risk_approvals,
            executions=executions,
            exposed_days=exposed_days,
            state=state,
            trades=tuple(trades),
            traces=tuple(traces),
            reasons=reasons,
            warnings=tuple(dict.fromkeys(warnings)),
            equity_curve=tuple(equity_curve),
            defensive_trades=defensive_trades,
        )

    @staticmethod
    def _validate_inputs(
        config: TrendFollowingEngineConfig,
        daily: tuple[Candle, ...],
        hourly: tuple[Candle | FuturesCandle, ...],
        marks: tuple[MarkPriceCandle, ...],
        funding: tuple[FundingRate, ...],
    ) -> None:
        if not daily or not hourly:
            raise ValueError("trend-following engine requires daily and hourly candles")
        if any(
            left.open_time >= right.open_time for left, right in zip(daily, daily[1:], strict=False)
        ):
            raise ValueError("daily candles must be strictly chronological")
        if any(
            left.open_time >= right.open_time
            for left, right in zip(hourly, hourly[1:], strict=False)
        ):
            raise ValueError("hourly candles must be strictly chronological")
        if any(candle.interval != "1d" for candle in daily):
            raise ValueError("signal candles must use interval 1d")
        if any(candle.interval != "1h" for candle in hourly):
            raise ValueError("execution candles must use interval 1h")
        if config.market == "spot" and (marks or funding):
            raise ValueError("Spot simulation does not accept mark or funding data")
        if config.market == "futures":
            if len(marks) != len(hourly):
                raise ValueError("Futures hourly candles and marks must align one-to-one")
            mark_times = tuple(item.open_time for item in marks)
            hour_times = tuple(item.open_time for item in hourly)
            if mark_times != hour_times:
                raise ValueError("Futures mark timestamps must exactly match 1h candles")
        if any(
            left.funding_time >= right.funding_time
            for left, right in zip(funding, funding[1:], strict=False)
        ):
            raise ValueError("funding events must be strictly chronological")

    @staticmethod
    def _allows_long(config: TrendFollowingEngineConfig) -> bool:
        return config.mode in {"long", "long-short"}

    @staticmethod
    def _allows_short(config: TrendFollowingEngineConfig) -> bool:
        return config.market == "futures" and config.mode in {"short", "long-short"}

    def _daily_decision(
        self,
        *,
        config: TrendFollowingEngineConfig,
        candle: Candle,
        position: _Position | None,
        state: _DefensiveState,
        equity: Decimal,
        decision: TrendFollowingDecision,
    ) -> tuple[_PendingAction | None, TrendFollowingDecisionTrace, bool]:
        if (
            decision.sma is None
            or decision.previous_entry_high is None
            or decision.previous_entry_low is None
            or decision.exit_channel_high is None
            or decision.exit_channel_low is None
        ):
            raise RuntimeError("evaluated trend decision lost its indicators")
        macro_side = {
            MacroTrendSide.ABOVE_SMA: "ABOVE",
            MacroTrendSide.BELOW_SMA: "BELOW",
            MacroTrendSide.AT_SMA: "EQUAL",
            MacroTrendSide.UNKNOWN: "UNKNOWN",
        }[decision.macro_side]
        pending: _PendingAction | None = None
        signal_created = decision.direction in {
            TrendFollowingDirection.ENTER_LONG,
            TrendFollowingDirection.ENTER_SHORT,
        }
        if decision.actionable:
            if decision.execute_at is None:
                raise RuntimeError("actionable trend decision lost execution timestamp")
            if signal_created:
                side = (
                    PositionSide.LONG
                    if decision.direction is TrendFollowingDirection.ENTER_LONG
                    else PositionSide.SHORT
                )
                if decision.initial_stop is None:
                    raise RuntimeError("trend entry lost its structural stop")
                pending = _PendingAction(
                    execute_date=decision.execute_at.date(),
                    action="ENTRY",
                    side=side,
                    initial_stop=decision.initial_stop,
                    reason=None,
                )
            else:
                primary = TrendFollowingExitReason(decision.reason_code.value)
                pending = _PendingAction(
                    execute_date=decision.execute_at.date(),
                    action="EXIT",
                    side=None,
                    initial_stop=None,
                    reason=primary,
                    macro_exit_true=decision.macro_exit_condition,
                    donchian_exit_true=decision.donchian_exit_condition,
                )
        exit_channel = (
            decision.exit_channel_low
            if position is not None and position.side is PositionSide.LONG
            else decision.exit_channel_high
            if position is not None
            else decision.initial_stop
        )

        trace = TrendFollowingDecisionTrace(
            market=config.market,
            mode=config.mode,
            variant_id=config.variant_id,
            period=config.period,
            scenario=config.scenario,
            date=candle.open_time,
            close=candle.close,
            sma_200=decision.sma,
            previous_20_high=decision.previous_entry_high,
            previous_20_low=decision.previous_entry_low,
            exit_channel=exit_channel,
            macro_side=macro_side,
            breakout_long=decision.breakout_long,
            breakout_short=decision.breakout_short,
            position_side=position.side.value if position is not None else "FLAT",
            risk_mode=state.mode.value,
            risk_percent=state.risk_percent,
            risk_budget=equity * state.risk_percent / HUNDRED,
            initial_stop=decision.initial_stop,
            risk_per_unit=(
                abs(candle.close - decision.initial_stop)
                if decision.initial_stop is not None
                else None
            ),
            quantity=None,
            macro_exit_true=decision.macro_exit_condition,
            donchian_exit_true=decision.donchian_exit_condition,
            reason_code=decision.reason_code.value,
        )
        return pending, trace, signal_created

    def _open_position(
        self,
        *,
        config: TrendFollowingEngineConfig,
        side: PositionSide,
        initial_stop: Decimal,
        reference_price: Decimal,
        opened_at: datetime,
        cash_or_wallet: Decimal,
        state: _DefensiveState,
    ) -> tuple[_Position | None, Decimal, str]:
        equity_before = cash_or_wallet
        sizing = size_position(
            PositionSizingRequest(
                market=(MarketType.SPOT if config.market == "spot" else MarketType.USD_M_FUTURES),
                side=side,
                equity=equity_before,
                available_balance=cash_or_wallet,
                reference_price=reference_price,
                initial_stop=initial_stop,
                risk_percent=state.risk_percent,
                maximum_position_percent=config.maximum_position_percent,
                taker_fee_bps=config.fee_bps,
                spread_bps=config.spread_bps,
                slippage_bps=config.slippage_bps,
                leverage=config.leverage,
                margin_buffer_percent=config.margin_buffer_percent,
                maximum_notional=config.maximum_notional,
                minimum_quantity=config.minimum_quantity,
                quantity_precision=self._step_precision(config.quantity_step),
            )
        )
        if not sizing.approved:
            return None, cash_or_wallet, sizing.reason_code.value
        effective = sizing.estimated_entry_price
        quantity = _quantity_floor(sizing.quantity, config.quantity_step)
        if quantity < config.minimum_quantity:
            return None, cash_or_wallet, "POSITION_SIZE_ZERO"
        notional = effective * quantity
        fee_rate = config.fee_bps / TEN_THOUSAND
        fee = notional * fee_rate
        if config.market == "spot" and notional + fee > cash_or_wallet:
            return None, cash_or_wallet, "CASH_INSUFFICIENT"
        if config.market == "futures":
            required_margin = notional / config.leverage
            margin_buffer = required_margin * config.margin_buffer_percent / HUNDRED
            if required_margin + margin_buffer + fee > cash_or_wallet:
                return None, cash_or_wallet, "MARGIN_INSUFFICIENT"
        execution_cost = abs(effective - reference_price) * quantity
        if config.market == "spot":
            cash_or_wallet -= notional + fee
        else:
            cash_or_wallet -= fee
        liquidation_price = (
            approximate_liquidation_price(
                side,
                effective,
                config.leverage,
                config.maintenance_margin_rate,
            )
            if config.market == "futures"
            else None
        )
        return (
            _Position(
                side=side,
                entry_time=opened_at,
                entry_reference_price=reference_price,
                entry_price=effective,
                quantity=quantity,
                initial_stop=initial_stop,
                entry_fee=fee,
                entry_execution_cost=execution_cost,
                equity_before=equity_before,
                risk_mode_at_entry=state.mode,
                risk_percent=sizing.risk_percent,
                risk_budget=sizing.risk_budget,
                consecutive_losses_before=state.consecutive_losses,
                recovery_target_before=state.recovery_target,
                liquidation_price=liquidation_price,
            ),
            cash_or_wallet,
            "POSITION_SIZE_APPROVED",
        )

    @staticmethod
    def _step_precision(step: Decimal) -> int:
        exponent = step.normalize().as_tuple().exponent
        if not isinstance(exponent, int):
            raise ValueError("quantity_step must be finite")
        return max(0, -exponent)

    @staticmethod
    def _apply_funding(
        *,
        config: TrendFollowingEngineConfig,
        position: _Position | None,
        events: tuple[FundingRate, ...],
        mark_open: Decimal,
        cash_or_wallet: Decimal,
    ) -> Decimal:
        if position is None or not config.funding_enabled:
            return cash_or_wallet
        for event in events:
            mark_for_funding = event.mark_price or mark_open
            flow = funding_cash_flow(
                position.side,
                mark_for_funding * position.quantity,
                event.funding_rate,
            )
            cash_or_wallet += flow
            position.funding += flow
        return cash_or_wallet

    @staticmethod
    def _liquidated_at_open(position: _Position, mark_open: Decimal) -> bool:
        if position.liquidation_price is None:
            return False
        if position.side is PositionSide.LONG:
            return mark_open <= position.liquidation_price
        return mark_open >= position.liquidation_price

    @staticmethod
    def _liquidated_intrahour(
        position: _Position,
        mark: MarkPriceCandle,
    ) -> bool:
        if position.liquidation_price is None:
            return False
        if position.side is PositionSide.LONG:
            return mark.low <= position.liquidation_price
        return mark.high >= position.liquidation_price

    def _close_position(
        self,
        *,
        config: TrendFollowingEngineConfig,
        position: _Position,
        reference_price: Decimal,
        closed_at: datetime,
        reason: TrendFollowingExitReason,
        cash_or_wallet: Decimal,
        state: _DefensiveState,
        macro_exit_true: bool,
        donchian_exit_true: bool,
    ) -> tuple[Decimal, TrendFollowingClosedTrade]:
        buying = position.side is PositionSide.SHORT
        effective = _effective_price(
            reference_price,
            buying=buying,
            spread_bps=config.spread_bps,
            slippage_bps=config.slippage_bps,
        )
        exit_fee = effective * position.quantity * config.fee_bps / TEN_THOUSAND
        raw_gross = (
            (reference_price - position.entry_reference_price) * position.quantity
            if position.side is PositionSide.LONG
            else (position.entry_reference_price - reference_price) * position.quantity
        )
        fill_gross = (
            (effective - position.entry_price) * position.quantity
            if position.side is PositionSide.LONG
            else (position.entry_price - effective) * position.quantity
        )
        liquidation_fee = (
            effective * position.quantity * config.liquidation_fee_rate
            if reason is TrendFollowingExitReason.LIQUIDATION
            else ZERO
        )
        if config.market == "spot":
            cash_or_wallet += (
                effective * position.quantity - exit_fee + position.funding - liquidation_fee
            )
            net_pnl = (
                fill_gross - position.entry_fee - exit_fee + position.funding - liquidation_fee
            )
        else:
            net_pnl = (
                fill_gross - position.entry_fee - exit_fee + position.funding - liquidation_fee
            )
            cash_or_wallet += fill_gross - exit_fee - liquidation_fee
        state.record_close(
            reason=reason,
            net_pnl=net_pnl,
            equity_before_trade=position.equity_before,
            equity_after=cash_or_wallet,
            closed_at=closed_at,
        )
        funding_paid = -min(position.funding, ZERO)
        funding_received = max(position.funding, ZERO)
        exit_execution = abs(effective - reference_price) * position.quantity
        trade = TrendFollowingClosedTrade(
            market=config.market,
            mode=config.mode,
            variant_id=config.variant_id,
            period=config.period,
            scenario=config.scenario,
            side=position.side.value,
            entry_time=position.entry_time,
            exit_time=closed_at,
            entry_reference_price=position.entry_reference_price,
            exit_reference_price=reference_price,
            entry_price=position.entry_price,
            exit_price=effective,
            quantity=position.quantity,
            initial_stop=position.initial_stop,
            risk_mode_at_entry=position.risk_mode_at_entry.value,
            risk_percent=position.risk_percent,
            risk_budget=position.risk_budget,
            consecutive_losses_before=position.consecutive_losses_before,
            consecutive_losses_after=state.consecutive_losses,
            recovery_target_before=position.recovery_target_before,
            recovery_target_after=state.recovery_target,
            equity_before=position.equity_before,
            equity_after=cash_or_wallet,
            defensive_activated_at=state.activated_at,
            defensive_recovered_at=state.recovered_at,
            gross_pnl=raw_gross,
            fees=position.entry_fee + exit_fee,
            execution_costs=position.entry_execution_cost + exit_execution,
            funding_paid=funding_paid,
            funding_received=funding_received,
            net_funding=position.funding,
            liquidation_fee=liquidation_fee,
            net_pnl=net_pnl,
            holding_hours=position.holding_hours,
            exit_reason=reason.value,
            macro_exit_true=macro_exit_true,
            donchian_exit_true=donchian_exit_true,
            liquidated=reason is TrendFollowingExitReason.LIQUIDATION,
        )
        return cash_or_wallet, trade

    @staticmethod
    def _equity(
        config: TrendFollowingEngineConfig,
        cash_or_wallet: Decimal,
        position: _Position | None,
        price: Decimal,
    ) -> Decimal:
        if position is None:
            return cash_or_wallet
        if config.market == "spot":
            return cash_or_wallet + position.quantity * price
        return cash_or_wallet + unrealized_pnl(
            position.side,
            position.entry_price,
            price,
            position.quantity,
        )

    @staticmethod
    def _result(
        *,
        config: TrendFollowingEngineConfig,
        effective_start: datetime,
        final_capital: Decimal,
        evaluated: int,
        entry_signals: int,
        risk_approvals: int,
        executions: int,
        exposed_days: int,
        state: _DefensiveState,
        trades: tuple[TrendFollowingClosedTrade, ...],
        traces: tuple[TrendFollowingDecisionTrace, ...],
        reasons: Counter[str],
        warnings: tuple[str, ...],
        equity_curve: tuple[Decimal, ...],
        defensive_trades: int,
    ) -> TrendFollowingRun:
        pnl = tuple(trade.net_pnl for trade in trades)
        gross_pnl = sum((trade.gross_pnl for trade in trades), ZERO)
        net_pnl = final_capital - config.initial_capital
        wins = tuple(value for value in pnl if value > ZERO)
        losses = tuple(value for value in pnl if value < ZERO)
        profit_factor = sum(wins, ZERO) / abs(sum(losses, ZERO)) if losses else None
        drawdown = _maximum_drawdown(equity_curve)
        best_concentration, top_three, without_best, without_three = _concentration(trades)
        net_return = net_pnl / config.initial_capital * HUNDRED
        return TrendFollowingRun(
            market=config.market,
            mode=config.mode,
            variant_id=config.variant_id,
            period=config.period,
            scenario=config.scenario,
            evaluation_start=config.evaluation_start,
            evaluation_end=config.evaluation_end,
            effective_evaluation_start=effective_start,
            initial_capital=config.initial_capital,
            final_capital=final_capital,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            gross_return_percent=gross_pnl / config.initial_capital * HUNDRED,
            net_return_percent=net_return,
            win_rate_percent=(
                Decimal(len(wins)) / Decimal(len(trades)) * HUNDRED if trades else ZERO
            ),
            profit_factor=profit_factor,
            expectancy=sum(pnl, ZERO) / Decimal(len(pnl)) if pnl else ZERO,
            median_trade_pnl=_median(pnl),
            maximum_drawdown_percent=drawdown,
            return_to_drawdown=net_return / drawdown if drawdown > ZERO else None,
            exposure_percent=(
                Decimal(exposed_days) / Decimal(evaluated) * HUNDRED if evaluated else ZERO
            ),
            fees=sum((trade.fees for trade in trades), ZERO),
            execution_costs=sum((trade.execution_costs for trade in trades), ZERO),
            funding_paid=sum((trade.funding_paid for trade in trades), ZERO),
            funding_received=sum((trade.funding_received for trade in trades), ZERO),
            net_funding=sum((trade.net_funding for trade in trades), ZERO),
            liquidation_count=sum(trade.liquidated for trade in trades),
            evaluated_daily_candles=evaluated,
            entry_signals=entry_signals,
            risk_approvals=risk_approvals,
            executions=executions,
            long_trades=sum(trade.side == PositionSide.LONG.value for trade in trades),
            short_trades=sum(trade.side == PositionSide.SHORT.value for trade in trades),
            defensive_mode_activations=state.activations,
            candles_in_defensive_mode=state.days_defensive,
            trades_in_defensive_mode=defensive_trades,
            risk_reduction_duration_days=state.days_defensive,
            net_pnl_without_best_trade=without_best,
            net_pnl_without_top_three=without_three,
            best_trade_concentration_percent=best_concentration,
            top_three_concentration_percent=top_three,
            reason_counts=tuple(sorted(reasons.items())),
            warnings=warnings,
            trades=trades,
            traces=traces,
        )
