from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from adaptive_trader.domain.market import PositionSide
from adaptive_trader.domain.models import MarketRegime
from adaptive_trader.strategy.pullback import (
    PullbackDecisionTrace,
    PullbackReasonCode,
)


def calibration_trace(**updates: object) -> PullbackDecisionTrace:
    value = PullbackDecisionTrace(
        timestamp=datetime(2023, 1, 1, tzinfo=UTC),
        side=PositionSide.LONG,
        regime=MarketRegime.TRENDING_UP,
        trend_confirmed=True,
        trend_persistence_count=3,
        pullback_detected=True,
        pullback_valid=True,
        pullback_age=2,
        pullback_depth_atr=Decimal("0.2"),
        resumed=True,
        overextended=False,
        ema_distance=Decimal("5"),
        price_to_short_ema=Decimal("1"),
        price_to_long_ema=Decimal("6"),
        atr_relative=Decimal("0.02"),
        volume_ratio=Decimal("1.1"),
        long_eligible=False,
        short_eligible=False,
        reason_code=PullbackReasonCode.VOLUME_REJECTED,
        close_price=Decimal("106"),
        short_ema=Decimal("105"),
        long_ema=Decimal("100"),
        atr=Decimal("10"),
        previous_close=Decimal("105"),
        entry_extension_atr=Decimal("0.6"),
        regime_matched=True,
        ema_alignment=True,
        price_long_ema_side=True,
        persistence_valid=True,
        pullback_age_valid=True,
        pullback_depth_min_valid=True,
        pullback_depth_max_valid=True,
        long_ema_not_crossed=True,
        resumption_cross=True,
        directional_close_confirmation=True,
        entry_extension_valid=True,
        volume_valid=False,
        volatility_valid=True,
        signal_created=False,
        all_failure_codes=("VOLUME_REJECTED",),
    )
    return replace(value, **updates)  # type: ignore[arg-type]
