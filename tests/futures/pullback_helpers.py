from __future__ import annotations

from decimal import Decimal

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.domain.models import MarketRegime
from adaptive_trader.strategy.pullback import (
    PullbackDecisionTrace,
    PullbackEvaluation,
    PullbackReasonCode,
)


class ApprovedCore:
    def __init__(self, side: PositionSide) -> None:
        self.side = side
        self.allow_long = False
        self.allow_short = False

    def evaluate(self, **kwargs: object) -> PullbackEvaluation:
        self.allow_long = bool(kwargs["allow_long"])
        self.allow_short = bool(kwargs["allow_short"])
        latest = kwargs["latest"]
        reason = (
            PullbackReasonCode.ENTER_LONG_APPROVED
            if self.side is PositionSide.LONG
            else PullbackReasonCode.ENTER_SHORT_APPROVED
        )
        return PullbackEvaluation(
            direction=self.side,
            trace=PullbackDecisionTrace(
                timestamp=latest.close_time,
                side=self.side,
                regime=(
                    MarketRegime.TRENDING_UP
                    if self.side is PositionSide.LONG
                    else MarketRegime.TRENDING_DOWN
                ),
                trend_confirmed=True,
                trend_persistence_count=3,
                pullback_detected=True,
                pullback_valid=True,
                pullback_age=1,
                pullback_depth_atr=Decimal("0.2"),
                resumed=True,
                overextended=False,
                ema_distance=Decimal("1"),
                price_to_short_ema=Decimal("0.1"),
                price_to_long_ema=Decimal("1"),
                atr_relative=Decimal("0.01"),
                volume_ratio=Decimal("1"),
                long_eligible=self.side is PositionSide.LONG,
                short_eligible=self.side is PositionSide.SHORT,
                reason_code=reason,
                close_price=latest.close,
                short_ema=latest.close,
                long_ema=latest.close,
                atr=Decimal("1"),
            ),
        )
