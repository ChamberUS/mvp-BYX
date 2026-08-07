from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.domain.models import MarketRegime
from adaptive_trader.futures.engine import FuturesBacktestEngine
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FundingRate,
    FuturesExitReason,
    FuturesPriceSource,
    FuturesSignal,
    FuturesSignalDirection,
)
from tests.futures.conftest import make_candles, make_marks


class EntryAnalyzer:
    def __init__(self, side: PositionSide) -> None:
        self.side = side
        self.emitted = False

    def analyze(self, candles, config, position_side):
        latest = candles[-1]
        if not self.emitted:
            self.emitted = True
            short = self.side is PositionSide.SHORT
            return FuturesSignal(
                signal_id=f"fixture-{self.side}",
                symbol=latest.symbol,
                generated_at=latest.close_time,
                direction=(
                    FuturesSignalDirection.ENTER_SHORT
                    if short
                    else FuturesSignalDirection.ENTER_LONG
                ),
                regime=MarketRegime.TRENDING_DOWN if short else MarketRegime.TRENDING_UP,
                entry_price=latest.close,
                stop_loss=Decimal("110") if short else Decimal("90"),
                take_profit=Decimal("80") if short else Decimal("120"),
                rationale="fixture",
                reason_code="FIXTURE_ENTRY",
            )
        return FuturesSignal(
            signal_id="hold",
            symbol=latest.symbol,
            generated_at=latest.close_time,
            direction=FuturesSignalDirection.HOLD,
            regime=MarketRegime.UNKNOWN,
            entry_price=latest.close,
            stop_loss=None,
            take_profit=None,
            rationale="hold",
            reason_code="HOLD",
        )


class EntryExitAnalyzer(EntryAnalyzer):
    def analyze(self, candles, config, position_side):
        if position_side is not None:
            latest = candles[-1]
            return FuturesSignal(
                signal_id="manual-exit",
                symbol=latest.symbol,
                generated_at=latest.close_time,
                direction=(
                    FuturesSignalDirection.EXIT_LONG
                    if position_side is PositionSide.LONG
                    else FuturesSignalDirection.EXIT_SHORT
                ),
                regime=MarketRegime.UNKNOWN,
                entry_price=latest.close,
                stop_loss=None,
                take_profit=None,
                rationale="manual fixture exit",
                reason_code="FIXTURE_EXIT",
            )
        return super().analyze(candles, config, position_side)


def run_engine(config, candles, marks, side=PositionSide.LONG, funding=(), analyzer=None):
    return FuturesBacktestEngine(
        config,
        analyzer=analyzer or EntryAnalyzer(side),
    ).run(candles, marks, funding)


@pytest.mark.parametrize(
    ("side", "closes", "profitable"),
    [
        (PositionSide.LONG, ("100", "101", "102", "110", "115"), True),
        (PositionSide.LONG, ("100", "101", "102", "95", "92"), False),
        (PositionSide.SHORT, ("100", "99", "98", "90", "85"), True),
        (PositionSide.SHORT, ("100", "99", "98", "105", "108"), False),
    ],
)
def test_long_and_short_profit_loss(
    futures_config,
    start_time,
    side,
    closes,
    profitable,
) -> None:
    candles = make_candles(start_time, closes)
    result = run_engine(futures_config, candles, make_marks(candles), side)
    assert result.metrics.trade_count == 1
    assert (result.metrics.net_pnl > 0) is profitable
    assert result.trades[0].side is side
    assert result.metrics.trading_fees > 0
    assert result.trades[0].exit_reason is FuturesExitReason.FORCED_END


def test_stop_take_profit_and_time_exit(futures_config, start_time) -> None:
    stopped = make_candles(
        start_time,
        ("100", "101", "102", "95", "96"),
        lows=("99", "100", "100", "89", "95"),
    )
    stop_result = run_engine(futures_config, stopped, make_marks(stopped))
    assert stop_result.trades[0].exit_reason is FuturesExitReason.STOP_LOSS
    target = make_candles(
        start_time,
        ("100", "101", "102", "119", "121"),
        highs=("101", "102", "103", "121", "122"),
    )
    target_result = run_engine(futures_config, target, make_marks(target))
    assert target_result.trades[0].exit_reason is FuturesExitReason.TAKE_PROFIT
    timed = run_engine(
        replace(futures_config, time_exit_candles=1),
        target,
        make_marks(target),
    )
    assert timed.trades[0].exit_reason is FuturesExitReason.TIME_EXIT


@pytest.mark.parametrize(
    ("side", "expected_sign"),
    [(PositionSide.LONG, -1), (PositionSide.SHORT, 1)],
)
def test_positive_funding_long_pays_short_receives(
    futures_config,
    start_time,
    side,
    expected_sign,
) -> None:
    candles = make_candles(start_time)
    event = FundingRate(
        symbol="ETHUSDT",
        funding_time=start_time + timedelta(hours=2, minutes=30),
        funding_rate=Decimal("0.001"),
        mark_price=Decimal("103"),
    )
    config = replace(
        futures_config,
        funding_enabled=True,
        funding_missing_policy=FundingMissingPolicy.FAIL,
        trading_mode=(
            TradingMode.FUTURES_SHORT_ONLY
            if side is PositionSide.SHORT
            else TradingMode.FUTURES_LONG_ONLY
        ),
    )
    result = run_engine(config, candles, make_marks(candles), side, (event,))
    assert result.metrics.funding_event_count == 1
    assert (result.metrics.net_funding > 0) is (expected_sign > 0)
    if side is PositionSide.LONG:
        assert result.metrics.funding_paid > 0
    else:
        assert result.metrics.funding_received > 0


