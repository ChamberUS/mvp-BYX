from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.microstructure.alpha import (
    GateContext,
    IntradayAlphaCoordinator,
    LongAlphaConfig,
    LongAlphaReason,
    LongMicrostructureAlpha,
    NoTradeGate,
    NoTradeGateConfig,
    ShortAlphaConfig,
    ShortAlphaReason,
    ShortMicrostructureAlpha,
    liquidity_state,
    summarize_alpha_frequency,
)
from adaptive_trader.microstructure.models import (
    AlphaDecisionStatus,
    LiquidityState,
    NoTradeReason,
)
from tests.microstructure.helpers import at, feature_snapshot


def gate() -> NoTradeGate:
    return NoTradeGate(
        NoTradeGateConfig(
            maximum_event_age_ms=Decimal("1000"),
            maximum_book_age_ms=Decimal("1000"),
            maximum_spread_bps=Decimal("10"),
            minimum_top_20_notional=Decimal("1"),
        )
    )


def long_config() -> LongAlphaConfig:
    return LongAlphaConfig(
        maximum_spread_bps=Decimal("5"),
        minimum_bid_notional=Decimal("100"),
        minimum_depth_imbalance=Decimal("0.1"),
        minimum_ofi=Decimal("1"),
        minimum_trade_imbalance=Decimal("0.2"),
        minimum_microprice_edge_bps=Decimal("0.1"),
        minimum_momentum_bps=Decimal("0.1"),
        minimum_persistence_ms=100,
    )


def short_config() -> ShortAlphaConfig:
    return ShortAlphaConfig(
        maximum_spread_bps=Decimal("6"),
        minimum_ask_notional=Decimal("120"),
        maximum_depth_imbalance=Decimal("-0.2"),
        maximum_ofi=Decimal("-2"),
        maximum_trade_imbalance=Decimal("-0.3"),
        maximum_microprice_edge_bps=Decimal("-0.2"),
        maximum_momentum_bps=Decimal("-0.4"),
        minimum_persistence_ms=120,
    )


def long_inputs():
    liquidity, features = feature_snapshot(market=MarketType.USD_M_FUTURES)
    liquidity = replace(
        liquidity,
        spread_bps=Decimal("1"),
        top_20_bid_notional=Decimal("1000"),
        top_20_ask_notional=Decimal("1000"),
        depth_imbalance_20=Decimal("0.4"),
    )
    features = replace(
        features,
        spread_bps=Decimal("1"),
        depth_imbalance_20=Decimal("0.4"),
        ofi_1s=Decimal("5"),
        trade_flow_1s=replace(
            features.trade_flow_1s,
            aggressive_trade_imbalance=Decimal("0.5"),
        ),
        microprice_edge_bps=Decimal("0.5"),
        momentum_1s_bps=Decimal("0.5"),
        event_age_ms=Decimal("0"),
        warmup_complete=True,
    )
    return liquidity, features


def short_inputs():
    liquidity, features = feature_snapshot(market=MarketType.USD_M_FUTURES)
    liquidity = replace(
        liquidity,
        spread_bps=Decimal("1.5"),
        top_20_bid_notional=Decimal("900"),
        top_20_ask_notional=Decimal("1200"),
        depth_imbalance_20=Decimal("-0.5"),
    )
    features = replace(
        features,
        spread_bps=Decimal("1.5"),
        depth_imbalance_20=Decimal("-0.5"),
        ofi_1s=Decimal("-5"),
        trade_flow_1s=replace(
            features.trade_flow_1s,
            aggressive_trade_imbalance=Decimal("-0.6"),
        ),
        microprice_edge_bps=Decimal("-0.5"),
        momentum_1s_bps=Decimal("-0.8"),
        event_age_ms=Decimal("0"),
        warmup_complete=True,
    )
    return liquidity, features


