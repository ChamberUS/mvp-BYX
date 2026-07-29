from datetime import UTC, datetime, timedelta
from decimal import Decimal

import adaptive_trader.backtest.engine as engine_module
from adaptive_trader.backtest.engine import BacktestEngine
from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.models import (
    Candle,
    MarketContext,
    MarketRegime,
    MarketSignal,
    Position,
    SignalDirection,
)
from adaptive_trader.execution.backtest import BacktestExecutionConfig, BacktestOrderExecutor
from adaptive_trader.risk.manager import DefaultRiskManager


def make_candle(index: int, *, open_price: str, high: str, low: str, close: str) -> Candle:
    return Candle(
        symbol="ETHUSDT",
        interval="1m",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("10"),
    )


def buy_once_analyzer(quantity: Decimal = Decimal("0.5")) -> object:
    class BuyOnceAnalyzer:
        def analyze(self, context: MarketContext) -> MarketSignal:
            if len(context.candles) > 1:
                return MarketSignal(
                    signal_id=f"hold-{len(context.candles)}",
                    symbol=context.symbol,
                    generated_at=context.created_at,
                    direction=SignalDirection.HOLD,
                    regime=MarketRegime.UNKNOWN,
                    confidence=Decimal("0"),
                    entry_price=Decimal("100"),
                    stop_loss=Decimal("0"),
                    take_profit=Decimal("0"),
                    suggested_quantity=Decimal("0"),
                    rationale="done",
                    analyzer_name="regression",
                )
            return MarketSignal(
                signal_id="regression-buy",
                symbol=context.symbol,
                generated_at=context.created_at,
                direction=SignalDirection.BUY,
                regime=MarketRegime.TRENDING_UP,
                confidence=Decimal("1"),
                entry_price=Decimal("100"),
                stop_loss=Decimal("90"),
                take_profit=Decimal("120"),
                suggested_quantity=quantity,
                rationale="regression",
                analyzer_name="regression",
            )

    return BuyOnceAnalyzer()


def make_engine(config: TradingConfig) -> BacktestEngine:
    return BacktestEngine(
        strategy=buy_once_analyzer(Decimal("1")),  # type: ignore[arg-type]
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(
            BacktestExecutionConfig(
                taker_fee_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
                spread_bps=Decimal("0"),
            )
        ),
        config=config,
    )


class SnapshotCollector:
    def __init__(self) -> None:
        self.snapshots = []

    def save_portfolio_snapshot(self, snapshot) -> None:
        self.snapshots.append(snapshot)

    def save_strategy_decision(self, record) -> None:
        pass

    def save_risk_decision(self, decision) -> None:
        pass

    def save_simulated_order(self, order) -> None:
        pass

    def save_fill(self, fill) -> None:
        pass

    def save_position(self, position) -> None:
        pass


def base_config(**updates: object) -> TradingConfig:
    values: dict[str, object] = {
        "short_ema_period": 2,
        "long_ema_period": 3,
        "atr_period": 1,
        "volume_period": 2,
        "warmup_candles": 1,
        "force_close_at_end": True,
    }
    values.update(updates)
    return TradingConfig(**values)  # type: ignore[arg-type]


def test_trailing_stop_activates_after_close_for_next_candle(monkeypatch) -> None:
    monkeypatch.setattr(engine_module, "atr", lambda history, period: Decimal("5"))
    engine = make_engine(
        base_config(trailing_stop_enabled=True, trailing_stop_atr_multiple=Decimal("1"))
    )
    position = Position(
        position_id="position",
        symbol="ETHUSDT",
        quantity=Decimal("1"),
        average_entry_price=Decimal("100"),
        current_price=Decimal("100"),
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
        stop_loss=Decimal("90"),
        take_profit=Decimal("130"),
        initial_risk=Decimal("10"),
    )
    candle = make_candle(1, open_price="100", high="110", low="95", close="110")

    assert engine._exit_trigger(position, candle) == (None, None, False)
    updated = engine._update_position_protection(position, (candle,), candle)

    assert updated.stop_loss == Decimal("105")
    next_candle = make_candle(2, open_price="110", high="111", low="104", close="108")
    assert engine._exit_trigger(updated, next_candle)[:2] == ("STOP_LOSS", Decimal("105"))


def test_break_even_close_does_not_retroactively_use_same_candle_low() -> None:
    engine = make_engine(base_config(break_even_after_r_multiple=Decimal("1")))
    position = Position(
        position_id="position",
        symbol="ETHUSDT",
        quantity=Decimal("1"),
        average_entry_price=Decimal("100"),
        current_price=Decimal("100"),
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
        stop_loss=Decimal("90"),
        take_profit=Decimal("130"),
        initial_risk=Decimal("10"),
    )
    candle = make_candle(1, open_price="100", high="110", low="95", close="110")

    assert engine._exit_trigger(position, candle)[0] is None
    updated = engine._update_position_protection(position, (candle,), candle)
    assert updated.stop_loss == Decimal("100")

    next_candle = make_candle(2, open_price="110", high="111", low="99", close="105")
    assert engine._exit_trigger(updated, next_candle)[:2] == ("STOP_LOSS", Decimal("100"))


def test_trailing_stop_never_decreases(monkeypatch) -> None:
    monkeypatch.setattr(engine_module, "atr", lambda history, period: Decimal("5"))
    engine = make_engine(
        base_config(trailing_stop_enabled=True, trailing_stop_atr_multiple=Decimal("1"))
    )
    position = Position(
        position_id="position",
        symbol="ETHUSDT",
        quantity=Decimal("1"),
        average_entry_price=Decimal("100"),
        current_price=Decimal("100"),
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
        stop_loss=Decimal("105"),
        take_profit=Decimal("130"),
        initial_risk=Decimal("10"),
    )
    candle = make_candle(1, open_price="100", high="106", low="99", close="106")

    updated = engine._update_position_protection(position, (candle,), candle)
    assert updated.stop_loss == Decimal("105")


