from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.microstructure.models import DepthLevel
from adaptive_trader.microstructure.multi_minute_runner import (
    MultiMinuteProfitRunnerConfig,
    MultiMinuteProfitRunnerController,
    MultiMinuteRunnerState,
    MultiMinuteRunnerVariant,
    RunnerReversalEvidence,
)
from adaptive_trader.microstructure.replay import VirtualClock
from tests.microstructure.helpers import at, liquidity


def executable(milliseconds: int, bid: str = "2002", *, synchronized: bool = True):
    base = liquidity(milliseconds=milliseconds)
    bid_price = Decimal(bid)
    ask_price = bid_price + Decimal("0.10")
    return replace(
        base,
        timestamp=at(milliseconds),
        best_bid=bid_price,
        best_ask=ask_price,
        mid_price=(bid_price + ask_price) / 2,
        spread=ask_price - bid_price,
        spread_bps=Decimal("0.5"),
        book_age_ms=Decimal("0"),
        synchronized=synchronized,
        bids=(DepthLevel(bid_price, Decimal("10")),),
        asks=(DepthLevel(ask_price, Decimal("10")),),
    )


def runner(
    variant: MultiMinuteRunnerVariant = MultiMinuteRunnerVariant.RUNNER_10M,
    side: PositionSide = PositionSide.LONG,
) -> MultiMinuteProfitRunnerController:
    return MultiMinuteProfitRunnerController(
        side=side,
        quantity=Decimal("1"),
        entry_price=Decimal("2000"),
        config=MultiMinuteProfitRunnerConfig(variant),
    )


@pytest.mark.parametrize(
    ("variant", "deadline_ms", "reason"),
    [
        (MultiMinuteRunnerVariant.RUNNER_10M, 600_000, "MAX_HOLD_10M"),
        (MultiMinuteRunnerVariant.RUNNER_15M, 900_000, "MAX_HOLD_15M"),
    ],
)
def test_runner_uses_virtual_time_and_exact_max_hold(
    variant: MultiMinuteRunnerVariant, deadline_ms: int, reason: str
) -> None:
    model = runner(variant)
    clock = VirtualClock()
    clock.advance_to(at(0))
    armed = model.observe(timestamp=clock.now, liquidity=executable(0), mark_price=Decimal("1"))
    clock.advance_to(at(300))
    still_open = model.observe(timestamp=clock.now, liquidity=executable(300))
    clock.advance_to(at(deadline_ms))
    exited = model.observe(timestamp=clock.now, liquidity=executable(deadline_ms))

    assert armed.state is MultiMinuteRunnerState.ARMED
    assert still_open.state is MultiMinuteRunnerState.EXTENDING
    assert still_open.exit_reason is None
    assert exited.exit_reason == reason
    assert exited.mark_price_ignored is True


def test_runner_reversal_requires_150ms_and_recovers() -> None:
    model = runner()
    model.observe(timestamp=at(0), liquidity=executable(0))
    pending = model.observe(
        timestamp=at(50),
        liquidity=executable(50, "2001.90"),
        reversal=RunnerReversalEvidence(ofi=True),
    )
    recovered = model.observe(timestamp=at(100), liquidity=executable(100, "2001.90"))
    model.observe(
        timestamp=at(200),
        liquidity=executable(200, "2001.90"),
        reversal=RunnerReversalEvidence(depth=True),
    )
    confirmed = model.observe(
        timestamp=at(350),
        liquidity=executable(350, "2001.90"),
        reversal=RunnerReversalEvidence(depth=True),
    )

    assert pending.state is MultiMinuteRunnerState.REVERSAL_PENDING
    assert recovered.state is MultiMinuteRunnerState.EXTENDING
    assert recovered.reversal_started_at is None
    assert confirmed.exit_reason == "REVERSAL_CONFIRMED_150MS"
    assert confirmed.reversal_evidence.depth is True


def test_runner_hard_floor_is_immediate_and_peak_is_monotonic() -> None:
    model = runner()
    model.observe(timestamp=at(0), liquidity=executable(0))
    peak = model.observe(timestamp=at(100), liquidity=executable(100, "2003"))
    exited = model.observe(timestamp=at(101), liquidity=executable(101, "2001"))

    assert peak.peak_net_profit_bps is not None
    assert exited.exit_reason == "HARD_PROFIT_FLOOR"
    assert exited.floor_at_exit_bps == Decimal("1")
    assert exited.maximum_giveback_bps is not None
    assert exited.maximum_giveback_bps > 0


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"capture_boundary_valid": False}, "CAPTURE_BOUNDARY_INVALID"),
        ({"accounting_invariant_valid": False}, "ACCOUNTING_RISK_INVARIANT"),
        ({"feed_ready": False}, "LIQUIDITY_EXIT_FAILSAFE"),
    ],
)
def test_runner_failsafes_exit_without_wait(kwargs: dict[str, bool], reason: str) -> None:
    model = runner()
    observed = model.observe(timestamp=at(0), liquidity=executable(0), **kwargs)
    assert observed.state is MultiMinuteRunnerState.FAILSAFE
    assert observed.exit_reason == reason


def test_short_runner_realizes_on_asks_not_mark() -> None:
    model = MultiMinuteProfitRunnerController(
        side=PositionSide.SHORT,
        quantity=Decimal("1"),
        entry_price=Decimal("2000"),
        config=MultiMinuteProfitRunnerConfig(MultiMinuteRunnerVariant.RUNNER_15M),
    )
    state = executable(0, "1997.90")
    observed = model.observe(
        timestamp=at(0), liquidity=state, mark_price=Decimal("9999")
    )
    assert observed.executable_reference == state.best_ask
    assert observed.state is MultiMinuteRunnerState.ARMED
    assert observed.mark_price_ignored is True