@pytest.mark.parametrize(
    ("context", "liquidity_change", "feature_change", "expected"),
    [
        (GateContext(resync_in_progress=True), {}, {}, NoTradeReason.RESYNC_IN_PROGRESS),
        (GateContext(recent_gap=True), {}, {}, NoTradeReason.EVENT_GAP),
        (GateContext(replay_consistent=False), {}, {}, NoTradeReason.REPLAY_INCONSISTENT),
        (GateContext(market_state_known=False), {}, {}, NoTradeReason.MARKET_STATE_UNKNOWN),
        (GateContext(data_complete=False), {}, {}, NoTradeReason.NO_TRADE_ALLOWED),
        (GateContext(), {"synchronized": False}, {}, NoTradeReason.BOOK_NOT_SYNCHRONIZED),
        (
            GateContext(),
            {"spread_bps": Decimal("11")},
            {},
            NoTradeReason.INVALID_SPREAD,
        ),
        (
            GateContext(),
            {"book_age_ms": Decimal("1001")},
            {},
            NoTradeReason.MARKET_DATA_STALE,
        ),
        (
            GateContext(),
            {"top_20_bid_notional": Decimal("0.5")},
            {},
            NoTradeReason.DEPTH_INSUFFICIENT,
        ),
        (GateContext(), {}, {"warmup_complete": False}, NoTradeReason.FEATURE_WARMUP),
    ],
)
def test_no_trade_gate_is_first_class(
    context: GateContext,
    liquidity_change: dict[str, object],
    feature_change: dict[str, object],
    expected: NoTradeReason,
) -> None:
    liquidity, features = long_inputs()
    assert gate().evaluate(
        replace(liquidity, **liquidity_change),
        replace(features, **feature_change),
        context,
    ) is expected


def test_long_persistence_then_confirmation_never_emits_short() -> None:
    model = LongMicrostructureAlpha(gate(), long_config())
    liquidity, features = long_inputs()
    first = model.evaluate(
        market=MarketType.USD_M_FUTURES,
        liquidity=liquidity,
        features=features,
    )
    later_liquidity = replace(liquidity, timestamp=at(1_100))
    later_features = replace(features, timestamp=at(1_100))
    second = model.evaluate(
        market=MarketType.USD_M_FUTURES,
        liquidity=later_liquidity,
        features=later_features,
    )

    assert first.status is AlphaDecisionStatus.HOLD
    assert first.reason_codes == (LongAlphaReason.LONG_PERSISTENCE_REJECTED.value,)
    assert second.status is AlphaDecisionStatus.LONG
    assert second.side is PositionSide.LONG
    assert second.expected_execution_side == "BUY"
    assert model.candidate_since == at(1_000)


@pytest.mark.parametrize(
    ("liquidity_change", "feature_change", "expected"),
    [
        ({}, {"spread_bps": Decimal("6")}, LongAlphaReason.LONG_SPREAD_REJECTED),
        (
            {"top_20_bid_notional": Decimal("50")},
            {},
            LongAlphaReason.LONG_DEPTH_REJECTED,
        ),
        ({}, {"depth_imbalance_20": Decimal("0")}, LongAlphaReason.LONG_BOOK_IMBALANCE_REJECTED),
        ({}, {"ofi_1s": Decimal("0")}, LongAlphaReason.LONG_OFI_REJECTED),
        (
            {},
            {"trade_flow_1s": None},
            LongAlphaReason.LONG_TRADE_FLOW_REJECTED,
        ),
        ({}, {"microprice_edge_bps": Decimal("0")}, LongAlphaReason.LONG_MICROPRICE_REJECTED),
        ({}, {"momentum_1s_bps": Decimal("0")}, LongAlphaReason.LONG_MOMENTUM_REJECTED),
    ],
)
def test_long_reason_codes_are_independent(
    liquidity_change: dict[str, object],
    feature_change: dict[str, object],
    expected: LongAlphaReason,
) -> None:
    model = LongMicrostructureAlpha(gate(), long_config())
    liquidity, features = long_inputs()
    if "trade_flow_1s" in feature_change and feature_change["trade_flow_1s"] is None:
        feature_change["trade_flow_1s"] = replace(
            features.trade_flow_1s,
            aggressive_trade_imbalance=Decimal("0"),
        )
    decision = model.evaluate(
        market=MarketType.USD_M_FUTURES,
        liquidity=replace(liquidity, **liquidity_change),
        features=replace(features, **feature_change),
    )
    assert decision.status is AlphaDecisionStatus.HOLD
    assert decision.reason_codes == (expected.value,)
    assert model.candidate_since is None


