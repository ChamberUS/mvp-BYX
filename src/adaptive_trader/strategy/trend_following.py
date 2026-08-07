"""Pure point-in-time decisions for the preregistered daily trend strategy."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.indicators.trend_following import (
    PriceCandle,
    trend_following_indicators,
)


class TrendFollowingDirection(StrEnum):
    HOLD = "HOLD"
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT_LONG = "EXIT_LONG"
    EXIT_SHORT = "EXIT_SHORT"


class MacroTrendSide(StrEnum):
    ABOVE_SMA = "ABOVE_SMA"
    BELOW_SMA = "BELOW_SMA"
    AT_SMA = "AT_SMA"
    UNKNOWN = "UNKNOWN"


class TrendFollowingReasonCode(StrEnum):
    WARMUP_INCOMPLETE = "WARMUP_INCOMPLETE"
    MACRO_FILTER_LONG_REJECTED = "MACRO_FILTER_LONG_REJECTED"
    DONCHIAN_LONG_BREAKOUT_NOT_CONFIRMED = "DONCHIAN_LONG_BREAKOUT_NOT_CONFIRMED"
    POSITION_ALREADY_OPEN = "POSITION_ALREADY_OPEN"
    RISK_REJECTED = "RISK_REJECTED"
    ENTER_LONG_APPROVED = "ENTER_LONG_APPROVED"
    MACRO_FILTER_SHORT_REJECTED = "MACRO_FILTER_SHORT_REJECTED"
    DONCHIAN_SHORT_BREAKOUT_NOT_CONFIRMED = "DONCHIAN_SHORT_BREAKOUT_NOT_CONFIRMED"
    SHORT_NOT_ALLOWED = "SHORT_NOT_ALLOWED"
    ENTER_SHORT_APPROVED = "ENTER_SHORT_APPROVED"
    MACRO_FILTER_EXIT = "MACRO_FILTER_EXIT"
    DONCHIAN_EXIT_10 = "DONCHIAN_EXIT_10"
    DONCHIAN_EXIT_20 = "DONCHIAN_EXIT_20"
    FORCED_END = "FORCED_END"
    LIQUIDATION = "LIQUIDATION"
    ADMINISTRATIVE_EXIT = "ADMINISTRATIVE_EXIT"


@dataclass(frozen=True, slots=True)
class TrendFollowingParameters:
    sma_period: int = 200
    entry_period: int = 20
    exit_period: int = 20
    allow_long: bool = True
    allow_short: bool = False

    def __post_init__(self) -> None:
        for name in ("sma_period", "entry_period", "exit_period"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.exit_period not in {10, 20}:
            raise ValueError("exit_period must be either 10 or 20")
        if not self.allow_long and not self.allow_short:
            raise ValueError("at least one trend direction must be enabled")


@dataclass(frozen=True, slots=True)
class TrendFollowingDecision:
    timestamp: datetime
    execute_at: datetime | None
    symbol: str
    direction: TrendFollowingDirection
    reason_code: TrendFollowingReasonCode
    position_side: PositionSide | None
    close: Decimal
    sma: Decimal | None
    previous_entry_high: Decimal | None
    previous_entry_low: Decimal | None
    exit_channel_high: Decimal | None
    exit_channel_low: Decimal | None
    macro_side: MacroTrendSide
    macro_long: bool
    macro_short: bool
    breakout_long: bool
    breakout_short: bool
    macro_exit_condition: bool
    donchian_exit_condition: bool
    initial_stop: Decimal | None
    all_exit_conditions: tuple[TrendFollowingReasonCode, ...] = ()

    @property
    def actionable(self) -> bool:
        return self.direction is not TrendFollowingDirection.HOLD


class TrendFollowingDecisionEngine:
    """Evaluate exactly one closed daily prefix without retaining hidden state."""

    def __init__(self, parameters: TrendFollowingParameters | None = None) -> None:
        self.parameters = parameters or TrendFollowingParameters()

    def evaluate(
        self,
        candles: Sequence[PriceCandle],
        *,
        position_side: PositionSide | None = None,
    ) -> TrendFollowingDecision:
        self._validate_prefix(candles)
        latest = candles[-1]
        timestamp = latest.close_time or latest.open_time
        values = trend_following_indicators(
            candles,
            sma_period=self.parameters.sma_period,
            entry_period=self.parameters.entry_period,
            exit_period=self.parameters.exit_period,
        )
        if (
            values.sma is None
            or values.entry_channel is None
            or values.exit_channel is None
        ):
            return self._decision(
                latest=latest,
                timestamp=timestamp,
                direction=TrendFollowingDirection.HOLD,
                reason=TrendFollowingReasonCode.WARMUP_INCOMPLETE,
                position_side=position_side,
            )

        close = latest.close
        sma_value = values.sma
        entry = values.entry_channel
        exit_channel = values.exit_channel
        macro_side = (
            MacroTrendSide.ABOVE_SMA
            if close > sma_value
            else MacroTrendSide.BELOW_SMA
            if close < sma_value
            else MacroTrendSide.AT_SMA
        )
        macro_long = close > sma_value
        macro_short = close < sma_value
        breakout_long = close > entry.high
        breakout_short = close < entry.low

        if position_side is not None:
            macro_exit = (
                close < sma_value
                if position_side is PositionSide.LONG
                else close > sma_value
            )
            donchian_exit = (
                close < exit_channel.low
                if position_side is PositionSide.LONG
                else close > exit_channel.high
            )
            conditions = self._exit_conditions(macro_exit, donchian_exit)
            if conditions:
                return TrendFollowingDecision(
                    timestamp=timestamp,
                    execute_at=self._next_utc_day_open(latest.open_time),
                    symbol=latest.symbol,
                    direction=(
                        TrendFollowingDirection.EXIT_LONG
                        if position_side is PositionSide.LONG
                        else TrendFollowingDirection.EXIT_SHORT
                    ),
                    reason_code=conditions[0],
                    position_side=position_side,
                    close=close,
                    sma=sma_value,
                    previous_entry_high=entry.high,
                    previous_entry_low=entry.low,
                    exit_channel_high=exit_channel.high,
                    exit_channel_low=exit_channel.low,
                    macro_side=macro_side,
                    macro_long=macro_long,
                    macro_short=macro_short,
                    breakout_long=breakout_long,
                    breakout_short=breakout_short,
                    macro_exit_condition=macro_exit,
                    donchian_exit_condition=donchian_exit,
                    initial_stop=None,
                    all_exit_conditions=conditions,
                )
            return TrendFollowingDecision(
                timestamp=timestamp,
                execute_at=None,
                symbol=latest.symbol,
                direction=TrendFollowingDirection.HOLD,
                reason_code=TrendFollowingReasonCode.POSITION_ALREADY_OPEN,
                position_side=position_side,
                close=close,
                sma=sma_value,
                previous_entry_high=entry.high,
                previous_entry_low=entry.low,
                exit_channel_high=exit_channel.high,
                exit_channel_low=exit_channel.low,
                macro_side=macro_side,
                macro_long=macro_long,
                macro_short=macro_short,
                breakout_long=breakout_long,
                breakout_short=breakout_short,
                macro_exit_condition=False,
                donchian_exit_condition=False,
                initial_stop=None,
            )

        direction, reason, stop = self._entry_decision(
            macro_long=macro_long,
            macro_short=macro_short,
            breakout_long=breakout_long,
            breakout_short=breakout_short,
            exit_low=exit_channel.low,
            exit_high=exit_channel.high,
        )
        return TrendFollowingDecision(
            timestamp=timestamp,
            execute_at=(
                self._next_utc_day_open(latest.open_time)
                if direction is not TrendFollowingDirection.HOLD
                else None
            ),
            symbol=latest.symbol,
            direction=direction,
            reason_code=reason,
            position_side=None,
            close=close,
            sma=sma_value,
            previous_entry_high=entry.high,
            previous_entry_low=entry.low,
            exit_channel_high=exit_channel.high,
            exit_channel_low=exit_channel.low,
            macro_side=macro_side,
            macro_long=macro_long,
            macro_short=macro_short,
            breakout_long=breakout_long,
            breakout_short=breakout_short,
            macro_exit_condition=False,
            donchian_exit_condition=False,
            initial_stop=stop,
        )

    def _entry_decision(
        self,
        *,
        macro_long: bool,
        macro_short: bool,
        breakout_long: bool,
        breakout_short: bool,
        exit_low: Decimal,
        exit_high: Decimal,
    ) -> tuple[
        TrendFollowingDirection,
        TrendFollowingReasonCode,
        Decimal | None,
    ]:
        if macro_long:
            if not self.parameters.allow_long:
                return (
                    TrendFollowingDirection.HOLD,
                    TrendFollowingReasonCode.MACRO_FILTER_SHORT_REJECTED,
                    None,
                )
            if not breakout_long:
                return (
                    TrendFollowingDirection.HOLD,
                    TrendFollowingReasonCode.DONCHIAN_LONG_BREAKOUT_NOT_CONFIRMED,
                    None,
                )
            return (
                TrendFollowingDirection.ENTER_LONG,
                TrendFollowingReasonCode.ENTER_LONG_APPROVED,
                exit_low,
            )
        if macro_short:
            if not self.parameters.allow_short:
                return (
                    TrendFollowingDirection.HOLD,
                    (
                        TrendFollowingReasonCode.SHORT_NOT_ALLOWED
                        if breakout_short
                        else TrendFollowingReasonCode.MACRO_FILTER_LONG_REJECTED
                    ),
                    None,
                )
            if not breakout_short:
                return (
                    TrendFollowingDirection.HOLD,
                    TrendFollowingReasonCode.DONCHIAN_SHORT_BREAKOUT_NOT_CONFIRMED,
                    None,
                )
            return (
                TrendFollowingDirection.ENTER_SHORT,
                TrendFollowingReasonCode.ENTER_SHORT_APPROVED,
                exit_high,
            )
        return (
            TrendFollowingDirection.HOLD,
            (
                TrendFollowingReasonCode.MACRO_FILTER_LONG_REJECTED
                if self.parameters.allow_long
                else TrendFollowingReasonCode.MACRO_FILTER_SHORT_REJECTED
            ),
            None,
        )

    def _exit_conditions(
        self,
        macro_exit: bool,
        donchian_exit: bool,
    ) -> tuple[TrendFollowingReasonCode, ...]:
        reasons: list[TrendFollowingReasonCode] = []
        if macro_exit:
            reasons.append(TrendFollowingReasonCode.MACRO_FILTER_EXIT)
        if donchian_exit:
            reasons.append(
                TrendFollowingReasonCode.DONCHIAN_EXIT_10
                if self.parameters.exit_period == 10
                else TrendFollowingReasonCode.DONCHIAN_EXIT_20
            )
        return tuple(reasons)

    @staticmethod
    def _decision(
        *,
        latest: PriceCandle,
        timestamp: datetime,
        direction: TrendFollowingDirection,
        reason: TrendFollowingReasonCode,
        position_side: PositionSide | None,
    ) -> TrendFollowingDecision:
        return TrendFollowingDecision(
            timestamp=timestamp,
            execute_at=None,
            symbol=latest.symbol,
            direction=direction,
            reason_code=reason,
            position_side=position_side,
            close=latest.close,
            sma=None,
            previous_entry_high=None,
            previous_entry_low=None,
            exit_channel_high=None,
            exit_channel_low=None,
            macro_side=MacroTrendSide.UNKNOWN,
            macro_long=False,
            macro_short=False,
            breakout_long=False,
            breakout_short=False,
            macro_exit_condition=False,
            donchian_exit_condition=False,
            initial_stop=None,
        )

    @staticmethod
    def _next_utc_day_open(open_time: datetime) -> datetime:
        utc_day = open_time.astimezone(UTC).date() + timedelta(days=1)
        return datetime.combine(utc_day, time.min, tzinfo=UTC)

    @staticmethod
    def _validate_prefix(candles: Sequence[PriceCandle]) -> None:
        if not candles:
            raise ValueError("trend following requires daily candles")
        first = candles[0]
        previous: PriceCandle | None = None
        for candle in candles:
            if not candle.is_closed:
                raise ValueError("trend following accepts closed candles only")
            if candle.symbol != first.symbol or candle.interval != first.interval:
                raise ValueError("trend following candles must share symbol and interval")
            if candle.interval != "1d":
                raise ValueError("trend following requires 1d candles")
            if previous is not None and candle.open_time <= previous.open_time:
                raise ValueError("trend following candles must be strictly chronological")
            previous = candle


def evaluate_trend_following(
    candles: Sequence[PriceCandle],
    *,
    parameters: TrendFollowingParameters | None = None,
    position_side: PositionSide | None = None,
) -> TrendFollowingDecision:
    """Functional facade used by research runners and focused unit tests."""

    return TrendFollowingDecisionEngine(parameters).evaluate(
        candles,
        position_side=position_side,
    )
