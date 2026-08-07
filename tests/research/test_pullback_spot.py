from decimal import Decimal

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.domain.models import (
    MarketContext,
    MarketRegime,
    SignalDirection,
)
from adaptive_trader.strategy.pullback import (
    PullbackContinuationAnalyzer,
    PullbackDecisionTrace,
    PullbackEvaluation,
    PullbackReasonCode,
)
from adaptive_trader.strategy.regime import RegimeResult
from tests.research.pullback_helpers import candle, parameters


class UpClassifier:
    def classify(self, candles: object) -> RegimeResult:
        return RegimeResult(MarketRegime.TRENDING_UP, "fixture")


class ApprovedLongCore:
    def evaluate(self, **kwargs: object) -> PullbackEvaluation:
        latest = kwargs["latest"]
        assert hasattr(latest, "open_time")
        return PullbackEvaluation(
            direction=PositionSide.LONG,
            trace=PullbackDecisionTrace(
                timestamp=latest.close_time,
                side=PositionSide.LONG,
                regime=MarketRegime.TRENDING_UP,
                trend_confirmed=True,
                trend_persistence_count=3,
                pullback_detected=True,
                pullback_valid=True,
                pullback_age=1,
                pullback_depth_atr=Decimal("0.2"),
                resumed=True,
                overextended=False,
                ema_distance=Decimal("5"),
                price_to_short_ema=Decimal("1"),
                price_to_long_ema=Decimal("6"),
                atr_relative=Decimal("0.01"),
                volume_ratio=Decimal("1"),
                long_eligible=True,
                short_eligible=False,
                reason_code=PullbackReasonCode.ENTER_LONG_APPROVED,
                close_price=Decimal("106"),
                short_ema=Decimal("105"),
                long_ema=Decimal("100"),
                atr=Decimal("2"),
            ),
        )


def test_spot_pullback_emits_only_a_long_buy_signal() -> None:
    candles = (candle(0, "104"), candle(1, "106"))
    analyzer = PullbackContinuationAnalyzer(
        parameters(),
        classifier=UpClassifier(),
    )
    analyzer._core = ApprovedLongCore()  # type: ignore[assignment]
    context = MarketContext(
        symbol="ETHUSDT",
        interval="1h",
        created_at=candles[-1].close_time,
        candles=candles,
        latest_candle=candles[-1],
        indicators={
            "ema_short": Decimal("105"),
            "ema_long": Decimal("100"),
            "atr": Decimal("2"),
            "volume_ratio": Decimal("1"),
            "suggested_quantity": Decimal("1"),
        },
    )

    signal = analyzer.analyze(context)

    assert signal.direction is SignalDirection.BUY
    assert signal.reason_code == PullbackReasonCode.ENTER_LONG_APPROVED
    assert signal.stop_loss < signal.entry_price < signal.take_profit
    assert signal.analyzer_name == "pullback-continuation-v1"
