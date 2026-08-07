from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.domain.models import Candle
from adaptive_trader.strategy.trend_following import (
    TrendFollowingDecisionEngine,
    TrendFollowingDirection,
    TrendFollowingParameters,
    TrendFollowingReasonCode,
)


def _candles(
    closes: tuple[str, ...],
    *,
    highs: tuple[str, ...] | None = None,
    lows: tuple[str, ...] | None = None,
) -> tuple[Candle, ...]:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    result: list[Candle] = []
    previous = Decimal(closes[0])
    for index, close_text in enumerate(closes):
        close = Decimal(close_text)
        high = Decimal(highs[index]) if highs else max(previous, close) + Decimal("1")
        low = Decimal(lows[index]) if lows else min(previous, close) - Decimal("1")
        opened = start + timedelta(days=index)
        result.append(
            Candle(
                symbol="ETHUSDT",
                interval="1d",
                timestamp=opened,
                close_time=opened + timedelta(days=1) - timedelta(milliseconds=1),
                open=previous,
                high=high,
                low=low,
                close=close,
                volume=Decimal("10"),
            )
        )
        previous = close
    return tuple(result)


def _engine(*, allow_short: bool = False, exit_period: int = 10):
    return TrendFollowingDecisionEngine(
        TrendFollowingParameters(
            sma_period=3,
            entry_period=2,
            exit_period=exit_period,
            allow_long=True,
            allow_short=allow_short,
        )
    )


def test_long_breakout_uses_close_and_channels_exclude_current_candle() -> None:
    candles = _candles(
        tuple(["10"] * 9 + ["11", "13"]),
        highs=tuple(["11"] * 9 + ["12", "100"]),
        lows=tuple(["9"] * 11),
    )

    decision = _engine().evaluate(candles)

    assert decision.direction is TrendFollowingDirection.ENTER_LONG
    assert decision.reason_code is TrendFollowingReasonCode.ENTER_LONG_APPROVED
    assert decision.previous_entry_high == Decimal("12")
    assert decision.previous_entry_high != candles[-1].high
    assert decision.breakout_long is True
    assert decision.sma == (Decimal("10") + Decimal("11") + Decimal("13")) / Decimal("3")
    assert decision.initial_stop == Decimal("9")


def test_intraday_high_without_close_breakout_does_not_enter() -> None:
    candles = _candles(
        tuple(["10"] * 9 + ["12", "11.5"]),
        highs=tuple(["11"] * 9 + ["13", "100"]),
        lows=tuple(["9"] * 11),
    )

    decision = _engine().evaluate(candles)

    assert decision.direction is TrendFollowingDirection.HOLD
    assert (
        decision.reason_code
        is TrendFollowingReasonCode.DONCHIAN_LONG_BREAKOUT_NOT_CONFIRMED
    )
    assert decision.breakout_long is False


def test_short_breakout_is_distinct_from_spot_sell() -> None:
    candles = _candles(
        tuple(["10"] * 9 + ["9", "7"]),
        highs=tuple(["11"] * 11),
        lows=tuple(["9"] * 9 + ["8", "1"]),
    )

    decision = _engine(allow_short=True).evaluate(candles)

    assert decision.direction is TrendFollowingDirection.ENTER_SHORT
    assert decision.reason_code is TrendFollowingReasonCode.ENTER_SHORT_APPROVED
    assert decision.previous_entry_low == Decimal("8")
    assert decision.initial_stop == Decimal("11")


def test_long_only_mode_never_turns_short_breakout_into_spot_sell() -> None:
    candles = _candles(
        tuple(["10"] * 9 + ["9", "7"]),
        highs=tuple(["11"] * 11),
        lows=tuple(["9"] * 9 + ["8", "1"]),
    )

    decision = _engine(allow_short=False).evaluate(candles)

    assert decision.direction is TrendFollowingDirection.HOLD
    assert decision.reason_code is TrendFollowingReasonCode.SHORT_NOT_ALLOWED
    assert decision.breakout_short is True


def test_macro_exit_has_priority_but_preserves_both_true_conditions() -> None:
    candles = _candles(
        tuple(["10"] * 9 + ["11", "5"]),
        highs=tuple(["11"] * 11),
        lows=tuple(["9"] * 10 + ["4"]),
    )

    decision = _engine().evaluate(candles, position_side=PositionSide.LONG)

    assert decision.direction is TrendFollowingDirection.EXIT_LONG
    assert decision.reason_code is TrendFollowingReasonCode.MACRO_FILTER_EXIT
    assert decision.macro_exit_condition is True
    assert decision.donchian_exit_condition is True
    assert decision.all_exit_conditions == (
        TrendFollowingReasonCode.MACRO_FILTER_EXIT,
        TrendFollowingReasonCode.DONCHIAN_EXIT_10,
    )


def test_warmup_and_existing_position_are_explicit() -> None:
    warmup = _engine().evaluate(_candles(("10", "11")))
    managed = _engine().evaluate(
        _candles(tuple(["10"] * 9 + ["11", "10.5"])),
        position_side=PositionSide.LONG,
    )

    assert warmup.reason_code is TrendFollowingReasonCode.WARMUP_INCOMPLETE
    assert managed.reason_code is TrendFollowingReasonCode.POSITION_ALREADY_OPEN
    assert managed.direction is TrendFollowingDirection.HOLD