def test_short_has_own_state_thresholds_reasons_and_is_futures_only() -> None:
    model = ShortMicrostructureAlpha(gate(), short_config())
    liquidity, features = short_inputs()
    first = model.evaluate(
        market=MarketType.USD_M_FUTURES,
        liquidity=liquidity,
        features=features,
    )
    second = model.evaluate(
        market=MarketType.USD_M_FUTURES,
        liquidity=replace(liquidity, timestamp=at(1_120)),
        features=replace(features, timestamp=at(1_120)),
    )
    spot = model.evaluate(
        market=MarketType.SPOT,
        liquidity=liquidity,
        features=features,
    )

    assert first.reason_codes == (ShortAlphaReason.SHORT_PERSISTENCE_REJECTED.value,)
    assert second.status is AlphaDecisionStatus.SHORT
    assert second.side is PositionSide.SHORT
    assert second.expected_execution_side == "SELL"
    assert spot.status is AlphaDecisionStatus.NO_TRADE
    assert spot.no_trade_reason is NoTradeReason.NO_TRADE_ALLOWED


@pytest.mark.parametrize(
    ("liquidity_change", "feature_change", "expected"),
    [
        ({}, {"spread_bps": Decimal("7")}, ShortAlphaReason.SHORT_SPREAD_REJECTED),
        ({"top_20_ask_notional": Decimal("100")}, {}, ShortAlphaReason.SHORT_DEPTH_REJECTED),
        ({}, {"depth_imbalance_20": Decimal("0")}, ShortAlphaReason.SHORT_BOOK_IMBALANCE_REJECTED),
        ({}, {"ofi_1s": Decimal("0")}, ShortAlphaReason.SHORT_OFI_REJECTED),
        ({}, {"trade_flow_1s": None}, ShortAlphaReason.SHORT_TRADE_FLOW_REJECTED),
        ({}, {"microprice_edge_bps": Decimal("0")}, ShortAlphaReason.SHORT_MICROPRICE_REJECTED),
        ({}, {"momentum_1s_bps": Decimal("0")}, ShortAlphaReason.SHORT_MOMENTUM_REJECTED),
    ],
)
def test_short_reason_codes_are_not_long_inversions(
    liquidity_change: dict[str, object],
    feature_change: dict[str, object],
    expected: ShortAlphaReason,
) -> None:
    model = ShortMicrostructureAlpha(gate(), short_config())
    liquidity, features = short_inputs()
    if "trade_flow_1s" in feature_change and feature_change["trade_flow_1s"] is None:
        feature_change["trade_flow_1s"] = replace(
            features.trade_flow_1s,
            aggressive_trade_imbalance=Decimal("0"),
        )
    decision = model.evaluate(
        market=MarketType.USD_M_FUTURES,
        liquidity=replace(liquidity, **liquidity_change),
        features=replace(features, **feature_change),
    )
    assert decision.status is AlphaDecisionStatus.HOLD
    assert decision.reason_codes == (expected.value,)


