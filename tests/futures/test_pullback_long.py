from dataclasses import replace
from decimal import Decimal

from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.futures.models import FuturesSignalDirection
from adaptive_trader.futures.pullback import PullbackContinuationFuturesAnalyzer
from tests.futures.conftest import make_candles
from tests.futures.pullback_helpers import ApprovedCore
from tests.research.pullback_helpers import parameters


def test_futures_pullback_long_has_stop_below_and_target_above(
    futures_config,
    start_time,
) -> None:
    closes = tuple(str(100 + index) for index in range(10))
    candles = make_candles(start_time, closes)
    analyzer = PullbackContinuationFuturesAnalyzer(parameters())
    core = ApprovedCore(PositionSide.LONG)
    analyzer._core = core  # type: ignore[assignment]
    config = replace(
        futures_config,
        trading_mode=TradingMode.FUTURES_LONG_ONLY,
        minimum_volume_ratio=Decimal("0"),
    )

    signal = analyzer.analyze(candles, config, None)

    assert core.allow_long is True
    assert core.allow_short is False
    assert signal.direction is FuturesSignalDirection.ENTER_LONG
    assert signal.stop_loss < signal.entry_price < signal.take_profit
