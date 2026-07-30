"""Deterministic long and mirrored-short Futures research analyzer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.domain.models import MarketRegime
from adaptive_trader.futures.models import (
    FuturesBacktestConfig,
    FuturesCandle,
    FuturesSignal,
    FuturesSignalDirection,
)
from adaptive_trader.indicators import atr, candle_ema, volume_ratio
from adaptive_trader.strategy.regime import DeterministicRegimeClassifier


class FuturesMarketAnalyzer(Protocol):
    def analyze(
        self,
        candles: Sequence[FuturesCandle],
        config: FuturesBacktestConfig,
        position_side: PositionSide | None,
    ) -> FuturesSignal: ...


class DeterministicFuturesAnalyzer:
    def analyze(
        self,
        candles: Sequence[FuturesCandle],
        config: FuturesBacktestConfig,
        position_side: PositionSide | None,
    ) -> FuturesSignal:
        latest = candles[-1]
        if position_side is not None:
            return self._hold(latest, "POSITION_MANAGED_BY_ENGINE")
        required = max(
            config.long_ema_period + 5,
            config.atr_period,
            config.volume_period,
            10,
        )
        if len(candles) < required:
            return self._hold(latest, "INSUFFICIENT_DATA")
        indicator_candles = tuple(item.as_indicator_candle() for item in candles)
        short_ema = candle_ema(indicator_candles, config.short_ema_period)
        long_ema = candle_ema(indicator_candles, config.long_ema_period)
        atr_value = atr(indicator_candles, config.atr_period)
        try:
            relative_volume = volume_ratio(indicator_candles, config.volume_period)
        except ZeroDivisionError:
            return self._hold(latest, "ZERO_AVERAGE_VOLUME")
        regime = DeterministicRegimeClassifier(
            short_period=config.short_ema_period,
            long_period=config.long_ema_period,
            maximum_atr_relative=config.maximum_atr_relative,
        ).classify(indicator_candles).regime
        if relative_volume < config.minimum_volume_ratio:
            return self._hold(latest, "VOLUME_TOO_LOW", regime)
        if atr_value / latest.close > config.maximum_atr_relative:
            return self._hold(latest, "VOLATILITY_TOO_HIGH", regime)
        risk = atr_value * config.stop_atr_multiple
        if (
            config.trading_mode
            in {TradingMode.FUTURES_LONG_ONLY, TradingMode.FUTURES_LONG_SHORT}
            and regime is MarketRegime.TRENDING_UP
            and short_ema > long_ema
            and latest.close > risk
        ):
            return FuturesSignal(
                signal_id=f"{latest.symbol}-{latest.open_time.isoformat()}-ENTER_LONG",
                symbol=latest.symbol,
                generated_at=latest.close_time,
                direction=FuturesSignalDirection.ENTER_LONG,
                regime=regime,
                entry_price=latest.close,
                stop_loss=latest.close - risk,
                take_profit=latest.close + risk * config.target_r_multiple,
                rationale="deterministic bullish EMA, regime, volume and ATR confirmation",
                reason_code="LONG_APPROVED",
            )
        if (
            config.trading_mode
            in {TradingMode.FUTURES_SHORT_ONLY, TradingMode.FUTURES_LONG_SHORT}
            and regime is MarketRegime.TRENDING_DOWN
            and short_ema < long_ema
        ):
            return FuturesSignal(
                signal_id=f"{latest.symbol}-{latest.open_time.isoformat()}-ENTER_SHORT",
                symbol=latest.symbol,
                generated_at=latest.close_time,
                direction=FuturesSignalDirection.ENTER_SHORT,
                regime=regime,
                entry_price=latest.close,
                stop_loss=latest.close + risk,
                take_profit=latest.close - risk * config.target_r_multiple,
                rationale="mirrored bearish EMA, regime, volume and ATR confirmation",
                reason_code="SHORT_APPROVED",
            )
        return self._hold(latest, "TREND_NOT_CONFIRMED", regime)

    @staticmethod
    def _hold(
        candle: FuturesCandle,
        reason_code: str,
        regime: MarketRegime = MarketRegime.UNKNOWN,
    ) -> FuturesSignal:
        return FuturesSignal(
            signal_id=f"{candle.symbol}-{candle.open_time.isoformat()}-HOLD",
            symbol=candle.symbol,
            generated_at=candle.close_time,
            direction=FuturesSignalDirection.HOLD,
            regime=regime,
            entry_price=candle.close,
            stop_loss=None,
            take_profit=None,
            rationale=reason_code,
            reason_code=reason_code,
        )
