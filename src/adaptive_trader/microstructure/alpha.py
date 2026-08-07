"""Independent research-only long/short alpha models and first-class NO_TRADE."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from statistics import median

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.microstructure.features import MicrostructureFeatureSnapshot
from adaptive_trader.microstructure.models import (
    AlphaDecisionStatus,
    AlphaModelName,
    CalibrationStatus,
    IntradayAlphaDecision,
    LiquiditySnapshot,
    LiquidityState,
    NoTradeReason,
)

ZERO = Decimal("0")


class LongAlphaReason(StrEnum):
    LONG_SPREAD_REJECTED = "LONG_SPREAD_REJECTED"
    LONG_DEPTH_REJECTED = "LONG_DEPTH_REJECTED"
    LONG_BOOK_IMBALANCE_REJECTED = "LONG_BOOK_IMBALANCE_REJECTED"
    LONG_OFI_REJECTED = "LONG_OFI_REJECTED"
    LONG_TRADE_FLOW_REJECTED = "LONG_TRADE_FLOW_REJECTED"
    LONG_MICROPRICE_REJECTED = "LONG_MICROPRICE_REJECTED"
    LONG_MOMENTUM_REJECTED = "LONG_MOMENTUM_REJECTED"
    LONG_PERSISTENCE_REJECTED = "LONG_PERSISTENCE_REJECTED"
    LONG_ALPHA_CONFIRMED = "LONG_ALPHA_CONFIRMED"


class ShortAlphaReason(StrEnum):
    SHORT_SPREAD_REJECTED = "SHORT_SPREAD_REJECTED"
    SHORT_DEPTH_REJECTED = "SHORT_DEPTH_REJECTED"
    SHORT_BOOK_IMBALANCE_REJECTED = "SHORT_BOOK_IMBALANCE_REJECTED"
    SHORT_OFI_REJECTED = "SHORT_OFI_REJECTED"
    SHORT_TRADE_FLOW_REJECTED = "SHORT_TRADE_FLOW_REJECTED"
    SHORT_MICROPRICE_REJECTED = "SHORT_MICROPRICE_REJECTED"
    SHORT_MOMENTUM_REJECTED = "SHORT_MOMENTUM_REJECTED"
    SHORT_PERSISTENCE_REJECTED = "SHORT_PERSISTENCE_REJECTED"
    SHORT_ALPHA_CONFIRMED = "SHORT_ALPHA_CONFIRMED"


@dataclass(frozen=True, slots=True)
class NoTradeGateConfig:
    maximum_event_age_ms: Decimal
    maximum_book_age_ms: Decimal
    maximum_spread_bps: Decimal
    minimum_top_20_notional: Decimal

    def __post_init__(self) -> None:
        for name in (
            "maximum_event_age_ms",
            "maximum_book_age_ms",
            "maximum_spread_bps",
            "minimum_top_20_notional",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= ZERO:
                raise ValueError(f"{name} must be a positive finite Decimal")


@dataclass(frozen=True, slots=True)
class GateContext:
    recent_gap: bool = False
    resync_in_progress: bool = False
    data_complete: bool = True
    replay_consistent: bool = True
    market_state_known: bool = True


class NoTradeGate:
    def __init__(self, config: NoTradeGateConfig) -> None:
        self.config = config

    def evaluate(
        self,
        liquidity: LiquiditySnapshot,
        features: MicrostructureFeatureSnapshot,
        context: GateContext | None = None,
    ) -> NoTradeReason | None:
        context = context or GateContext()
        if context.resync_in_progress:
            return NoTradeReason.RESYNC_IN_PROGRESS
        if context.recent_gap:
            return NoTradeReason.EVENT_GAP
        if not context.replay_consistent:
            return NoTradeReason.REPLAY_INCONSISTENT
        if not context.market_state_known:
            return NoTradeReason.MARKET_STATE_UNKNOWN
        if not context.data_complete:
            return NoTradeReason.NO_TRADE_ALLOWED
        if not liquidity.synchronized:
            return NoTradeReason.BOOK_NOT_SYNCHRONIZED
        if liquidity.best_bid >= liquidity.best_ask or liquidity.spread <= ZERO:
            return NoTradeReason.INVALID_SPREAD
        if liquidity.spread_bps > self.config.maximum_spread_bps:
            return NoTradeReason.INVALID_SPREAD
        if (
            features.event_age_ms > self.config.maximum_event_age_ms
            or liquidity.book_age_ms > self.config.maximum_book_age_ms
        ):
            return NoTradeReason.MARKET_DATA_STALE
        if min(liquidity.top_20_bid_notional, liquidity.top_20_ask_notional) < (
            self.config.minimum_top_20_notional
        ):
            return NoTradeReason.DEPTH_INSUFFICIENT
        if not features.warmup_complete:
            return NoTradeReason.FEATURE_WARMUP
        return None


@dataclass(frozen=True, slots=True)
class LongAlphaConfig:
    maximum_spread_bps: Decimal
    minimum_bid_notional: Decimal
    minimum_depth_imbalance: Decimal
    minimum_ofi: Decimal
    minimum_trade_imbalance: Decimal
    minimum_microprice_edge_bps: Decimal
    minimum_momentum_bps: Decimal
    minimum_persistence_ms: int
    calibration_status: CalibrationStatus = CalibrationStatus.CALIBRATION_REQUIRED

    def __post_init__(self) -> None:
        _validate_alpha_config(
            self.maximum_spread_bps,
            self.minimum_bid_notional,
            self.minimum_persistence_ms,
        )


@dataclass(frozen=True, slots=True)
class ShortAlphaConfig:
    maximum_spread_bps: Decimal
    minimum_ask_notional: Decimal
    maximum_depth_imbalance: Decimal
    maximum_ofi: Decimal
    maximum_trade_imbalance: Decimal
    maximum_microprice_edge_bps: Decimal
    maximum_momentum_bps: Decimal
    minimum_persistence_ms: int
    calibration_status: CalibrationStatus = CalibrationStatus.CALIBRATION_REQUIRED

    def __post_init__(self) -> None:
        _validate_alpha_config(
            self.maximum_spread_bps,
            self.minimum_ask_notional,
            self.minimum_persistence_ms,
        )


class LongMicrostructureAlpha:
    """Long-only state and thresholds; it cannot emit a short decision."""

    def __init__(self, gate: NoTradeGate, config: LongAlphaConfig) -> None:
        self.gate = gate
        self.config = config
        self._candidate_since: datetime | None = None

    @property
    def candidate_since(self) -> datetime | None:
        return self._candidate_since

    def evaluate(
        self,
        *,
        market: MarketType,
        liquidity: LiquiditySnapshot,
        features: MicrostructureFeatureSnapshot,
        context: GateContext | None = None,
    ) -> IntradayAlphaDecision:
        gate_reason = self.gate.evaluate(liquidity, features, context)
        if gate_reason is not None:
            self._candidate_since = None
            return _decision(
                model=AlphaModelName.LONG_MICROSTRUCTURE_V0,
                market=market,
                liquidity=liquidity,
                features=features,
                status=AlphaDecisionStatus.NO_TRADE,
                side=None,
                reasons=(gate_reason.value,),
                no_trade_reason=gate_reason,
            )
        reason = self._rejection(liquidity, features)
        if reason is not None:
            self._candidate_since = None
            return _decision(
                model=AlphaModelName.LONG_MICROSTRUCTURE_V0,
                market=market,
                liquidity=liquidity,
                features=features,
                status=AlphaDecisionStatus.HOLD,
                side=None,
                reasons=(reason.value,),
            )
        if self._candidate_since is None:
            self._candidate_since = features.timestamp
        elapsed_ms = (features.timestamp - self._candidate_since).total_seconds() * 1000
        if elapsed_ms < self.config.minimum_persistence_ms:
            return _decision(
                model=AlphaModelName.LONG_MICROSTRUCTURE_V0,
                market=market,
                liquidity=liquidity,
                features=features,
                status=AlphaDecisionStatus.HOLD,
                side=None,
                reasons=(LongAlphaReason.LONG_PERSISTENCE_REJECTED.value,),
            )
        return _decision(
            model=AlphaModelName.LONG_MICROSTRUCTURE_V0,
            market=market,
            liquidity=liquidity,
            features=features,
            status=AlphaDecisionStatus.LONG,
            side=PositionSide.LONG,
            reasons=(LongAlphaReason.LONG_ALPHA_CONFIRMED.value,),
        )

    def _rejection(
        self,
        liquidity: LiquiditySnapshot,
        features: MicrostructureFeatureSnapshot,
    ) -> LongAlphaReason | None:
        if features.spread_bps > self.config.maximum_spread_bps:
            return LongAlphaReason.LONG_SPREAD_REJECTED
        if liquidity.top_20_bid_notional < self.config.minimum_bid_notional:
            return LongAlphaReason.LONG_DEPTH_REJECTED
        if features.depth_imbalance_20 < self.config.minimum_depth_imbalance:
            return LongAlphaReason.LONG_BOOK_IMBALANCE_REJECTED
        if features.ofi_1s < self.config.minimum_ofi:
            return LongAlphaReason.LONG_OFI_REJECTED
        if (
            features.trade_flow_1s.aggressive_trade_imbalance
            < self.config.minimum_trade_imbalance
        ):
            return LongAlphaReason.LONG_TRADE_FLOW_REJECTED
        if features.microprice_edge_bps < self.config.minimum_microprice_edge_bps:
            return LongAlphaReason.LONG_MICROPRICE_REJECTED
        if features.momentum_1s_bps < self.config.minimum_momentum_bps:
            return LongAlphaReason.LONG_MOMENTUM_REJECTED
        return None


class ShortMicrostructureAlpha:
    """Futures-only short state with independent thresholds and reason codes."""

    def __init__(self, gate: NoTradeGate, config: ShortAlphaConfig) -> None:
        self.gate = gate
        self.config = config
        self._candidate_since: datetime | None = None

    @property
    def candidate_since(self) -> datetime | None:
        return self._candidate_since

    def evaluate(
        self,
        *,
        market: MarketType,
        liquidity: LiquiditySnapshot,
        features: MicrostructureFeatureSnapshot,
        context: GateContext | None = None,
    ) -> IntradayAlphaDecision:
        if market is not MarketType.USD_M_FUTURES:
            self._candidate_since = None
            return _decision(
                model=AlphaModelName.SHORT_MICROSTRUCTURE_V0,
                market=market,
                liquidity=liquidity,
                features=features,
                status=AlphaDecisionStatus.NO_TRADE,
                side=None,
                reasons=(NoTradeReason.NO_TRADE_ALLOWED.value,),
                no_trade_reason=NoTradeReason.NO_TRADE_ALLOWED,
            )
        gate_reason = self.gate.evaluate(liquidity, features, context)
        if gate_reason is not None:
            self._candidate_since = None
            return _decision(
                model=AlphaModelName.SHORT_MICROSTRUCTURE_V0,
                market=market,
                liquidity=liquidity,
                features=features,
                status=AlphaDecisionStatus.NO_TRADE,
                side=None,
                reasons=(gate_reason.value,),
                no_trade_reason=gate_reason,
            )
        reason = self._rejection(liquidity, features)
        if reason is not None:
            self._candidate_since = None
            return _decision(
                model=AlphaModelName.SHORT_MICROSTRUCTURE_V0,
                market=market,
                liquidity=liquidity,
                features=features,
                status=AlphaDecisionStatus.HOLD,
                side=None,
                reasons=(reason.value,),
            )
        if self._candidate_since is None:
            self._candidate_since = features.timestamp
        elapsed_ms = (features.timestamp - self._candidate_since).total_seconds() * 1000
        if elapsed_ms < self.config.minimum_persistence_ms:
            return _decision(
                model=AlphaModelName.SHORT_MICROSTRUCTURE_V0,
                market=market,
                liquidity=liquidity,
                features=features,
                status=AlphaDecisionStatus.HOLD,
                side=None,
                reasons=(ShortAlphaReason.SHORT_PERSISTENCE_REJECTED.value,),
            )
        return _decision(
            model=AlphaModelName.SHORT_MICROSTRUCTURE_V0,
            market=market,
            liquidity=liquidity,
            features=features,
            status=AlphaDecisionStatus.SHORT,
            side=PositionSide.SHORT,
            reasons=(ShortAlphaReason.SHORT_ALPHA_CONFIRMED.value,),
        )

    def _rejection(
        self,
        liquidity: LiquiditySnapshot,
        features: MicrostructureFeatureSnapshot,
    ) -> ShortAlphaReason | None:
        if features.spread_bps > self.config.maximum_spread_bps:
            return ShortAlphaReason.SHORT_SPREAD_REJECTED
        if liquidity.top_20_ask_notional < self.config.minimum_ask_notional:
            return ShortAlphaReason.SHORT_DEPTH_REJECTED
        if features.depth_imbalance_20 > self.config.maximum_depth_imbalance:
            return ShortAlphaReason.SHORT_BOOK_IMBALANCE_REJECTED
        if features.ofi_1s > self.config.maximum_ofi:
            return ShortAlphaReason.SHORT_OFI_REJECTED
        if (
            features.trade_flow_1s.aggressive_trade_imbalance
            > self.config.maximum_trade_imbalance
        ):
            return ShortAlphaReason.SHORT_TRADE_FLOW_REJECTED
        if features.microprice_edge_bps > self.config.maximum_microprice_edge_bps:
            return ShortAlphaReason.SHORT_MICROPRICE_REJECTED
        if features.momentum_1s_bps > self.config.maximum_momentum_bps:
            return ShortAlphaReason.SHORT_MOMENTUM_REJECTED
        return None


class IntradayAlphaCoordinator:
    @staticmethod
    def resolve(
        long_decision: IntradayAlphaDecision,
        short_decision: IntradayAlphaDecision,
    ) -> IntradayAlphaDecision:
        if (
            long_decision.status is AlphaDecisionStatus.LONG
            and short_decision.status is AlphaDecisionStatus.SHORT
        ):
            return _decision(
                model=AlphaModelName.COORDINATOR,
                market=long_decision.market,
                liquidity=long_decision.liquidity_snapshot,
                features=long_decision.feature_snapshot,
                status=AlphaDecisionStatus.NO_TRADE,
                side=None,
                reasons=(NoTradeReason.NO_TRADE_CONFLICT.value,),
                no_trade_reason=NoTradeReason.NO_TRADE_CONFLICT,
            )
        if long_decision.status is AlphaDecisionStatus.LONG:
            return long_decision
        if short_decision.status is AlphaDecisionStatus.SHORT:
            return short_decision
        if long_decision.status is AlphaDecisionStatus.NO_TRADE:
            return long_decision
        if short_decision.status is AlphaDecisionStatus.NO_TRADE:
            return short_decision
        return long_decision


@dataclass(frozen=True, slots=True)
class AlphaFrequencySummary:
    total_evaluation_events: int
    long_alpha: int
    short_alpha: int
    hold: int
    no_trade: int
    no_trade_by_liquidity: int
    no_trade_by_data: int
    no_trade_by_spread: int
    no_trade_by_depth: int
    no_trade_by_conflict: int
    alpha_signals_per_minute: Decimal
    long_signals_per_hour: Decimal
    short_signals_per_hour: Decimal
    signals_per_day: Decimal
    no_trade_percent: Decimal
    average_signal_persistence_ms: Decimal
    median_signal_persistence_ms: Decimal
    target_is_diagnostic_only: bool = True


def summarize_alpha_frequency(
    decisions: tuple[IntradayAlphaDecision, ...],
) -> AlphaFrequencySummary:
    """Summarize observed alpha frequency without enforcing a trading quota."""

    ordered = tuple(sorted(decisions, key=lambda item: (item.timestamp, item.decision_id)))
    long_count = sum(item.status is AlphaDecisionStatus.LONG for item in ordered)
    short_count = sum(item.status is AlphaDecisionStatus.SHORT for item in ordered)
    hold_count = sum(item.status is AlphaDecisionStatus.HOLD for item in ordered)
    no_trade_count = sum(item.status is AlphaDecisionStatus.NO_TRADE for item in ordered)
    elapsed_seconds = (
        Decimal(str((ordered[-1].timestamp - ordered[0].timestamp).total_seconds()))
        if len(ordered) > 1
        else ZERO
    )
    signal_count = long_count + short_count
    durations = _signal_persistence_durations(ordered)
    average_persistence = (
        sum(durations, ZERO) / Decimal(len(durations)) if durations else ZERO
    )
    median_persistence = Decimal(str(median(durations))) if durations else ZERO
    reasons = tuple(item.no_trade_reason for item in ordered if item.no_trade_reason is not None)
    liquidity_reasons = {
        NoTradeReason.BOOK_NOT_SYNCHRONIZED,
        NoTradeReason.MARKET_DATA_STALE,
    }
    data_reasons = {
        NoTradeReason.EVENT_GAP,
        NoTradeReason.RESYNC_IN_PROGRESS,
        NoTradeReason.FEATURE_WARMUP,
        NoTradeReason.NO_TRADE_ALLOWED,
        NoTradeReason.MARKET_STATE_UNKNOWN,
        NoTradeReason.REPLAY_INCONSISTENT,
    }
    return AlphaFrequencySummary(
        total_evaluation_events=len(ordered),
        long_alpha=long_count,
        short_alpha=short_count,
        hold=hold_count,
        no_trade=no_trade_count,
        no_trade_by_liquidity=sum(reason in liquidity_reasons for reason in reasons),
        no_trade_by_data=sum(reason in data_reasons for reason in reasons),
        no_trade_by_spread=sum(reason is NoTradeReason.INVALID_SPREAD for reason in reasons),
        no_trade_by_depth=sum(reason is NoTradeReason.DEPTH_INSUFFICIENT for reason in reasons),
        no_trade_by_conflict=sum(
            reason is NoTradeReason.NO_TRADE_CONFLICT for reason in reasons
        ),
        alpha_signals_per_minute=_frequency(signal_count, elapsed_seconds, Decimal("60")),
        long_signals_per_hour=_frequency(long_count, elapsed_seconds, Decimal("3600")),
        short_signals_per_hour=_frequency(short_count, elapsed_seconds, Decimal("3600")),
        signals_per_day=_frequency(signal_count, elapsed_seconds, Decimal("86400")),
        no_trade_percent=(
            Decimal(no_trade_count) / Decimal(len(ordered)) * Decimal("100")
            if ordered
            else ZERO
        ),
        average_signal_persistence_ms=average_persistence,
        median_signal_persistence_ms=median_persistence,
    )


def liquidity_state(
    liquidity: LiquiditySnapshot,
    *,
    required_quantity: Decimal,
    maximum_visible_depth_fraction: Decimal,
    maximum_slippage_bps: Decimal,
    side: PositionSide,
) -> LiquidityState:
    visible = liquidity.visible_quantity(side)
    executable = (
        liquidity.executable_buy_price(required_quantity)
        if side is PositionSide.LONG
        else liquidity.executable_sell_price(required_quantity)
    )
    if not liquidity.synchronized or executable is None or visible <= ZERO:
        return LiquidityState.LIQUIDITY_UNSAFE
    fraction = required_quantity / visible
    slippage = liquidity.slippage_bps(side, required_quantity)
    if slippage is None or fraction > maximum_visible_depth_fraction:
        return LiquidityState.LIQUIDITY_UNSAFE
    if slippage > maximum_slippage_bps:
        return LiquidityState.LIQUIDITY_THIN
    return LiquidityState.LIQUIDITY_OK


def _frequency(count: int, elapsed_seconds: Decimal, unit_seconds: Decimal) -> Decimal:
    return Decimal(count) * unit_seconds / elapsed_seconds if elapsed_seconds > ZERO else ZERO


def _signal_persistence_durations(
    decisions: tuple[IntradayAlphaDecision, ...],
) -> tuple[Decimal, ...]:
    durations: list[Decimal] = []
    start: datetime | None = None
    previous: datetime | None = None
    active_status: AlphaDecisionStatus | None = None
    for decision in decisions:
        if decision.status not in {AlphaDecisionStatus.LONG, AlphaDecisionStatus.SHORT}:
            if start is not None and previous is not None:
                durations.append(Decimal(str((previous - start).total_seconds() * 1000)))
            start = previous = None
            active_status = None
            continue
        if active_status is not decision.status:
            if start is not None and previous is not None:
                durations.append(Decimal(str((previous - start).total_seconds() * 1000)))
            start = decision.timestamp
            active_status = decision.status
        previous = decision.timestamp
    if start is not None and previous is not None:
        durations.append(Decimal(str((previous - start).total_seconds() * 1000)))
    return tuple(durations)


def _decision(
    *,
    model: AlphaModelName,
    market: MarketType,
    liquidity: LiquiditySnapshot,
    features: object,
    status: AlphaDecisionStatus,
    side: PositionSide | None,
    reasons: tuple[str, ...],
    no_trade_reason: NoTradeReason | None = None,
) -> IntradayAlphaDecision:
    timestamp = liquidity.timestamp
    identity = "|".join(
        (model.value, market.value, liquidity.symbol, timestamp.isoformat(), *reasons)
    )
    return IntradayAlphaDecision(
        decision_id=hashlib.sha256(identity.encode()).hexdigest(),
        timestamp=timestamp,
        market=market,
        symbol=liquidity.symbol,
        model=model,
        side=side,
        status=status,
        confidence_inputs=(
            ("spread_bps", liquidity.spread_bps),
            ("book_synchronized", liquidity.synchronized),
        ),
        feature_snapshot=features,
        reason_codes=reasons,
        liquidity_snapshot=liquidity,
        expected_execution_side=(
            "BUY" if side is PositionSide.LONG else "SELL" if side is PositionSide.SHORT else None
        ),
        no_trade_reason=no_trade_reason,
    )


def _validate_alpha_config(
    spread: Decimal,
    depth: Decimal,
    persistence_ms: int,
) -> None:
    for value, name in ((spread, "spread"), (depth, "depth")):
        if not isinstance(value, Decimal) or not value.is_finite() or value <= ZERO:
            raise ValueError(f"{name} threshold must be positive")
    if persistence_ms <= 0:
        raise ValueError("minimum persistence must be positive")
