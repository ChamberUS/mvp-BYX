"""Point-in-time pullback-continuation analyzer for research-only Spot use."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, TypedDict

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.domain.models import (
    Candle,
    MarketContext,
    MarketRegime,
    MarketSignal,
    SignalDirection,
)
from adaptive_trader.strategy.regime import (
    DeterministicRegimeClassifier,
    RegimeResult,
)


class PullbackReasonCode(StrEnum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    TREND_NOT_ESTABLISHED = "TREND_NOT_ESTABLISHED"
    TREND_PERSISTENCE_TOO_SHORT = "TREND_PERSISTENCE_TOO_SHORT"
    NO_PULLBACK = "NO_PULLBACK"
    PULLBACK_TOO_SHALLOW = "PULLBACK_TOO_SHALLOW"
    PULLBACK_TOO_DEEP = "PULLBACK_TOO_DEEP"
    PULLBACK_TOO_OLD = "PULLBACK_TOO_OLD"
    PRICE_CROSSED_LONG_EMA = "PRICE_CROSSED_LONG_EMA"
    RESUMPTION_NOT_CONFIRMED = "RESUMPTION_NOT_CONFIRMED"
    DIRECTIONAL_CLOSE_REJECTED = "DIRECTIONAL_CLOSE_REJECTED"
    REGIME_AFTER_PULLBACK_REJECTED = "REGIME_AFTER_PULLBACK_REJECTED"
    PRICE_OVEREXTENDED = "PRICE_OVEREXTENDED"
    VOLUME_REJECTED = "VOLUME_REJECTED"
    VOLATILITY_REJECTED = "VOLATILITY_REJECTED"
    ENTER_LONG_APPROVED = "ENTER_LONG_APPROVED"
    ENTER_SHORT_APPROVED = "ENTER_SHORT_APPROVED"
    REGIME_LOSS_EXIT = "REGIME_LOSS_EXIT"
    POSITION_MANAGED_BY_ENGINE = "POSITION_MANAGED_BY_ENGINE"


@dataclass(frozen=True, slots=True)
class PullbackParameters:
    trend_persistence_candles: int
    pullback_min_candles: int
    pullback_max_candles: int
    minimum_pullback_depth_atr: Decimal
    maximum_pullback_depth_atr: Decimal
    maximum_entry_extension_atr: Decimal
    minimum_volume_ratio: Decimal = Decimal("1")
    maximum_atr_relative: Decimal = Decimal("0.05")
    stop_atr_multiple: Decimal = Decimal("2")
    target_r_multiple: Decimal = Decimal("2")
    regime_loss_exit: bool = False
    directional_close_confirmation: bool = True

    def __post_init__(self) -> None:
        if self.trend_persistence_candles < 1:
            raise ValueError("trend_persistence_candles must be positive")
        if self.pullback_min_candles < 1:
            raise ValueError("pullback_min_candles must be positive")
        if self.pullback_max_candles < self.pullback_min_candles:
            raise ValueError("pullback maximum must not precede minimum")
        for name in (
            "minimum_pullback_depth_atr",
            "maximum_pullback_depth_atr",
            "maximum_entry_extension_atr",
            "minimum_volume_ratio",
            "maximum_atr_relative",
            "stop_atr_multiple",
            "target_r_multiple",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise TypeError(f"{name} must be a finite Decimal")
        if self.minimum_pullback_depth_atr < 0:
            raise ValueError("minimum pullback depth must not be negative")
        if self.maximum_pullback_depth_atr < self.minimum_pullback_depth_atr:
            raise ValueError("maximum pullback depth is below minimum")
        if (
            self.maximum_entry_extension_atr <= 0
            or self.maximum_atr_relative <= 0
            or self.stop_atr_multiple <= 0
            or self.target_r_multiple <= 0
        ):
            raise ValueError("pullback risk and volatility parameters must be positive")


@dataclass(frozen=True, slots=True)
class PullbackDecisionTrace:
    timestamp: datetime
    side: PositionSide | None
    regime: MarketRegime
    trend_confirmed: bool
    trend_persistence_count: int
    pullback_detected: bool
    pullback_valid: bool
    pullback_age: int
    pullback_depth_atr: Decimal | None
    resumed: bool
    overextended: bool
    ema_distance: Decimal
    price_to_short_ema: Decimal
    price_to_long_ema: Decimal
    atr_relative: Decimal
    volume_ratio: Decimal
    long_eligible: bool
    short_eligible: bool
    reason_code: PullbackReasonCode
    close_price: Decimal
    short_ema: Decimal
    long_ema: Decimal
    atr: Decimal
    previous_close: Decimal = Decimal("0")
    entry_extension_atr: Decimal | None = None
    regime_matched: bool = False
    ema_alignment: bool = False
    price_long_ema_side: bool = False
    persistence_valid: bool = False
    pullback_age_valid: bool = False
    pullback_depth_min_valid: bool = False
    pullback_depth_max_valid: bool = False
    long_ema_not_crossed: bool = False
    resumption_cross: bool = False
    directional_close_confirmation: bool = False
    entry_extension_valid: bool = False
    volume_valid: bool = False
    volatility_valid: bool = False
    signal_created: bool = False
    all_failure_codes: tuple[str, ...] = ()


@dataclass(slots=True)
class _ActivePullback:
    started_index: int
    persistence_at_start: int
    maximum_depth_atr: Decimal


@dataclass(frozen=True, slots=True)
class _SideEvaluation:
    eligible: bool
    trend_confirmed: bool
    persistence: int
    pullback_detected: bool
    pullback_valid: bool
    pullback_age: int
    depth_atr: Decimal | None
    resumed: bool
    overextended: bool
    reason: PullbackReasonCode
    entry_extension_atr: Decimal | None = None
    checks: tuple[tuple[str, bool], ...] = ()
    failure_codes: tuple[str, ...] = ()


class _SideCommon(TypedDict):
    trend_confirmed: bool
    persistence: int
    pullback_detected: bool
    pullback_valid: bool
    pullback_age: int
    depth_atr: Decimal | None
    resumed: bool


@dataclass(frozen=True, slots=True)
class PullbackEvaluation:
    direction: PositionSide | None
    trace: PullbackDecisionTrace


class RegimeClassifier(Protocol):
    def classify(self, candles: object) -> RegimeResult: ...


class PullbackContinuationCore:
    def __init__(self, parameters: PullbackParameters) -> None:
        self.parameters = parameters
        self._index = -1
        self._last_open_time: datetime | None = None
        self._long_persistence = 0
        self._short_persistence = 0
        self._long_pullback: _ActivePullback | None = None
        self._short_pullback: _ActivePullback | None = None

    def evaluate(
        self,
        *,
        latest: Candle,
        previous: Candle,
        regime: MarketRegime,
        short_ema: Decimal,
        long_ema: Decimal,
        atr_value: Decimal,
        volume_ratio: Decimal,
        allow_long: bool,
        allow_short: bool,
    ) -> PullbackEvaluation:
        if (
            self._last_open_time is None
            or latest.open_time > self._last_open_time
        ):
            self._index += 1
        else:
            self._reset()
            self._index = 0
        self._last_open_time = latest.open_time
        close = latest.close
        long_foundation = short_ema > long_ema and close > long_ema
        short_foundation = short_ema < long_ema and close < long_ema
        if regime is MarketRegime.TRENDING_UP and long_foundation:
            self._long_persistence += 1
        elif self._long_pullback is None or not long_foundation:
            self._long_persistence = 0
        if regime is MarketRegime.TRENDING_DOWN and short_foundation:
            self._short_persistence += 1
        elif self._short_pullback is None or not short_foundation:
            self._short_persistence = 0
        long_result = self._evaluate_side(
            side=PositionSide.LONG,
            enabled=allow_long,
            foundation=long_foundation,
            regime_matches=regime is MarketRegime.TRENDING_UP,
            close=close,
            previous_close=previous.close,
            short_ema=short_ema,
            long_ema=long_ema,
            atr_value=atr_value,
            volume_ratio=volume_ratio,
        )
        short_result = self._evaluate_side(
            side=PositionSide.SHORT,
            enabled=allow_short,
            foundation=short_foundation,
            regime_matches=regime is MarketRegime.TRENDING_DOWN,
            close=close,
            previous_close=previous.close,
            short_ema=short_ema,
            long_ema=long_ema,
            atr_value=atr_value,
            volume_ratio=volume_ratio,
        )
        direction = (
            PositionSide.LONG
            if long_result.eligible
            else PositionSide.SHORT
            if short_result.eligible
            else None
        )
        selected = (
            long_result
            if direction is PositionSide.LONG
            else short_result
            if direction is PositionSide.SHORT
            else short_result
            if allow_long
            and allow_short
            and (
                regime is MarketRegime.TRENDING_DOWN
                or (
                    self._short_pullback is not None
                    and self._long_pullback is None
                )
            )
            else long_result
            if allow_long
            else short_result
        )
        reason = (
            PullbackReasonCode.ENTER_LONG_APPROVED
            if direction is PositionSide.LONG
            else PullbackReasonCode.ENTER_SHORT_APPROVED
            if direction is PositionSide.SHORT
            else selected.reason
        )
        trace = PullbackDecisionTrace(
            timestamp=latest.close_time or latest.open_time,
            side=direction,
            regime=regime,
            trend_confirmed=selected.trend_confirmed,
            trend_persistence_count=selected.persistence,
            pullback_detected=selected.pullback_detected,
            pullback_valid=selected.pullback_valid,
            pullback_age=selected.pullback_age,
            pullback_depth_atr=selected.depth_atr,
            resumed=selected.resumed,
            overextended=selected.overextended,
            ema_distance=short_ema - long_ema,
            price_to_short_ema=close - short_ema,
            price_to_long_ema=close - long_ema,
            atr_relative=atr_value / close,
            volume_ratio=volume_ratio,
            long_eligible=long_result.eligible,
            short_eligible=short_result.eligible,
            reason_code=reason,
            close_price=close,
            short_ema=short_ema,
            long_ema=long_ema,
            atr=atr_value,
            previous_close=previous.close,
            entry_extension_atr=selected.entry_extension_atr,
            regime_matched=dict(selected.checks).get("regime_matched", False),
            ema_alignment=dict(selected.checks).get("ema_alignment", False),
            price_long_ema_side=dict(selected.checks).get(
                "price_long_ema_side", False
            ),
            persistence_valid=dict(selected.checks).get(
                "trend_persistence", False
            ),
            pullback_age_valid=dict(selected.checks).get(
                "pullback_age_valid", False
            ),
            pullback_depth_min_valid=dict(selected.checks).get(
                "pullback_depth_min", False
            ),
            pullback_depth_max_valid=dict(selected.checks).get(
                "pullback_depth_max", False
            ),
            long_ema_not_crossed=dict(selected.checks).get(
                "long_ema_not_crossed", False
            ),
            resumption_cross=dict(selected.checks).get(
                "resumption_cross", False
            ),
            directional_close_confirmation=dict(selected.checks).get(
                "directional_close_confirmation", False
            ),
            entry_extension_valid=dict(selected.checks).get(
                "entry_extension_valid", False
            ),
            volume_valid=dict(selected.checks).get("volume_valid", False),
            volatility_valid=dict(selected.checks).get(
                "volatility_valid", False
            ),
            signal_created=selected.eligible,
            all_failure_codes=selected.failure_codes,
        )
        return PullbackEvaluation(direction=direction, trace=trace)

    def _evaluate_side(
        self,
        *,
        side: PositionSide,
        enabled: bool,
        foundation: bool,
        regime_matches: bool,
        close: Decimal,
        previous_close: Decimal,
        short_ema: Decimal,
        long_ema: Decimal,
        atr_value: Decimal,
        volume_ratio: Decimal,
    ) -> _SideEvaluation:
        if not enabled:
            return self._side_result(PullbackReasonCode.TREND_NOT_ESTABLISHED)
        persistence = (
            self._long_persistence
            if side is PositionSide.LONG
            else self._short_persistence
        )
        active = (
            self._long_pullback
            if side is PositionSide.LONG
            else self._short_pullback
        )
        crossed_long = (
            close <= long_ema
            if side is PositionSide.LONG
            else close >= long_ema
        )
        base_checks = (
            # A pullback is stateful: the regime requirement is locked when the
            # pullback starts. Requiring the classifier to return to TRENDING on
            # the very resumption candle made the pullback itself invalidate the
            # already-established setup and duplicated the persistence rule.
            ("regime_matched", regime_matches or active is not None),
            (
                "ema_alignment",
                short_ema > long_ema
                if side is PositionSide.LONG
                else short_ema < long_ema,
            ),
            ("price_long_ema_side", not crossed_long),
            (
                "trend_persistence",
                persistence >= self.parameters.trend_persistence_candles
                or active is not None,
            ),
            ("long_ema_not_crossed", not crossed_long),
        )
        if crossed_long:
            self._set_active(side, None)
            return self._side_result(
                PullbackReasonCode.PRICE_CROSSED_LONG_EMA,
                persistence=persistence,
                checks=base_checks,
            )
        if not foundation:
            self._set_active(side, None)
            return self._side_result(
                PullbackReasonCode.TREND_NOT_ESTABLISHED,
                persistence=persistence,
                checks=base_checks,
            )
        if (
            active is None
            and persistence < self.parameters.trend_persistence_candles
        ):
            return self._side_result(
                PullbackReasonCode.TREND_PERSISTENCE_TOO_SHORT,
                persistence=persistence,
                checks=base_checks,
            )
        on_pullback_side = (
            close <= short_ema
            if side is PositionSide.LONG
            else close >= short_ema
        )
        depth = (
            (short_ema - close) / atr_value
            if side is PositionSide.LONG
            else (close - short_ema) / atr_value
        )
        if active is None:
            if not on_pullback_side:
                return self._side_result(
                    PullbackReasonCode.NO_PULLBACK,
                    trend_confirmed=True,
                    persistence=persistence,
                    checks=base_checks,
                )
            if depth < self.parameters.minimum_pullback_depth_atr:
                return self._side_result(
                    PullbackReasonCode.PULLBACK_TOO_SHALLOW,
                    trend_confirmed=True,
                    persistence=persistence,
                    pullback_detected=True,
                    depth_atr=depth,
                    checks=(
                        *base_checks,
                        ("pullback_depth_min", False),
                        ("pullback_depth_max", True),
                    ),
                )
            if depth > self.parameters.maximum_pullback_depth_atr:
                return self._side_result(
                    PullbackReasonCode.PULLBACK_TOO_DEEP,
                    trend_confirmed=True,
                    persistence=persistence,
                    pullback_detected=True,
                    depth_atr=depth,
                    checks=(
                        *base_checks,
                        ("pullback_depth_min", True),
                        ("pullback_depth_max", False),
                    ),
                )
            active = _ActivePullback(
                started_index=self._index,
                persistence_at_start=persistence,
                maximum_depth_atr=depth,
            )
            self._set_active(side, active)
            return self._side_result(
                PullbackReasonCode.RESUMPTION_NOT_CONFIRMED,
                trend_confirmed=True,
                persistence=persistence,
                pullback_detected=True,
                pullback_valid=True,
                pullback_age=1,
                depth_atr=depth,
                checks=(
                    *base_checks,
                    ("pullback_age_valid", True),
                    ("pullback_depth_min", True),
                    ("pullback_depth_max", True),
                ),
            )
        age = (
            self._index - active.started_index + 1
            if on_pullback_side
            else self._index - active.started_index
        )
        if age > self.parameters.pullback_max_candles:
            self._set_active(side, None)
            return self._side_result(
                PullbackReasonCode.PULLBACK_TOO_OLD,
                trend_confirmed=True,
                persistence=active.persistence_at_start,
                pullback_detected=True,
                pullback_age=age,
                depth_atr=active.maximum_depth_atr,
                checks=(
                    *base_checks,
                    ("pullback_age_valid", False),
                    ("pullback_depth_min", True),
                    ("pullback_depth_max", True),
                ),
            )
        if on_pullback_side:
            active.maximum_depth_atr = max(active.maximum_depth_atr, depth)
            if depth > self.parameters.maximum_pullback_depth_atr:
                self._set_active(side, None)
                return self._side_result(
                    PullbackReasonCode.PULLBACK_TOO_DEEP,
                    trend_confirmed=True,
                    persistence=active.persistence_at_start,
                    pullback_detected=True,
                    pullback_age=age,
                    depth_atr=active.maximum_depth_atr,
                    checks=(
                        *base_checks,
                        ("pullback_age_valid", True),
                        ("pullback_depth_min", True),
                        ("pullback_depth_max", False),
                    ),
                )
            return self._side_result(
                PullbackReasonCode.RESUMPTION_NOT_CONFIRMED,
                trend_confirmed=True,
                persistence=active.persistence_at_start,
                pullback_detected=True,
                pullback_valid=True,
                pullback_age=age,
                depth_atr=active.maximum_depth_atr,
                checks=(
                    *base_checks,
                    ("pullback_age_valid", True),
                    ("pullback_depth_min", True),
                    ("pullback_depth_max", True),
                ),
            )
        resumption_cross = (
            close > short_ema
            if side is PositionSide.LONG
            else close < short_ema
        )
        directional_close = (
            close > previous_close
            if side is PositionSide.LONG
            else close < previous_close
        )
        resumed = resumption_cross
        common: _SideCommon = {
            "trend_confirmed": True,
            "persistence": active.persistence_at_start,
            "pullback_detected": True,
            "pullback_valid": True,
            "pullback_age": age,
            "depth_atr": active.maximum_depth_atr,
            "resumed": resumed,
        }
        extension = (
            (close - long_ema) / atr_value
            if side is PositionSide.LONG
            else (long_ema - close) / atr_value
        )
        checks = (
            *base_checks,
            (
                "pullback_age_valid",
                self.parameters.pullback_min_candles
                <= age
                <= self.parameters.pullback_max_candles,
            ),
            (
                "pullback_depth_min",
                active.maximum_depth_atr
                >= self.parameters.minimum_pullback_depth_atr,
            ),
            (
                "pullback_depth_max",
                active.maximum_depth_atr
                <= self.parameters.maximum_pullback_depth_atr,
            ),
            ("resumption_cross", resumption_cross),
            ("directional_close_confirmation", directional_close),
            (
                "entry_extension_valid",
                extension <= self.parameters.maximum_entry_extension_atr,
            ),
            (
                "volume_valid",
                volume_ratio >= self.parameters.minimum_volume_ratio,
            ),
            (
                "volatility_valid",
                atr_value / close <= self.parameters.maximum_atr_relative,
            ),
        )
        failure_codes = tuple(
            code
            for name, passed, code in (
                (
                    "resumption_cross",
                    resumption_cross,
                    PullbackReasonCode.RESUMPTION_NOT_CONFIRMED.value,
                ),
                (
                    "directional_close_confirmation",
                    directional_close
                    or not self.parameters.directional_close_confirmation,
                    PullbackReasonCode.DIRECTIONAL_CLOSE_REJECTED.value,
                ),
                (
                    "regime_matched",
                    regime_matches or active is not None,
                    PullbackReasonCode.REGIME_AFTER_PULLBACK_REJECTED.value,
                ),
                (
                    "entry_extension_valid",
                    extension <= self.parameters.maximum_entry_extension_atr,
                    PullbackReasonCode.PRICE_OVEREXTENDED.value,
                ),
                (
                    "volume_valid",
                    volume_ratio >= self.parameters.minimum_volume_ratio,
                    PullbackReasonCode.VOLUME_REJECTED.value,
                ),
                (
                    "volatility_valid",
                    atr_value / close <= self.parameters.maximum_atr_relative,
                    PullbackReasonCode.VOLATILITY_REJECTED.value,
                ),
            )
            if not passed
        )
        if age < self.parameters.pullback_min_candles:
            return self._side_result(
                PullbackReasonCode.PULLBACK_TOO_SHALLOW,
                entry_extension_atr=extension,
                checks=checks,
                failure_codes=failure_codes,
                **common,
            )
        if not resumption_cross:
            return self._side_result(
                PullbackReasonCode.RESUMPTION_NOT_CONFIRMED,
                entry_extension_atr=extension,
                checks=checks,
                failure_codes=failure_codes,
                **common,
            )
        if (
            self.parameters.directional_close_confirmation
            and not directional_close
        ):
            return self._side_result(
                PullbackReasonCode.DIRECTIONAL_CLOSE_REJECTED,
                entry_extension_atr=extension,
                checks=checks,
                failure_codes=failure_codes,
                **common,
            )
        if extension > self.parameters.maximum_entry_extension_atr:
            self._set_active(side, None)
            return self._side_result(
                PullbackReasonCode.PRICE_OVEREXTENDED,
                overextended=True,
                entry_extension_atr=extension,
                checks=checks,
                failure_codes=failure_codes,
                **common,
            )
        if volume_ratio < self.parameters.minimum_volume_ratio:
            return self._side_result(
                PullbackReasonCode.VOLUME_REJECTED,
                entry_extension_atr=extension,
                checks=checks,
                failure_codes=failure_codes,
                **common,
            )
        if atr_value / close > self.parameters.maximum_atr_relative:
            return self._side_result(
                PullbackReasonCode.VOLATILITY_REJECTED,
                entry_extension_atr=extension,
                checks=checks,
                failure_codes=failure_codes,
                **common,
            )
        self._set_active(side, None)
        return self._side_result(
            (
                PullbackReasonCode.ENTER_LONG_APPROVED
                if side is PositionSide.LONG
                else PullbackReasonCode.ENTER_SHORT_APPROVED
            ),
            eligible=True,
            entry_extension_atr=extension,
            checks=checks,
            failure_codes=(),
            **common,
        )

    @staticmethod
    def _side_result(
        reason: PullbackReasonCode,
        *,
        eligible: bool = False,
        trend_confirmed: bool = False,
        persistence: int = 0,
        pullback_detected: bool = False,
        pullback_valid: bool = False,
        pullback_age: int = 0,
        depth_atr: Decimal | None = None,
        resumed: bool = False,
        overextended: bool = False,
        entry_extension_atr: Decimal | None = None,
        checks: tuple[tuple[str, bool], ...] = (),
        failure_codes: tuple[str, ...] = (),
    ) -> _SideEvaluation:
        return _SideEvaluation(
            eligible=eligible,
            trend_confirmed=trend_confirmed,
            persistence=persistence,
            pullback_detected=pullback_detected,
            pullback_valid=pullback_valid,
            pullback_age=pullback_age,
            depth_atr=depth_atr,
            resumed=resumed,
            overextended=overextended,
            reason=reason,
            entry_extension_atr=entry_extension_atr,
            checks=checks,
            failure_codes=failure_codes,
        )

    def _set_active(
        self,
        side: PositionSide,
        value: _ActivePullback | None,
    ) -> None:
        if side is PositionSide.LONG:
            self._long_pullback = value
        else:
            self._short_pullback = value

    def _reset(self) -> None:
        self._long_persistence = 0
        self._short_persistence = 0
        self._long_pullback = None
        self._short_pullback = None


class PullbackContinuationAnalyzer:
    def __init__(
        self,
        parameters: PullbackParameters,
        *,
        short_period: int = 20,
        long_period: int = 50,
        classifier: RegimeClassifier | None = None,
    ) -> None:
        self.parameters = parameters
        self._classifier = classifier or DeterministicRegimeClassifier(
            short_period=short_period,
            long_period=long_period,
            maximum_atr_relative=parameters.maximum_atr_relative,
        )
        self._core = PullbackContinuationCore(parameters)
        self._traces: list[PullbackDecisionTrace] = []
        self._entry_active = False

    @property
    def traces(self) -> tuple[PullbackDecisionTrace, ...]:
        return tuple(self._traces)

    def analyze(self, context: MarketContext) -> MarketSignal:
        indicators = context.indicators
        required = ("ema_short", "ema_long", "atr", "volume_ratio")
        if len(context.candles) < 2 or any(name not in indicators for name in required):
            return self._hold(context, PullbackReasonCode.INSUFFICIENT_DATA)
        regime = self._classifier.classify(context.candles).regime
        if (
            self.parameters.regime_loss_exit
            and self._entry_active
            and regime is not MarketRegime.TRENDING_UP
        ):
            self._entry_active = False
            return self._exit(context, regime)
        evaluation = self._core.evaluate(
            latest=context.latest_candle,
            previous=context.candles[-2],
            regime=regime,
            short_ema=indicators["ema_short"],
            long_ema=indicators["ema_long"],
            atr_value=indicators["atr"],
            volume_ratio=indicators["volume_ratio"],
            allow_long=True,
            allow_short=False,
        )
        self._traces.append(evaluation.trace)
        if evaluation.direction is not PositionSide.LONG:
            return self._hold(context, evaluation.trace.reason_code, regime)
        close = context.latest_candle.close
        risk = indicators["atr"] * self.parameters.stop_atr_multiple
        self._entry_active = True
        return MarketSignal(
            signal_id=f"{context.symbol}-{context.latest_candle.open_time.isoformat()}-BUY",
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=SignalDirection.BUY,
            regime=regime,
            confidence=Decimal("0.75"),
            entry_price=close,
            stop_loss=close - risk,
            take_profit=close + risk * self.parameters.target_r_multiple,
            suggested_quantity=indicators.get("suggested_quantity", Decimal("0")),
            rationale="point-in-time pullback continuation long resumption",
            analyzer_name="pullback-continuation-v1",
            reason_code=PullbackReasonCode.ENTER_LONG_APPROVED,
        )

    def _exit(
        self,
        context: MarketContext,
        regime: MarketRegime,
    ) -> MarketSignal:
        close = context.latest_candle.close
        return MarketSignal(
            signal_id=(
                f"{context.symbol}-{context.latest_candle.open_time.isoformat()}-"
                "REGIME-LOSS-EXIT"
            ),
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=SignalDirection.SELL,
            regime=regime,
            confidence=Decimal("1"),
            entry_price=close,
            stop_loss=close,
            take_profit=close,
            suggested_quantity=max(
                context.indicators.get("suggested_quantity", Decimal("0")),
                Decimal("0.00000001"),
            ),
            rationale="trend regime ceased; delayed research-only Spot exit",
            analyzer_name="pullback-continuation-v1",
            reason_code=PullbackReasonCode.REGIME_LOSS_EXIT,
        )

    @staticmethod
    def _hold(
        context: MarketContext,
        reason: PullbackReasonCode,
        regime: MarketRegime = MarketRegime.UNKNOWN,
    ) -> MarketSignal:
        return MarketSignal(
            signal_id=f"{context.symbol}-{context.latest_candle.open_time.isoformat()}-HOLD",
            symbol=context.symbol,
            generated_at=context.created_at,
            direction=SignalDirection.HOLD,
            regime=regime,
            confidence=Decimal("0"),
            entry_price=context.latest_candle.close,
            stop_loss=Decimal("0"),
            take_profit=Decimal("0"),
            suggested_quantity=Decimal("0"),
            rationale=reason.value,
            analyzer_name="pullback-continuation-v1",
            reason_code=reason,
        )
