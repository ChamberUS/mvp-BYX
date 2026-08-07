from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.domain.models import Candle
from adaptive_trader.strategy.trend_following import (
    TrendFollowingDecisionEngine,
    TrendFollowingDirection,
    TrendFollowingParameters,
)


def _daily_prefix(last_close: str) -> tuple[Candle, ...]:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    closes = tuple(["10"] * 10 + [last_close])
    candles: list[Candle] = []
    for index, close_text in enumerate(closes):
        opened = start + timedelta(days=index)
        close = Decimal(close_text)
        candles.append(
            Candle(
                symbol="ETHUSDT",
                interval="1d",
                timestamp=opened,
                close_time=opened + timedelta(days=1) - timedelta(milliseconds=1),
                open=Decimal("10"),
                high=max(Decimal("11"), close),
                low=min(Decimal("9"), close),
                close=close,
                volume=Decimal("1"),
            )
        )
    return tuple(candles)


def _engine() -> TrendFollowingDecisionEngine:
    return TrendFollowingDecisionEngine(
        TrendFollowingParameters(
            sma_period=3,
            entry_period=2,
            exit_period=10,
        )
    )


def test_entry_is_confirmed_at_close_and_scheduled_for_next_utc_day() -> None:
    candles = _daily_prefix("12")

    decision = _engine().evaluate(candles)

    assert decision.direction is TrendFollowingDirection.ENTER_LONG
    assert decision.timestamp == candles[-1].close_time
    assert decision.execute_at == candles[-1].open_time + timedelta(days=1)
    assert decision.execute_at > decision.timestamp


def test_exit_is_detected_at_close_and_never_scheduled_same_day() -> None:
    candles = _daily_prefix("5")

    decision = _engine().evaluate(candles, position_side=PositionSide.LONG)

    assert decision.direction is TrendFollowingDirection.EXIT_LONG
    assert decision.timestamp == candles[-1].close_time
    assert decision.execute_at == candles[-1].open_time + timedelta(days=1)
    assert decision.execute_at.date() > decision.timestamp.date()


def test_hold_has_no_execution_timestamp() -> None:
    decision = _engine().evaluate(
        _daily_prefix("10.5"),
        position_side=PositionSide.LONG,
    )

    assert decision.direction is TrendFollowingDirection.HOLD
    assert decision.execute_at is None
