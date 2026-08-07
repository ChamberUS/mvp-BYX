from dataclasses import replace
from decimal import Decimal

from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.futures.models import FuturesSignalDirection
from adaptive_trader.futures.pullback import PullbackContinuationFuturesAnalyzer
from tests.futures.conftest import make_candles
from tests.futures.pullback_helpers import ApprovedCore
from tests.research.pullback_helpers import parameters


def test_futures_pullback_short_is_mirrored_not_a_spot_sell(
    futures_config,
    start_time,
) -> None:
    closes = tuple(str(110 - index) for index in range(10))
    candles = make_candles(start_time, closes)
    analyzer = PullbackContinuationFuturesAnalyzer(parameters())
    core = ApprovedCore(PositionSide.SHORT)
    analyzer._core = core  # type: ignore[assignment]
    config = replace(
        futures_config,
        trading_mode=TradingMode.FUTURES_SHORT_ONLY,
        minimum_volume_ratio=Decimal("0"),
    )

    signal = analyzer.analyze(candles, config, None)

    assert core.allow_long is False
    assert core.allow_short is True
    assert signal.direction is FuturesSignalDirection.ENTER_SHORT
    assert signal.take_profit < signal.entry_price < signal.stop_loss
