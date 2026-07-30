from dataclasses import replace
from decimal import Decimal

from adaptive_trader.domain.market import TradingMode
from adaptive_trader.futures.models import FuturesSignalDirection
from adaptive_trader.futures.strategy import DeterministicFuturesAnalyzer
from tests.futures.conftest import make_candles


def strategy_config(futures_config, mode):
    return replace(
        futures_config,
        trading_mode=mode,
        short_ema_period=1,
        long_ema_period=8,
        minimum_volume_ratio=Decimal("0.5"),
        maximum_atr_relative=Decimal("0.05"),
    )


def test_deterministic_strategy_has_distinct_long_and_short_signals(
    futures_config,
    start_time,
) -> None:
    rising = make_candles(
        start_time,
        tuple(str(100 + index * 2) for index in range(16)),
    )
    falling = make_candles(
        start_time,
        tuple(str(140 - index * 2) for index in range(16)),
    )
    analyzer = DeterministicFuturesAnalyzer()
    long_signal = analyzer.analyze(
        rising,
        strategy_config(futures_config, TradingMode.FUTURES_LONG_ONLY),
        None,
    )
    short_signal = analyzer.analyze(
        falling,
        strategy_config(futures_config, TradingMode.FUTURES_SHORT_ONLY),
        None,
    )
    assert long_signal.direction is FuturesSignalDirection.ENTER_LONG
    assert short_signal.direction is FuturesSignalDirection.ENTER_SHORT
    assert long_signal.stop_loss < long_signal.entry_price < long_signal.take_profit
    assert short_signal.take_profit < short_signal.entry_price < short_signal.stop_loss