def test_simultaneous_long_short_resolves_to_no_trade_conflict() -> None:
    liquidity, features = long_inputs()
    permissive_long = replace(
        long_config(),
        minimum_depth_imbalance=Decimal("-1"),
        minimum_ofi=Decimal("-10"),
        minimum_trade_imbalance=Decimal("-1"),
        minimum_microprice_edge_bps=Decimal("-10"),
        minimum_momentum_bps=Decimal("-10"),
        minimum_persistence_ms=1,
    )
    permissive_short = replace(
        short_config(),
        maximum_depth_imbalance=Decimal("1"),
        maximum_ofi=Decimal("10"),
        maximum_trade_imbalance=Decimal("1"),
        maximum_microprice_edge_bps=Decimal("10"),
        maximum_momentum_bps=Decimal("10"),
        minimum_persistence_ms=1,
    )
    long_model = LongMicrostructureAlpha(gate(), permissive_long)
    short_model = ShortMicrostructureAlpha(gate(), permissive_short)
    long_model.evaluate(market=MarketType.USD_M_FUTURES, liquidity=liquidity, features=features)
    short_model.evaluate(market=MarketType.USD_M_FUTURES, liquidity=liquidity, features=features)
    later_liquidity = replace(liquidity, timestamp=at(1_001))
    later_features = replace(features, timestamp=at(1_001))
    long_decision = long_model.evaluate(
        market=MarketType.USD_M_FUTURES,
        liquidity=later_liquidity,
        features=later_features,
    )
    short_decision = short_model.evaluate(
        market=MarketType.USD_M_FUTURES,
        liquidity=later_liquidity,
        features=later_features,
    )

    conflict = IntradayAlphaCoordinator.resolve(long_decision, short_decision)
    assert conflict.status is AlphaDecisionStatus.NO_TRADE
    assert conflict.no_trade_reason is NoTradeReason.NO_TRADE_CONFLICT
    assert LongMicrostructureAlpha is not ShortMicrostructureAlpha
    assert LongAlphaConfig is not ShortAlphaConfig
    assert set(LongAlphaReason).isdisjoint(set(ShortAlphaReason))


def test_frequency_is_diagnostic_and_liquidity_gates_use_visible_depth() -> None:
    model = LongMicrostructureAlpha(gate(), replace(long_config(), minimum_persistence_ms=1))
    liquidity, features = long_inputs()
    hold = model.evaluate(
        market=MarketType.USD_M_FUTURES,
        liquidity=liquidity,
        features=features,
    )
    signal1 = model.evaluate(
        market=MarketType.USD_M_FUTURES,
        liquidity=replace(liquidity, timestamp=at(2_000)),
        features=replace(features, timestamp=at(2_000)),
    )
    signal2 = model.evaluate(
        market=MarketType.USD_M_FUTURES,
        liquidity=replace(liquidity, timestamp=at(3_000)),
        features=replace(features, timestamp=at(3_000)),
    )
    summary = summarize_alpha_frequency((signal2, hold, signal1))

    assert summary.total_evaluation_events == 3
    assert summary.long_alpha == 2 and summary.hold == 1
    assert summary.alpha_signals_per_minute == Decimal("60")
    assert summary.average_signal_persistence_ms == Decimal("1000.0")
    assert summary.target_is_diagnostic_only is True
    assert liquidity_state(
        liquidity,
        required_quantity=Decimal("1"),
        maximum_visible_depth_fraction=Decimal("0.5"),
        maximum_slippage_bps=Decimal("10"),
        side=PositionSide.LONG,
    ) is LiquidityState.LIQUIDITY_OK
    assert liquidity_state(
        liquidity,
        required_quantity=Decimal("1000"),
        maximum_visible_depth_fraction=Decimal("0.5"),
        maximum_slippage_bps=Decimal("10"),
        side=PositionSide.LONG,
    ) is LiquidityState.LIQUIDITY_UNSAFE


def test_alpha_config_validation_rejects_unguarded_thresholds() -> None:
    with pytest.raises(ValueError, match="positive"):
        replace(long_config(), maximum_spread_bps=Decimal("0"))
    with pytest.raises(ValueError, match="persistence"):
        replace(short_config(), minimum_persistence_ms=0)
