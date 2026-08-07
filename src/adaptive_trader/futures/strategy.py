"""Deterministic long and mirrored-short Futures research analyzer."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.domain.models import Candle, MarketRegime
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
    def __init__(self) -> None:
        self._indicator_candles: list[Candle] = []
        self._cached_first_open_time: datetime | None = None
        self._cached_last_open_time: datetime | None = None
        self._cached_short_ema: Decimal | None = None
        self._cached_long_ema: Decimal | None = None
        self._cached_periods: tuple[int, int] | None = None
        self._regime_classifier: DeterministicRegimeClassifier | None = None
        self._regime_parameters: tuple[int, int, Decimal] | None = None

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
        indicator_candles = self._materialize_incrementally(candles)
        short_ema, long_ema = self._ema_values(indicator_candles, config)
        indicator_window = tuple(
            indicator_candles[-max(config.atr_period + 1, config.volume_period) :]
        )
        atr_value = atr(indicator_window, config.atr_period)
        try:
            relative_volume = volume_ratio(indicator_window, config.volume_period)
        except ZeroDivisionError:
            return self._hold(latest, "ZERO_AVERAGE_VOLUME")
        regime = self._classifier(config).classify(indicator_candles).regime
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

    def _materialize_incrementally(
        self,
        candles: Sequence[FuturesCandle],
    ) -> list[Candle]:
        sequential = (
            len(self._indicator_candles) == len(candles) - 1
            and bool(self._indicator_candles)
            and self._cached_first_open_time == candles[0].open_time
            and self._cached_last_open_time == candles[-2].open_time
        )
        if sequential:
            self._indicator_candles.append(candles[-1].as_indicator_candle())
        else:
            self._indicator_candles = [
                item.as_indicator_candle() for item in candles
            ]
            self._cached_short_ema = None
            self._cached_long_ema = None
        self._cached_first_open_time = candles[0].open_time
        self._cached_last_open_time = candles[-1].open_time
        return self._indicator_candles

    def _ema_values(
        self,
        candles: Sequence[Candle],
        config: FuturesBacktestConfig,
    ) -> tuple[Decimal, Decimal]:
        periods = (config.short_ema_period, config.long_ema_period)
        if (
            self._cached_short_ema is None
            or self._cached_long_ema is None
            or self._cached_periods != periods
        ):
            materialized = tuple(candles)
            short = candle_ema(materialized, config.short_ema_period)
            long = candle_ema(materialized, config.long_ema_period)
        else:
            short_multiplier = Decimal("2") / Decimal(config.short_ema_period + 1)
            long_multiplier = Decimal("2") / Decimal(config.long_ema_period + 1)
            short = (
                (candles[-1].close - self._cached_short_ema) * short_multiplier
                + self._cached_short_ema
            )
            long = (
                (candles[-1].close - self._cached_long_ema) * long_multiplier
                + self._cached_long_ema
            )
        self._cached_short_ema = short
        self._cached_long_ema = long
        self._cached_periods = periods
        return short, long

    def _classifier(
        self,
        config: FuturesBacktestConfig,
    ) -> DeterministicRegimeClassifier:
        parameters = (
            config.short_ema_period,
            config.long_ema_period,
            config.maximum_atr_relative,
        )
        if self._regime_classifier is None or self._regime_parameters != parameters:
            self._regime_classifier = DeterministicRegimeClassifier(
                short_period=config.short_ema_period,
                long_period=config.long_ema_period,
                maximum_atr_relative=config.maximum_atr_relative,
            )
            self._regime_parameters = parameters
        return self._regime_classifier

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