def test_funding_without_event_mark_uses_current_mark_open_without_lookahead(
    futures_config,
    start_time,
) -> None:
    candles = make_candles(start_time)
    marks = make_marks(
        candles,
        closes=("100", "101", "200", "110", "112", "115"),
        highs=("101", "102", "201", "111", "113", "116"),
    )
    event = FundingRate(
        symbol="ETHUSDT",
        funding_time=start_time + timedelta(hours=2, minutes=30),
        funding_rate=Decimal("0.001"),
        mark_price=None,
    )
    config = replace(
        futures_config,
        funding_enabled=True,
        funding_missing_policy=FundingMissingPolicy.FAIL,
    )
    result = run_engine(config, candles, marks, funding=(event,))
    expected = result.trades[0].quantity * marks[2].open * event.funding_rate
    assert result.metrics.funding_paid == expected


@pytest.mark.parametrize("side", [PositionSide.LONG, PositionSide.SHORT])
def test_liquidation_uses_mark_price_first_and_never_hides_negative_wallet(
    futures_config,
    start_time,
    side,
) -> None:
    candles = make_candles(
        start_time,
        ("100", "101", "102", "103", "104"),
        lows=("99", "100", "89", "102", "103"),
        highs=("101", "102", "111", "104", "105"),
    )
    mark_lows = ("99", "100", "60", "102", "103")
    mark_highs = ("101", "102", "140", "104", "105")
    marks = make_marks(candles, lows=mark_lows, highs=mark_highs)
    config = replace(
        futures_config,
        leverage=Decimal("3"),
        trading_mode=(
            TradingMode.FUTURES_SHORT_ONLY
            if side is PositionSide.SHORT
            else TradingMode.FUTURES_LONG_ONLY
        ),
    )
    result = run_engine(config, candles, marks, side)
    assert result.trades[0].exit_reason is FuturesExitReason.LIQUIDATION
    assert result.metrics.liquidation_count == 1
    assert result.metrics.final_wallet >= 0
    assert "INTRABAR_LIQUIDATION_AMBIGUOUS" in result.warnings
    assert result.metrics.liquidation_fees > 0


def test_mark_price_is_not_silently_replaced_by_close(futures_config, start_time) -> None:
    candles = make_candles(start_time, ("100", "101", "102", "103", "104"))
    marks = make_marks(
        candles,
        lows=("99", "100", "0.1", "102", "103"),
        highs=("101", "102", "103", "104", "105"),
    )
    result = run_engine(futures_config, candles, marks)
    assert result.trades[0].exit_reason is FuturesExitReason.LIQUIDATION
    with pytest.raises(ValueError, match="MARK_PRICE_MISSING"):
        run_engine(futures_config, candles, marks[:-2])


def test_forced_end_manual_exit_warmup_and_no_lookahead(
    futures_config,
    start_time,
) -> None:
    candles = make_candles(start_time)
    marks = make_marks(candles)
    forced = run_engine(futures_config, candles, marks)
    assert forced.trades[0].entry_time == candles[2].open_time
    assert forced.warmup_candle_count == 1
    assert forced.evaluated_candle_count == len(candles) - 1
    assert len(forced.equity_curve) >= forced.evaluated_candle_count
    manual = run_engine(
        futures_config,
        candles,
        marks,
        analyzer=EntryExitAnalyzer(PositionSide.LONG),
    )
    assert manual.trades[0].exit_reason is FuturesExitReason.MANUAL_SIMULATED_EXIT


def test_missing_funding_fails_by_default(futures_config, futures_candles, mark_prices) -> None:
    config = replace(
        futures_config,
        funding_enabled=True,
        funding_missing_policy=FundingMissingPolicy.FAIL,
    )
    with pytest.raises(ValueError, match="FUNDING_DATA_MISSING"):
        run_engine(config, futures_candles, mark_prices)
    warned = replace(config, funding_missing_policy=FundingMissingPolicy.WARN_AND_SKIP)
    result = run_engine(warned, futures_candles, mark_prices)
    assert "FUNDING_DATA_MISSING" in result.warnings


def test_spot_proxy_path_is_explicitly_invalid_for_reports(
    futures_config,
    futures_candles,
) -> None:
    proxy = replace(
        futures_config,
        price_source=FuturesPriceSource.SPOT_PROXY_FOR_TESTS_ONLY,
    )
    result = run_engine(proxy, futures_candles, ())
    assert "REPORT_INVALID_PRICE_PROXY" in result.warnings
    assert result.metadata["valid_price_source"] is False
