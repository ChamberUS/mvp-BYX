from decimal import Decimal

from adaptive_trader.backtest.metrics import calculate_metrics


def test_metrics_handle_empty_trades() -> None:
    metrics = calculate_metrics(
        initial_capital=Decimal("1000"),
        final_capital=Decimal("1000"),
        trades=(),
        equity_curve=(Decimal("1000"), Decimal("990"), Decimal("1010")),
        exposure_curve=(Decimal("0"), Decimal("10")),
        start_price=Decimal("100"),
        end_price=Decimal("110"),
    )

    assert metrics.total_trades == 0
    assert metrics.win_rate is None
    assert metrics.profit_factor is None
    assert metrics.maximum_drawdown_value == Decimal("10")
    assert metrics.buy_and_hold_return == Decimal("10")
