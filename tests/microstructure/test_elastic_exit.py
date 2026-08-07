from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.microstructure.elastic_exit import (
    ElasticProfitExitConfig,
    ElasticProfitExitController,
)
from adaptive_trader.microstructure.models import DepthLevel, ProfitExtensionState
from adaptive_trader.microstructure.replay import VirtualClock
from tests.microstructure.helpers import at, liquidity


def long_liquidity(milliseconds: int, bid: str = "2002", quantity: str = "2"):
    base = liquidity(milliseconds=milliseconds)
    price = Decimal(bid)
    return replace(
        base,
        timestamp=at(milliseconds),
        best_bid=price,
        best_ask=price + Decimal("0.10"),
        mid_price=price + Decimal("0.05"),
        spread=Decimal("0.10"),
        spread_bps=Decimal("0.5"),
        bid_quantity=Decimal(quantity),
        ask_quantity=Decimal("2"),
        book_age_ms=Decimal("0"),
        bids=(DepthLevel(price, Decimal(quantity)),),
        asks=(DepthLevel(price + Decimal("0.10"), Decimal("2")),),
    )


def short_liquidity(milliseconds: int, ask: str = "1998", quantity: str = "2"):
    base = liquidity(milliseconds=milliseconds)
    price = Decimal(ask)
    return replace(
        base,
        timestamp=at(milliseconds),
        best_bid=price - Decimal("0.10"),
        best_ask=price,
        mid_price=price - Decimal("0.05"),
        spread=Decimal("0.10"),
        spread_bps=Decimal("0.5"),
        bid_quantity=Decimal("2"),
        ask_quantity=Decimal(quantity),
        book_age_ms=Decimal("0"),
        bids=(DepthLevel(price - Decimal("0.10"), Decimal("2")),),
        asks=(DepthLevel(price, Decimal(quantity)),),
    )


def controller(side: PositionSide = PositionSide.LONG) -> ElasticProfitExitController:
    return ElasticProfitExitController(
        side=side,
        quantity=Decimal("1"),
        entry_price=Decimal("2000"),
    )


def test_disarmed_activation_and_mark_price_cannot_fake_executable_profit() -> None:
    model = controller()
    disarmed = model.observe(
        timestamp=at(),
        liquidity=long_liquidity(0, bid="2000.50"),
        microstructure_reversal=False,
        mark_price=Decimal("9999"),
    )
    armed = model.observe(
        timestamp=at(10),
        liquidity=long_liquidity(10),
        microstructure_reversal=False,
        mark_price=Decimal("1"),
    )

    assert disarmed.state is ProfitExtensionState.DISARMED
    assert disarmed.mark_price_ignored is True
    assert armed.state is ProfitExtensionState.ARMED
    assert armed.activation_time == at(10)
    assert armed.peak_executable_price == Decimal("2002")
    assert armed.net_executable_profit_bps == Decimal("5.000")
    assert armed.continuation_deadline == at(310)


def test_new_peak_resets_300ms_and_exact_deadline_exits_without_sleep() -> None:
    model = controller()
    clock = VirtualClock()
    clock.advance_to(at())
    model.observe(
        timestamp=clock.now,
        liquidity=long_liquidity(0),
        microstructure_reversal=False,
    )
    clock.advance_to(at(100))
    peak = model.observe(
        timestamp=clock.now,
        liquidity=long_liquidity(100, bid="2003"),
        microstructure_reversal=False,
    )
    before = model.observe(
        timestamp=at(399),
        liquidity=long_liquidity(399, bid="2003"),
        microstructure_reversal=False,
    )
    deadline = model.observe(
        timestamp=at(400),
        liquidity=long_liquidity(400, bid="2003"),
        microstructure_reversal=False,
    )

    assert peak.state is ProfitExtensionState.EXTENDING
    assert peak.continuation_deadline == at(400)
    assert before.state is ProfitExtensionState.EXTENDING
    assert deadline.state is ProfitExtensionState.EXIT_REQUESTED
    assert deadline.exit_reason == "NO_NEW_PEAK_300MS"


def test_reversal_149ms_waits_and_150ms_requests_exit() -> None:
    model = controller()
    model.observe(timestamp=at(), liquidity=long_liquidity(0), microstructure_reversal=False)
    started = model.observe(
        timestamp=at(50),
        liquidity=long_liquidity(50, bid="2001.90"),
        microstructure_reversal=True,
    )
    before = model.observe(
        timestamp=at(199),
        liquidity=long_liquidity(199, bid="2001.90"),
        microstructure_reversal=True,
    )
    confirmed = model.observe(
        timestamp=at(200),
        liquidity=long_liquidity(200, bid="2001.90"),
        microstructure_reversal=True,
    )

    assert started.state is ProfitExtensionState.REVERSAL_PENDING
    assert before.state is ProfitExtensionState.REVERSAL_PENDING
    assert confirmed.state is ProfitExtensionState.EXIT_REQUESTED
    assert confirmed.exit_reason == "REVERSAL_CONFIRMED_150MS"


