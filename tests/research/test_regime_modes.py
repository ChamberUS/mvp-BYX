from datetime import UTC, datetime, timedelta
from decimal import Decimal

import adaptive_trader.strategy.deterministic as deterministic_module
from adaptive_trader.domain.models import (
    Candle,
    MarketContext,
    MarketRegime,
    SignalDirection,
)
from adaptive_trader.strategy.deterministic import DeterministicAnalyzer
from adaptive_trader.strategy.regime import RegimeResult, SpotRegimeMode


def _context() -> MarketContext:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles = tuple(
        Candle(
            symbol="ETHUSDT",
            interval="1h",
            timestamp=start + timedelta(hours=index),
            open=Decimal("99"),
            high=Decimal("103"),
            low=Decimal("98"),
            close=Decimal("99") if index < 3 else Decimal("102"),
            volume=Decimal("10"),
        )
        for index in range(4)
    )
    return MarketContext(
        symbol="ETHUSDT",
        interval="1h",
        created_at=candles[-1].open_time,
        candles=candles,
        latest_candle=candles[-1],
        indicators={
            "ema_short": Decimal("101"),
            "ema_long": Decimal("100"),
            "volume_ratio": Decimal("2"),
            "atr": Decimal("1"),
            "suggested_quantity": Decimal("1"),
        },
    )


def _analyzer(mode: SpotRegimeMode, regime: MarketRegime) -> DeterministicAnalyzer:
    analyzer = DeterministicAnalyzer(
        short_period=2,
        long_period=3,
        regime_mode=mode,
    )
    analyzer._classifier.classify = lambda _: RegimeResult(regime, "test")  # type: ignore[method-assign]
    return analyzer


def test_strict_requires_trending_up() -> None:
    signal = _analyzer(
        SpotRegimeMode.STRICT_TRENDING_UP,
        MarketRegime.RANGING,
    ).analyze(_context())

    assert signal.direction is SignalDirection.HOLD
    assert signal.reason_code == "REGIME_NOT_UP"


def test_up_or_transition_uses_only_point_in_time_history(monkeypatch) -> None:
    monkeypatch.setattr(
        deterministic_module,
        "candle_ema",
        lambda _, period: Decimal("99") if period == 2 else Decimal("100"),
    )
    context = _context()
    signal = _analyzer(
        SpotRegimeMode.UP_OR_TRANSITION,
        MarketRegime.RANGING,
    ).analyze(context)
    future = Candle(
        symbol="ETHUSDT",
        interval="1h",
        timestamp=context.latest_candle.open_time + timedelta(hours=1),
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
    )

    assert signal.direction is SignalDirection.BUY
    assert future not in context.candles


def test_ema_only_and_diagnostic_preserve_other_filters() -> None:
    context = _context()

    ema_signal = _analyzer(
        SpotRegimeMode.EMA_TREND_ONLY,
        MarketRegime.RANGING,
    ).analyze(context)
    diagnostic_signal = _analyzer(
        SpotRegimeMode.NO_REGIME_FILTER_DIAGNOSTIC,
        MarketRegime.TRENDING_DOWN,
    ).analyze(context)

    assert ema_signal.direction is SignalDirection.BUY
    assert diagnostic_signal.direction is SignalDirection.BUY
    assert SpotRegimeMode.NO_REGIME_FILTER_DIAGNOSTIC.diagnostic_only