def test_stop_first_wins_when_old_stop_and_take_are_both_hit() -> None:
    engine = make_engine(base_config())
    position = Position(
        position_id="position",
        symbol="ETHUSDT",
        quantity=Decimal("1"),
        average_entry_price=Decimal("100"),
        current_price=Decimal("100"),
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
        stop_loss=Decimal("90"),
        take_profit=Decimal("110"),
        initial_risk=Decimal("10"),
    )
    candle = make_candle(1, open_price="100", high="111", low="89", close="100")

    assert engine._exit_trigger(position, candle) == ("STOP_LOSS", Decimal("90"), True)


def test_holding_candles_use_exit_index_minus_entry_index() -> None:
    config = base_config()
    candles = (
        make_candle(0, open_price="100", high="100", low="100", close="100"),
        make_candle(1, open_price="100", high="101", low="99.5", close="100"),
        make_candle(2, open_price="100", high="121", low="99.5", close="101"),
        make_candle(3, open_price="101", high="103", low="100", close="102"),
    )

    result = make_engine(config).run(candles)

    assert result.trades[0].holding_candles == 1


def test_daily_state_resets_at_utc_date_boundary() -> None:
    collector = SnapshotCollector()
    config = base_config()
    engine = BacktestEngine(
        strategy=buy_once_analyzer(),  # type: ignore[arg-type]
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(
            BacktestExecutionConfig(
                taker_fee_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
                spread_bps=Decimal("0"),
            )
        ),
        config=config,
        repository=collector,  # type: ignore[arg-type]
    )
    candles = (
        make_candle(0, open_price="100", high="100", low="100", close="100").__class__(
            symbol="ETHUSDT",
            interval="1m",
            timestamp=datetime(2026, 1, 1, 23, 58, tzinfo=UTC),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("10"),
        ),
        make_candle(1, open_price="100", high="101", low="99.5", close="100").__class__(
            symbol="ETHUSDT",
            interval="1m",
            timestamp=datetime(2026, 1, 1, 23, 59, tzinfo=UTC),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99.5"),
            close=Decimal("100"),
            volume=Decimal("10"),
        ),
        make_candle(2, open_price="100", high="101", low="99.5", close="100").__class__(
            symbol="ETHUSDT",
            interval="1m",
            timestamp=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99.5"),
            close=Decimal("100"),
            volume=Decimal("10"),
        ),
    )

    engine.run(candles)

    second_day = collector.snapshots[-1]
    assert second_day.day_start_equity == Decimal("10000")
    assert second_day.entries_today == 0
    assert second_day.daily_loss == Decimal("0")


def test_valid_purchase_keeps_cash_and_equity_consistent() -> None:
    collector = SnapshotCollector()
    config = base_config(initial_balance=Decimal("100"), maximum_position_percent=Decimal("100"))
    engine = BacktestEngine(
        strategy=buy_once_analyzer(),  # type: ignore[arg-type]
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(
            BacktestExecutionConfig(
                taker_fee_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
                spread_bps=Decimal("0"),
            )
        ),
        config=config,
        repository=collector,  # type: ignore[arg-type]
    )
    candles = (
        make_candle(0, open_price="100", high="100", low="100", close="100"),
        make_candle(1, open_price="100", high="101", low="99.5", close="100"),
    )

    engine.run(candles)

    snapshot = collector.snapshots[-1]
    assert snapshot.cash_balance == Decimal("50.0")
    assert snapshot.equity == Decimal("100.0")
    assert snapshot.cash_balance >= 0


def test_latency_is_applied_at_future_candle_open() -> None:
    candles = tuple(
        make_candle(index, open_price="100", high="101", low="99.5", close="100")
        for index in range(4)
    )
    config = base_config(latency_candles=2)

    result = make_engine(config).run(candles)

    assert result.metrics.entry_count == 1
    assert result.trades[0].entry_time == candles[2].open_time


def test_effective_cost_rejects_purchase_that_fits_nominal_cash() -> None:
    config = base_config(
        initial_balance=Decimal("100"),
        maximum_position_percent=Decimal("100"),
    )
    engine = BacktestEngine(
        strategy=buy_once_analyzer(Decimal("1")),  # type: ignore[arg-type]
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(
            BacktestExecutionConfig(
                taker_fee_bps=Decimal("100"),
                slippage_bps=Decimal("100"),
                spread_bps=Decimal("100"),
            )
        ),
        config=config,
    )
    candles = (
        make_candle(0, open_price="100", high="100", low="100", close="100"),
        make_candle(1, open_price="100", high="100", low="100", close="100"),
    )

    result = engine.run(candles)

    assert result.metrics.entry_count == 0
    assert any("effective cost exceeds cash" in warning for warning in result.warnings)


def test_gap_does_not_create_negative_cash() -> None:
    config = base_config(initial_balance=Decimal("100"), maximum_position_percent=Decimal("100"))
    candles = (
        make_candle(0, open_price="100", high="100", low="100", close="100"),
        make_candle(1, open_price="200", high="200", low="200", close="200"),
    )

    result = BacktestEngine(
        strategy=buy_once_analyzer(Decimal("1")),  # type: ignore[arg-type]
        risk_manager=DefaultRiskManager(local_simulation=True),
        executor=BacktestOrderExecutor(
            BacktestExecutionConfig(
                taker_fee_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
                spread_bps=Decimal("0"),
            )
        ),
        config=config,
    ).run(candles)

    assert result.metrics.entry_count == 0
    assert result.metrics.final_capital == Decimal("100")
