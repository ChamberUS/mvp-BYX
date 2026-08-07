from dataclasses import replace
from decimal import Decimal

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.futures.engine import FuturesBacktestEngine
from adaptive_trader.futures.real_validation import _liquidation_rows
from tests.futures.conftest import make_candles, make_marks
from tests.futures.test_engine import EntryAnalyzer


def test_liquidation_at_one_x_is_never_hidden(
    futures_config,
    start_time,
) -> None:
    candles = make_candles(
        start_time,
        ("100", "100", "100", "100"),
        lows=("99", "99", "0.1", "99"),
    )
    marks = make_marks(
        candles,
        lows=("99", "99", "0.1", "99"),
    )
    config = replace(
        futures_config,
        leverage=Decimal("1"),
        maximum_leverage=Decimal("1"),
    )
    result = FuturesBacktestEngine(
        config,
        analyzer=EntryAnalyzer(PositionSide.LONG),
    ).run(candles, marks, ())
    rows = _liquidation_rows(
        result,
        variant_id="FUTURES_LONG_BASELINE_1X",
        fold="fixture",
    )
    assert result.metrics.liquidation_count == 1
    assert len(rows) == 1
    assert rows[0]["warning"] == "UNEXPECTED_LIQUIDATION_AT_1X"
    assert rows[0]["wallet_before"] is not None
    assert rows[0]["wallet_after"] is not None