def test_recovery_and_new_peak_cancel_reversal_state() -> None:
    recovered_model = controller()
    recovered_model.observe(
        timestamp=at(), liquidity=long_liquidity(0), microstructure_reversal=False
    )
    recovered_model.observe(
        timestamp=at(50),
        liquidity=long_liquidity(50, bid="2001.90"),
        microstructure_reversal=True,
    )
    recovered = recovered_model.observe(
        timestamp=at(100),
        liquidity=long_liquidity(100, bid="2001.90"),
        microstructure_reversal=False,
    )

    peak_model = controller()
    peak_model.observe(timestamp=at(), liquidity=long_liquidity(0), microstructure_reversal=False)
    peak_model.observe(
        timestamp=at(50),
        liquidity=long_liquidity(50, bid="2001.90"),
        microstructure_reversal=True,
    )
    peak = peak_model.observe(
        timestamp=at(100),
        liquidity=long_liquidity(100, bid="2003"),
        microstructure_reversal=True,
    )

    assert recovered.state is ProfitExtensionState.EXTENDING
    assert recovered.reversal_started_at is None
    assert peak.state is ProfitExtensionState.EXTENDING
    assert peak.reversal_started_at is None
    assert peak.peak_executable_price == Decimal("2003")


def test_hard_profit_floor_has_priority_over_reversal_confirmation() -> None:
    model = controller()
    model.observe(timestamp=at(), liquidity=long_liquidity(0), microstructure_reversal=False)
    result = model.observe(
        timestamp=at(1),
        liquidity=long_liquidity(1, bid="2001"),
        microstructure_reversal=True,
    )

    assert result.state is ProfitExtensionState.EXIT_REQUESTED
    assert result.exit_reason == "HARD_PROFIT_FLOOR"


@pytest.mark.parametrize(
    "change",
    [
        {"spread_bps": Decimal("6")},
        {"synchronized": False},
        {"book_age_ms": Decimal("251")},
        {"bids": (DepthLevel(Decimal("2002"), Decimal("0.1")),)},
    ],
)
def test_liquidity_deterioration_depth_desync_and_stale_trigger_failsafe(
    change: dict[str, object],
) -> None:
    model = controller()
    model.observe(timestamp=at(), liquidity=long_liquidity(0), microstructure_reversal=False)
    result = model.observe(
        timestamp=at(1),
        liquidity=replace(long_liquidity(1), **change),
        microstructure_reversal=False,
    )

    assert result.state is ProfitExtensionState.FAILSAFE
    assert result.exit_reason == "LIQUIDITY_EXIT_FAILSAFE"


def test_long_uses_bid_short_uses_ask_and_controllers_are_deterministic() -> None:
    long_model = controller(PositionSide.LONG)
    short_model = controller(PositionSide.SHORT)
    long_result = long_model.observe(
        timestamp=at(), liquidity=long_liquidity(0), microstructure_reversal=False
    )
    short_result = short_model.observe(
        timestamp=at(), liquidity=short_liquidity(0), microstructure_reversal=False
    )

    assert long_result.executable_reference == Decimal("2002")
    assert short_result.executable_reference == Decimal("1998")
    assert long_result.state is short_result.state is ProfitExtensionState.ARMED

    first = controller()
    second = controller()
    first_result = first.observe(
        timestamp=at(), liquidity=long_liquidity(0), microstructure_reversal=False
    )
    second_result = second.observe(
        timestamp=at(), liquidity=long_liquidity(0), microstructure_reversal=False
    )
    assert first_result == second_result


def test_elastic_profile_and_observation_input_guards() -> None:
    with pytest.raises(ValueError, match="only ELASTIC"):
        ElasticProfitExitConfig(profile_id="OTHER")
    with pytest.raises(ValueError, match="fixed at 300/150"):
        ElasticProfitExitConfig(continuation_grace_ms=299)
    with pytest.raises(ValueError, match="locked profit"):
        ElasticProfitExitConfig(minimum_locked_profit_bps=Decimal("5"))
    with pytest.raises(ValueError, match="positive"):
        ElasticProfitExitController(
            side=PositionSide.LONG,
            quantity=Decimal("0"),
            entry_price=Decimal("2000"),
        )
    model = controller()
    with pytest.raises(ValueError, match="future"):
        model.observe(
            timestamp=at(),
            liquidity=long_liquidity(1),
            microstructure_reversal=False,
        )
